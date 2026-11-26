"""Build the schema catalog from the files the platform already ships.

Sources, and only these sources:

- migrations/*.sql plus the Episode dataclass
- openapi/midplane-v0.yaml
- MidPlaneState and the node I/O TypedDicts
- the PaperOnlyTradingControlClient source
- the training_record return value in midplane/export.py
- SchemaAgentResultFields

RECONCILE flags are the artifact names the same files already mark.
"""

from __future__ import annotations

import ast
import re
from dataclasses import fields
from functools import lru_cache
from pathlib import Path
from types import UnionType
from typing import Annotated, Literal, Union, get_args, get_origin, get_type_hints

import yaml

from midplane.agents.types import SchemaAgentResultFields
from midplane.schema_registry.models import SchemaEntry, SchemaField
from midplane.state import NODE_CONTRACTS, MidPlaneState
from midplane.store import Episode

_RECONCILE_FILE_RE = re.compile(r"\b([A-Za-z0-9_-]+\.(?:sql|json|md|py|xlsx|yaml|yml))\b")
_CREATE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?P<name>\w+)\s*\((?P<body>.*)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_ALTER_RE = re.compile(
    r"alter\s+table\s+(?P<table>\w+)\s+add\s+column\s+(?:if\s+not\s+exists\s+)?(?P<name>\w+)\s+(?P<rest>.+?);",
    re.IGNORECASE | re.DOTALL,
)
_SQL_KEYWORDS = {"check", "primary", "unique", "constraint", "foreign"}
_METHODS = {"get", "post", "put", "patch", "delete"}
_SSE_PAYLOADS = {
    "run.started": "RunStartedData",
    "node.started": "NodeStartedData",
    "node.completed": "NodeCompletedData",
    "run.completed": "RunCompletedData",
    "run.failed": "RunFailedData",
}
_PYTHON_SQL = {
    str: "text",
    int: "integer",
    float: "double precision",
    dict: "jsonb",
    list: "jsonb",
}
_STOP = {
    "the",
    "and",
    "for",
    "with",
    "how",
    "does",
    "what",
    "which",
    "this",
    "that",
    "into",
    "from",
    "are",
    "can",
    "use",
    "its",
    "not",
}


def repo_root() -> Path:
    start = Path(__file__).resolve()
    for parent in start.parents:
        if (parent / "openapi" / "midplane-v0.yaml").is_file():
            return parent
    cwd = Path.cwd()
    if (cwd / "openapi" / "midplane-v0.yaml").is_file():
        return cwd
    raise FileNotFoundError("openapi/midplane-v0.yaml")


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def reconcile_flags(*paths: Path) -> tuple[str, ...]:
    found: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "RECONCILE" not in line:
                continue
            for name in _RECONCILE_FILE_RE.findall(line):
                if name not in found:
                    found.append(name)
    return tuple(sorted(found))


def _split_sql_list(body: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for char in body:
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            buf.append(char)
            continue
        if char == "(":
            depth += 1
            buf.append(char)
        elif char == ")":
            depth -= 1
            buf.append(char)
        elif char == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(char)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _sql_type_and_nullable(rest: str) -> tuple[str, bool]:
    lowered = rest.lower()
    nullable = "not null" not in lowered and "primary key" not in lowered
    cut = re.split(
        r"\b(not\s+null|primary\s+key|default|check|unique|references)\b",
        rest,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return " ".join(cut.split()).lower(), nullable


def _episode_columns(sql_paths: list[Path]) -> list[tuple[str, str, bool, str]]:
    columns: list[tuple[str, str, bool, str]] = []
    seen: set[str] = set()
    for path in sql_paths:
        text = path.read_text(encoding="utf-8")
        for match in _CREATE_RE.finditer(text):
            if match.group("name").lower() != "episodes":
                continue
            for part in _split_sql_list(match.group("body")):
                name = part.split(None, 1)[0].lower()
                if name in _SQL_KEYWORDS or name in seen:
                    continue
                rest = part.split(None, 1)[1] if len(part.split(None, 1)) == 2 else ""
                sql_type, nullable = _sql_type_and_nullable(rest)
                columns.append((name, sql_type, nullable, path.as_posix()))
                seen.add(name)
        for match in _ALTER_RE.finditer(text):
            if match.group("table").lower() != "episodes":
                continue
            name = match.group("name").lower()
            if name in seen:
                continue
            sql_type, nullable = _sql_type_and_nullable(match.group("rest"))
            columns.append((name, sql_type, nullable, path.as_posix()))
            seen.add(name)
    return columns


def _openapi_type(prop: dict) -> tuple[str, bool]:
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1], False
    if "anyOf" in prop and isinstance(prop["anyOf"], list):
        options = [option for option in prop["anyOf"] if isinstance(option, dict)]
        nulls = [option for option in options if option.get("type") == "null"]
        others = [option for option in options if option not in nulls]
        if len(others) == 1:
            name, nullable = _openapi_type(others[0])
            return name, nullable or bool(nulls)
    typ = prop.get("type", "object")
    nullable = False
    if isinstance(typ, list):
        nullable = "null" in typ
        rest = [item for item in typ if item != "null"]
        base = str(rest[0]) if rest else "object"
    else:
        base = str(typ)
    if "const" in prop and base == "string":
        return f"enum[{prop['const']}]", nullable
    if "enum" in prop and base == "string":
        values = ",".join(str(item) for item in prop["enum"])
        return f"enum[{values}]", nullable
    return base, nullable


def _fields_from_object(schema: dict, source: str) -> tuple[SchemaField, ...]:
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fields_out: list[SchemaField] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        type_name, nullable = _openapi_type(prop)
        description = str(prop.get("description") or "")
        fields_out.append(
            SchemaField(
                name=str(name),
                type=type_name,
                nullable=nullable,
                required=str(name) in required,
                source_file=source,
                description=description,
            )
        )
    return tuple(fields_out)


def _python_type(hint: object) -> tuple[str, bool]:
    origin = get_origin(hint)
    if origin is Annotated:
        return _python_type(get_args(hint)[0])
    args = get_args(hint)
    if origin in {Union, UnionType}:
        non_none = [arg for arg in args if arg is not type(None)]
        nullable = len(non_none) != len(args)
        if len(non_none) == 1:
            name, inner = _python_type(non_none[0])
            return name, nullable or inner
        return "|".join(_python_type(arg)[0] for arg in non_none), nullable
    if origin is Literal:
        values = ",".join(str(arg) for arg in args)
        return f"enum[{values}]", False
    if origin is list:
        return "array", False
    if origin is dict:
        return "object", False
    if hint is str:
        return "string", False
    if hint is int:
        return "integer", False
    if hint is float:
        return "number", False
    if hint is bool:
        return "boolean", False
    if hint is dict:
        return "object", False
    if hint is list:
        return "array", False
    name = getattr(hint, "__name__", None)
    return (name or "object"), False


def _fields_from_typed(cls: type, source: str) -> tuple[SchemaField, ...]:
    hints = get_type_hints(cls, include_extras=True)
    parsed: list[SchemaField] = []
    for name, hint in hints.items():
        type_name, nullable = _python_type(hint)
        parsed.append(
            SchemaField(
                name=name,
                type=type_name,
                nullable=nullable,
                required=True,
                source_file=source,
            )
        )
    return tuple(parsed)


def _expr_type(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return {
            "run_id": "string",
            "source": "string",
            "created_at": "string",
            "quality_score": "number",
            "score_version": "string",
            "outcome": "string",
        }.get(node.attr, "object")
    if isinstance(node, ast.Name):
        if node.id in {"prompt", "assistant"}:
            return "string"
        return "object"
    if isinstance(node, ast.List):
        return "array"
    if isinstance(node, ast.Dict):
        return "object"
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return "string"
        if isinstance(node.value, bool):
            return "boolean"
        if isinstance(node.value, (int, float)):
            return "number"
    return "object"


def _dict_fields(node: ast.Dict, source: str, nullable_names: set[str]) -> tuple[SchemaField, ...]:
    parsed: list[SchemaField] = []
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        parsed.append(
            SchemaField(
                name=key.value,
                type=_expr_type(value),
                nullable=key.value in nullable_names,
                required=True,
                source_file=source,
            )
        )
    return tuple(parsed)


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise ValueError(f"function {name} was not found")


def _class_method(tree: ast.AST, class_name: str, method: str) -> ast.FunctionDef:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return _function(node, method)
    raise ValueError(f"class {class_name} was not found")


def _return_dict(fn: ast.FunctionDef) -> ast.Dict:
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            return node.value
    raise ValueError(f"{fn.name} does not return a dict literal")


class SchemaCatalog:
    def __init__(self, entries: tuple[SchemaEntry, ...]) -> None:
        ids = [entry.id for entry in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("schema registry has duplicate ids")
        self.entries = entries
        self._by_id = {entry.id: entry for entry in entries}

    def lookup(self, schema_id: str) -> SchemaEntry | None:
        return self._by_id.get(schema_id)

    def search(self, query: str, limit: int = 8) -> tuple[SchemaEntry, ...]:
        tokens = [
            token
            for token in re.split(r"[^a-z0-9]+", query.lower())
            if len(token) >= 3 and token not in _STOP
        ]
        scored: list[tuple[int, str, SchemaEntry]] = []
        for entry in self.entries:
            blob = " ".join(
                [
                    entry.id,
                    entry.domain,
                    entry.description,
                    entry.source_file,
                    *[field.name for field in entry.fields],
                    *[field.type for field in entry.fields],
                ]
            ).lower()
            score = sum(1 for token in tokens if token in blob)
            if entry.domain in tokens or entry.id.lower() in query.lower():
                score += 2
            if score:
                scored.append((score, entry.id, entry))
        scored.sort(key=lambda item: (-item[0], item[1]))
        if not scored and not tokens:
            return self.entries[:limit]
        return tuple(item[2] for item in scored[:limit])


def _episode_entry(root: Path) -> SchemaEntry:
    migration_dir = root / "migrations"
    sql_paths = sorted(migration_dir.glob("*.sql"))
    model_path = root / "midplane" / "store.py"
    ddl_path = root / "midplane" / "ddl.py"
    flag_paths = [
        migration_dir / "001_episodes.sql",
        migration_dir / "003_episode_indexes.sql",
        model_path,
        ddl_path,
    ]
    columns = _episode_columns(sql_paths)
    by_name = {name: (sql_type, nullable, source) for name, sql_type, nullable, source in columns}
    model_fields = {field.name: field.type for field in fields(Episode)}
    flags = list(reconcile_flags(*[path for path in flag_paths if path.is_file()]))
    if set(by_name) != set(model_fields):
        if "episode-model-drift" not in flags:
            flags.append("episode-model-drift")
    for name, annotation in model_fields.items():
        if name not in by_name:
            continue
        expected = _PYTHON_SQL.get(annotation)
        actual = by_name[name][0]
        if expected and expected != actual and "episode-type-drift" not in flags:
            flags.append("episode-type-drift")
    ordered: list[SchemaField] = []
    for name, sql_type, nullable, source in columns:
        ordered.append(
            SchemaField(
                name=name,
                type=sql_type,
                nullable=nullable,
                required=not nullable,
                source_file=_rel(Path(source), root),
            )
        )
    for name in model_fields:
        if name in by_name:
            continue
        annotation = model_fields[name]
        ordered.append(
            SchemaField(
                name=name,
                type=_PYTHON_SQL.get(annotation, "object"),
                nullable=False,
                required=True,
                source_file=_rel(model_path, root),
            )
        )
    source_files = tuple(
        dict.fromkeys((*(_rel(Path(source), root) for _, _, _, source in columns), _rel(model_path, root)))
    )
    return SchemaEntry(
        id="episode",
        domain="episode",
        version="v0",
        fields=tuple(ordered),
        source_file="migrations/001_episodes.sql",
        source_files=source_files,
        reconcile=tuple(sorted(flags)),
        description="Episode row built from migrations/*.sql and the Episode model.",
    )


def _gateway_entries(root: Path) -> list[SchemaEntry]:
    path = root / "openapi" / "midplane-v0.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    source = _rel(path, root)
    version = str((document.get("info") or {}).get("version") or "0.1.0")
    flags = reconcile_flags(path)
    components = (document.get("components") or {}).get("schemas") or {}
    entries: list[SchemaEntry] = []
    for name, schema in components.items():
        if not isinstance(schema, dict):
            continue
        entries.append(
            SchemaEntry(
                id=f"gateway.component.{name}",
                domain="gateway",
                version=version,
                fields=_fields_from_object(schema, source),
                source_file=source,
                reconcile=flags,
                description=str(schema.get("description") or f"OpenAPI component {name}."),
            )
        )
    error = components.get("Error")
    if isinstance(error, dict):
        inner = (error.get("properties") or {}).get("error") or {}
        entries.append(
            SchemaEntry(
                id="gateway.error",
                domain="gateway",
                version=version,
                fields=_fields_from_object(inner, source),
                source_file=source,
                reconcile=flags,
                description="Error envelope. Every 4xx and 5xx body uses this object.",
            )
        )
    event_enum: list[str] = []
    run_event = components.get("RunEvent") or {}
    event_prop = ((run_event.get("properties") or {}).get("event") or {})
    if isinstance(event_prop.get("enum"), list):
        event_enum = [str(item) for item in event_prop["enum"]]
    if event_enum and set(event_enum) != set(_SSE_PAYLOADS):
        raise ValueError("OpenAPI SSE event names drifted from the schema registry map")
    for event_name, component_name in _SSE_PAYLOADS.items():
        payload = components.get(component_name) or {}
        entries.append(
            SchemaEntry(
                id=f"gateway.sse.{event_name}",
                domain="gateway",
                version=version,
                fields=_fields_from_object(payload, source),
                source_file=source,
                reconcile=flags,
                description=f"SSE event {event_name}. Payload component {component_name}.",
            )
        )
    for route, path_item in (document.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in _METHODS or not isinstance(operation, dict):
                continue
            fields_out: list[SchemaField] = []
            for param in operation.get("parameters") or []:
                if not isinstance(param, dict):
                    continue
                type_name, nullable = _openapi_type(param.get("schema") or {})
                fields_out.append(
                    SchemaField(
                        name=str(param.get("name")),
                        type=type_name,
                        nullable=nullable,
                        required=bool(param.get("required")),
                        source_file=source,
                        description=str(param.get("description") or param.get("in") or ""),
                    )
                )
            body = operation.get("requestBody") or {}
            content = ((body.get("content") or {}).get("application/json") or {}).get("schema") or {}
            if isinstance(content, dict) and content:
                type_name, nullable = _openapi_type(content)
                fields_out.append(
                    SchemaField(
                        name="body",
                        type=type_name,
                        nullable=nullable,
                        required=bool(body.get("required", True)),
                        source_file=source,
                    )
                )
            entries.append(
                SchemaEntry(
                    id=f"gateway.endpoint.{method.upper()} {route}",
                    domain="gateway",
                    version=version,
                    fields=tuple(fields_out),
                    source_file=source,
                    reconcile=flags,
                    description=str(operation.get("summary") or operation.get("description") or f"{method.upper()} {route}"),
                )
            )
    return entries


def _state_entries(root: Path) -> list[SchemaEntry]:
    state_path = root / "midplane" / "state.py"
    graph_path = root / "midplane" / "graph.py"
    source = _rel(state_path, root)
    flags = reconcile_flags(state_path, graph_path)
    entries = [
        SchemaEntry(
            id="midplane.state",
            domain="midplane",
            version="v0",
            fields=_fields_from_typed(MidPlaneState, source),
            source_file=source,
            reconcile=flags,
            description="Shared LangGraph state for one mid-plane run.",
        )
    ]
    for node, contracts in NODE_CONTRACTS.items():
        for direction, typed in contracts.items():
            entries.append(
                SchemaEntry(
                    id=f"midplane.node.{node}.{direction}",
                    domain="midplane",
                    version="v0",
                    fields=_fields_from_typed(typed, source),
                    source_file=source,
                    source_files=(source, _rel(graph_path, root)),
                    reconcile=flags,
                    description=f"{node} {direction} contract.",
                )
            )
    return entries


def _trading_entries(root: Path) -> list[SchemaEntry]:
    path = root / "midplane" / "trading.py"
    source = _rel(path, root)
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    submit = _class_method(tree, "PaperOnlyTradingControlClient", "submit")
    action_names: list[str] = []
    for node in ast.walk(submit):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                action_names.append(node.args[0].value)
    if not action_names:
        raise ValueError("PaperOnlyTradingControlClient.submit does not read action fields")
    action_fields = tuple(
        SchemaField(name=name, type="string", nullable=False, required=True, source_file=source)
        for name in action_names
    )
    result_fields = _dict_fields(_return_dict(submit), source, set())
    flags = reconcile_flags(path)
    description = "Paper-only trading control client. real_money is false. No broker call."
    return [
        SchemaEntry(
            id="trading.control.action",
            domain="trading",
            version="v0",
            fields=action_fields,
            source_file=source,
            reconcile=flags,
            description=description,
        ),
        SchemaEntry(
            id="trading.control.result",
            domain="trading",
            version="v0",
            fields=result_fields,
            source_file=source,
            reconcile=flags,
            description=description,
        ),
    ]


def _export_entries(root: Path) -> list[SchemaEntry]:
    path = root / "midplane" / "export.py"
    source = _rel(path, root)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = _function(tree, "training_record")
    nullable: set[str] = set()
    for stmt in fn.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name) and target.id == "model_choice":
                nullable.add("model_choice")
    returned = _return_dict(fn)
    fields_out = list(_dict_fields(returned, source, nullable))
    message_fields: tuple[SchemaField, ...] = ()
    for key, value in zip(returned.keys, returned.values):
        if isinstance(key, ast.Constant) and key.value == "messages" and isinstance(value, ast.List):
            for item in value.elts:
                if isinstance(item, ast.Dict):
                    message_fields = _dict_fields(item, source, set())
                    break
    flags = reconcile_flags(path)
    entries = [
        SchemaEntry(
            id="export.jsonl",
            domain="export",
            version="v0",
            fields=tuple(fields_out),
            source_file=source,
            reconcile=flags,
            description="One JSONL training record produced by midplane.export.",
        )
    ]
    if message_fields:
        entries.append(
            SchemaEntry(
                id="export.jsonl.message",
                domain="export",
                version="v0",
                fields=message_fields,
                source_file=source,
                reconcile=flags,
                description="One message inside an export JSONL record.",
            )
        )
    return entries


def _result_entry(root: Path) -> SchemaEntry:
    types_path = root / "midplane" / "agents" / "types.py"
    agent_path = root / "midplane" / "agents" / "schema_agent.py"
    prompt_path = root / "midplane" / "prompts" / "schema_agent.md"
    sources = [types_path]
    for extra in (agent_path, prompt_path):
        if extra.is_file():
            sources.append(extra)
    return SchemaEntry(
        id="schema_agent.result",
        domain="schema_agent",
        version="v0",
        fields=_fields_from_typed(SchemaAgentResultFields, _rel(types_path, root)),
        source_file=_rel(types_path, root),
        source_files=tuple(_rel(path, root) for path in sources),
        reconcile=reconcile_flags(*sources),
        description="Typed output of one schema-agent run.",
    )


def build_schema_registry(root: Path | None = None) -> SchemaCatalog:
    base = root or repo_root()
    entries: list[SchemaEntry] = [_episode_entry(base)]
    entries.extend(_gateway_entries(base))
    entries.extend(_state_entries(base))
    entries.extend(_trading_entries(base))
    entries.extend(_export_entries(base))
    entries.append(_result_entry(base))
    return SchemaCatalog(tuple(entries))


@lru_cache(maxsize=1)
def load_schema_registry() -> SchemaCatalog:
    return build_schema_registry()
