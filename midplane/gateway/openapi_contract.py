"""OpenAPI adjustments that FastAPI does not emit on its own.

Nullable JSON is `type: [<type>, "null"]`. `anyOf` with `null` is removed so
swift-openapi-generator 1.14 keeps the property. The events operation is
`text/event-stream` with one schema component per event payload.
"""

from __future__ import annotations

SSE_DESCRIPTION = """
Server-Sent Events for one run. Each frame is three lines: `id`, `event`, and `data`, then a blank line.

`id` is a decimal integer, monotonic per run, starting at 1. It is stored with the run, not per connection.
Send it back as the `Last-Event-ID` request header to resume. The server replays frames whose id is greater than that value.
If the header is missing, not an integer, or not an id this run has stored, the server replays the whole log.

`event` is one of `run.started`, `node.started`, `node.completed`, `run.completed`, `run.failed`.
`data` is one JSON object. Its shape is the schema named for that event.

The graph reports a node when the node returns. The gateway then appends `node.started` and `node.completed` for that node, in that order, with the same `at` timestamp.
""".strip()

_EVENT_NAMES = [
    "run.started",
    "node.started",
    "node.completed",
    "run.completed",
    "run.failed",
]


def _nullable_string(title: str, description: str) -> dict:
    return {
        "type": ["string", "null"],
        "title": title,
        "description": description,
    }


def event_component_schemas() -> dict:
    return {
        "RunStartedData": {
            "type": "object",
            "title": "RunStartedData",
            "description": "Payload for event run.started.",
            "additionalProperties": False,
            "required": ["run_id", "task_type", "at"],
            "properties": {
                "run_id": {"type": "string", "title": "Run Id"},
                "task_type": {"type": "string", "title": "Task Type"},
                "at": {"type": "string", "title": "At", "description": "UTC timestamp."},
            },
        },
        "NodeStartedData": {
            "type": "object",
            "title": "NodeStartedData",
            "description": "Payload for event node.started.",
            "additionalProperties": False,
            "required": ["node", "at"],
            "properties": {
                "node": {"type": "string", "title": "Node"},
                "at": {"type": "string", "title": "At", "description": "UTC timestamp."},
            },
        },
        "NodeCompletedData": {
            "type": "object",
            "title": "NodeCompletedData",
            "description": "Payload for event node.completed.",
            "additionalProperties": False,
            "required": ["node", "status", "at"],
            "properties": {
                "node": {"type": "string", "title": "Node"},
                "status": {"type": "string", "enum": ["completed"], "title": "Status"},
                "at": {"type": "string", "title": "At", "description": "UTC timestamp."},
            },
        },
        "RunCompletedData": {
            "type": "object",
            "title": "RunCompletedData",
            "description": "Payload for event run.completed. episode_id is null only if the writer did not persist a row.",
            "additionalProperties": False,
            "properties": {
                "run_id": {"type": "string", "title": "Run Id"},
                "status": {"type": "string", "enum": ["succeeded"], "title": "Status"},
                "episode_id": _nullable_string(
                    "Episode Id",
                    "Present as a string when the episode row exists. JSON null when it does not. The key is sent either way.",
                ),
                "at": {"type": "string", "title": "At", "description": "UTC timestamp."},
            },
            "required": ["run_id", "status", "at"],
        },
        "RunFailedData": {
            "type": "object",
            "title": "RunFailedData",
            "description": "Payload for event run.failed.",
            "additionalProperties": False,
            "properties": {
                "run_id": {"type": "string", "title": "Run Id"},
                "status": {"type": "string", "enum": ["failed"], "title": "Status"},
                "episode_id": _nullable_string(
                    "Episode Id",
                    "String when an episode row was still written. JSON null when the run failed before the writer. The key is sent either way.",
                ),
                "error": _nullable_string("Error", "Failure message. JSON null when there is no message. The key is sent either way."),
                "at": {"type": "string", "title": "At", "description": "UTC timestamp."},
            },
            "required": ["run_id", "status", "at"],
        },
        "RunEvent": {
            "type": "object",
            "title": "RunEvent",
            "description": "One stored frame. The HTTP body is text/event-stream, not a JSON array of these objects.",
            "additionalProperties": False,
            "required": ["id", "event", "data"],
            "properties": {
                "id": {"type": "integer", "minimum": 1, "title": "Id"},
                "event": {"type": "string", "enum": list(_EVENT_NAMES), "title": "Event"},
                "data": {
                    "title": "Data",
                    "description": (
                        "JSON object for the event name: RunStartedData, NodeStartedData, "
                        "NodeCompletedData, RunCompletedData, or RunFailedData."
                    ),
                    "type": "object",
                    "additionalProperties": True,
                },
            },
        },
    }


def _is_null_schema(option: object) -> bool:
    return isinstance(option, dict) and option.get("type") == "null" and len(option) == 1


def _collapse_nullable_anyof(schema: dict) -> dict:
    options = schema.get("anyOf")
    if not isinstance(options, list):
        return schema
    nulls = [option for option in options if _is_null_schema(option)]
    others = [option for option in options if option not in nulls]
    if len(nulls) != 1 or len(others) != 1 or not isinstance(others[0], dict):
        return schema
    base = dict(others[0])
    if "$ref" in base or "type" not in base:
        return schema
    typ = base.pop("type")
    merged = {key: value for key, value in schema.items() if key != "anyOf"}
    merged.update(base)
    if isinstance(typ, list):
        merged["type"] = typ if "null" in typ else [*typ, "null"]
    else:
        merged["type"] = [typ, "null"]
    return merged


def rewrite_nullable(node: object) -> object:
    if isinstance(node, list):
        return [rewrite_nullable(item) for item in node]
    if not isinstance(node, dict):
        return node
    collapsed = _collapse_nullable_anyof(node)
    return {key: rewrite_nullable(value) for key, value in collapsed.items()}


def apply_contract(schema: dict) -> dict:
    """Return the OpenAPI document the checked-in YAML must match."""

    document = rewrite_nullable(schema)
    if not isinstance(document, dict):
        return schema
    components = document.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas.update(event_component_schemas())
    events = document.get("paths", {}).get("/v1/runs/{run_id}/events", {}).get("get")
    if isinstance(events, dict):
        events["description"] = SSE_DESCRIPTION
        responses = events.setdefault("responses", {})
        responses["200"] = {
            "description": SSE_DESCRIPTION,
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/RunEvent"},
                }
            },
        }
    return document
