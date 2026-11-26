"""Schema and connector documents.

``SchemaDocumentLoader`` is the plug a later schema agent can implement.
``midplane/schema_registry/`` is a separate change and is not imported here.
``BuiltinSchemaLoader`` reads the checked-in OpenAPI file, the SQL migrations,
and ``connectors.yaml``.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Protocol

import yaml

from midplane.retrieval.embedder import Embedder, HashedNgramEmbedder
from midplane.retrieval.index import VectorIndex, open_index
from midplane.retrieval.rerank import LexicalReranker
from midplane.retrieval.retriever import Retriever
from midplane.retrieval.types import Document, RetrievalResult
from midplane.retrieval.weights import schema_prior

_METHODS = {"get", "post", "put", "patch", "delete"}
_CREATE_TABLE = re.compile(
    r"create\s+table\s+if\s+not\s+exists\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\);",
    re.IGNORECASE | re.DOTALL,
)
_SKIP_COLUMN = ("constraint", "primary", "unique", "check", "foreign")


class SchemaDocumentLoader(Protocol):
    """Source of schema and connector documents.

    A later ``midplane/schema_registry/`` loader can replace ``BuiltinSchemaLoader``.
    This package does not import that registry.
    """

    def load_documents(self) -> list[Document]:
        """Return the documents to index."""


def repo_root() -> Path:
    """Directory that holds ``openapi/`` and ``migrations/``.

    Editable checkouts find it from this file. The installed package finds it
    from the process working directory (the image workdir is ``/app``).
    """

    candidates = [Path.cwd(), *Path(__file__).resolve().parents]
    for candidate in candidates:
        if (candidate / "openapi" / "midplane-v0.yaml").is_file() and (candidate / "migrations").is_dir():
            return candidate
    raise FileNotFoundError("openapi/midplane-v0.yaml")


def connectors_path() -> Path:
    return Path(__file__).with_name("connectors.yaml")


class BuiltinSchemaLoader:
    """OpenAPI operations and components, migration tables, and the connector seed."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        connectors: Path | None = None,
    ) -> None:
        self.root = root or repo_root()
        self.connectors = connectors or connectors_path()

    def load_documents(self) -> list[Document]:
        return [
            *load_openapi_documents(self.root / "openapi" / "midplane-v0.yaml"),
            *load_migration_documents(self.root / "migrations"),
            *load_connector_documents(self.connectors),
        ]


def load_openapi_documents(path: Path) -> list[Document]:
    spec = yaml.safe_load(path.read_text()) or {}
    documents: list[Document] = []
    for path_key, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method not in _METHODS or not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or "")
            summary = str(operation.get("summary") or "")
            description = str(operation.get("description") or "")
            tags = " ".join(str(tag) for tag in operation.get("tags") or [])
            parameters = " ".join(
                str(parameter.get("name") or "")
                for parameter in operation.get("parameters") or []
                if isinstance(parameter, dict)
            )
            text = (
                f"{method.upper()} {path_key} operation {operation_id} "
                f"summary {summary} tags {tags} parameters {parameters} {description}"
            )
            documents.append(
                Document(
                    doc_id=f"openapi:{method.upper()}:{path_key}",
                    store="schema",
                    text=" ".join(text.split()),
                    metadata={
                        "store": "schema",
                        "kind": "endpoint",
                        "method": method.upper(),
                        "path": str(path_key),
                        "name": operation_id,
                        "status": "wired",
                        "source_file": "openapi/midplane-v0.yaml",
                    },
                    fields={
                        "path": str(path_key),
                        "method": method.upper(),
                        "name": operation_id,
                        "kind": "endpoint",
                    },
                )
            )
    schemas = (spec.get("components") or {}).get("schemas") or {}
    for name, schema in schemas.items():
        properties = ""
        description = ""
        if isinstance(schema, dict):
            description = str(schema.get("description") or "")
            raw_properties = schema.get("properties") or {}
            if isinstance(raw_properties, dict):
                properties = " ".join(str(key) for key in raw_properties)
        documents.append(
            Document(
                doc_id=f"schema:{name}",
                store="schema",
                text=f"schema component {name} {description} properties {properties}",
                metadata={
                    "store": "schema",
                    "kind": "schema",
                    "name": str(name),
                    "status": "wired",
                    "source_file": "openapi/midplane-v0.yaml",
                },
                fields={"name": str(name), "kind": "schema"},
            )
        )
    return documents


def load_migration_documents(directory: Path) -> list[Document]:
    documents: list[Document] = []
    for path in sorted(directory.glob("*.sql")):
        for match in _CREATE_TABLE.finditer(path.read_text()):
            table = match.group(1)
            columns = _columns(match.group(2))
            documents.append(
                Document(
                    doc_id=f"table:{table}",
                    store="schema",
                    text=f"table {table} columns {' '.join(columns)} migration {path.name}",
                    metadata={
                        "store": "schema",
                        "kind": "table",
                        "table": table,
                        "name": table,
                        "columns": columns,
                        "status": "wired",
                        "source_file": f"migrations/{path.name}",
                    },
                    fields={"table": table, "name": table, "kind": "table"},
                )
            )
    return documents


def _columns(body: str) -> list[str]:
    columns: list[str] = []
    for line in body.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped or stripped.startswith("--"):
            continue
        if stripped.lower().startswith(_SKIP_COLUMN):
            continue
        name = stripped.split()[0]
        if name and name not in columns:
            columns.append(name)
    return columns


def load_connector_documents(path: Path) -> list[Document]:
    payload = yaml.safe_load(path.read_text()) or {}
    rows = payload.get("connectors")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path} must contain a connectors list")
    documents: list[Document] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each connector must be a mapping")
        name = str(row.get("name") or "").strip()
        title = str(row.get("title") or name)
        status = str(row.get("status") or "")
        summary = " ".join(str(row.get("summary") or "").split())
        if not name or status not in {"wired", "planned"}:
            raise ValueError(f"connector {name or title} needs a name and status wired or planned")
        if row.get("real_money") is not False:
            raise ValueError(f"{name} must set real_money: false")
        paper_only = bool(row.get("paper_only"))
        if name == "trading-control" and paper_only is not True:
            raise ValueError("trading-control must set paper_only: true")
        documents.append(
            Document(
                doc_id=f"connector:{name}",
                store="schema",
                text=(
                    f"connector {name} {title} status {status} "
                    f"paper_only {str(paper_only).lower()} real_money false {summary}"
                ),
                metadata={
                    "store": "schema",
                    "kind": "connector",
                    "name": name,
                    "title": title,
                    "status": status,
                    "paper_only": paper_only,
                    "real_money": False,
                    "source_file": "midplane/retrieval/connectors.yaml",
                },
                fields={"name": name, "title": title, "kind": "connector", "status": status},
            )
        )
    return documents


class SchemaConnectorStore:
    """Indexes whatever ``loader`` returns. The default loader is the built-in seed."""

    def __init__(
        self,
        *,
        loader: SchemaDocumentLoader | None = None,
        embedder: Embedder | None = None,
        index: VectorIndex | None = None,
        database_url: str | None = None,
    ) -> None:
        self.loader = loader or BuiltinSchemaLoader()
        self.embedder = embedder or HashedNgramEmbedder()
        self.index = index or open_index(
            database_url,
            store_name="schema",
            dimension=self.embedder.dimension,
        )
        self._lock = threading.RLock()
        self.retriever = Retriever(
            name="schema",
            embedder=self.embedder,
            index=self.index,
            reranker=LexicalReranker(schema_prior),
        )
        self.reload()

    def close(self) -> None:
        self.index.close()

    def reload(self) -> int:
        documents = self.loader.load_documents()
        with self._lock:
            self.retriever.replace(documents)
        return len(documents)

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: dict | None = None,
        candidate_k: int | None = None,
    ) -> list[RetrievalResult]:
        with self._lock:
            return self.retriever.retrieve(query, top_k=top_k, filters=filters, candidate_k=candidate_k)
