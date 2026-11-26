"""Mid-plane Gateway API v0.

The checked-in OpenAPI document is openapi/midplane-v0.yaml. Change this
module and regenerate that file together. CI rejects a drift.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from midplane.gateway.errors import APIError
from midplane.gateway.openapi_contract import apply_contract
from midplane.gateway.keys import hash_token, pepper_from_env
from midplane.gateway.limiter import RateLimiter
from midplane.gateway.store import GatewayStore, KeyRecord, RunRecord, utc_now
from midplane.agents.schema_agent import schema_task
from midplane.graph import stream_review
from midplane.store import EpisodeStore, InvalidEpisodeCursor, open_store, resolve_database_url

logger = logging.getLogger(__name__)

API_DESCRIPTION = """
Mid-plane Gateway API v0. Paths live under /v1. This document's version is 0.1.0.

Authentication v0: `Authorization: Bearer <api key>` on every path except `GET /healthz`.
Keys are scoped to a client id such as `ios-dev`. A key reads only the runs and episodes created with that key.

Errors use one shape: `{"error": {"code": string, "message": string, "request_id": string}}`.
Every response carries `X-Request-ID`. Clients may send that header; otherwise the server generates one.

Pagination: list bodies are `{"data": [...], "next_cursor": string | null, "limit": integer}`.
`limit` defaults to 50 and must be from 1 to 100. `cursor` is opaque. Send `next_cursor` back as `cursor` for the next page. An unknown cursor returns `invalid_cursor`.

Rate limiting v0 is per API key inside a single process. Authenticated responses include `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset` (seconds until the window moves). Over the limit, the status is 429, the code is `rate_limited`, and the response also includes `Retry-After`.

Auth v1 is not built. The planned path is Sign in with Apple, then a short-lived JWT from this gateway. Bearer API keys stay available for service clients.

`POST /v1/runs` accepts `task_type` `code_review` or `schema_agent`, or an `agent` name (`schema-agent` or `schema_agent`). A code review requires `input.diff`. A schema agent run requires `input.goal` (or a non-default `input.instructions`).

This contract has no trading endpoints.
""".strip()

_bearer = HTTPBearer(auto_error=False, description="Bearer API key scoped to a client such as ios-dev.")


_TASK_TYPES = {"code_review", "schema_agent"}
_AGENT_TASKS = {
    "schema_agent": "schema_agent",
    "schema-agent": "schema_agent",
    "code_review": "code_review",
    "code-review": "code_review",
}
_DEFAULT_INSTRUCTIONS = "Review this diff."


def resolve_run_target(task_type: str | None, agent: str | None) -> str:
    """Return code_review or schema_agent from a task type, an agent name, or both."""

    agent_key = (agent or "").strip().lower()
    from_agent = _AGENT_TASKS.get(agent_key) if agent_key else None
    if agent_key and from_agent is None:
        raise ValueError("agent must be code_review, code-review, schema_agent, or schema-agent")
    if task_type and from_agent and task_type != from_agent:
        raise ValueError("agent and task_type name different workers")
    resolved = task_type or from_agent
    if resolved is None:
        raise ValueError("task_type or agent is required")
    return resolved


def schema_goal_from_input(goal: str | None, instructions: str | None) -> str:
    text = (goal or "").strip()
    if text:
        return text
    fallback = (instructions or "").strip()
    if fallback and fallback != _DEFAULT_INSTRUCTIONS:
        return fallback
    return ""


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diff: str | None = Field(
        default=None,
        description="Unified diff to review. Required when the task is code_review.",
    )
    repo: str = "local"
    instructions: str = _DEFAULT_INSTRUCTIONS
    goal: str | None = Field(
        default=None,
        description="Schema, connector, or integration question. Required when the task is schema_agent.",
    )
    allow_side_effects: bool = False
    allow_writes: bool = False


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: str | None = Field(
        default=None,
        description="Worker to run: code_review or schema_agent. Optional when agent is set. JSON null is allowed.",
    )

    @field_validator("task_type")
    @classmethod
    def known_task_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in _TASK_TYPES:
            raise ValueError("must be code_review or schema_agent")
        return value
    agent: str | None = Field(
        default=None,
        max_length=100,
        description="Agent name. schema-agent selects the schema agent. Optional when task_type is set.",
    )
    input: RunInput
    client_ref: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def resolve_target(self) -> CreateRunRequest:
        self.task_type = resolve_run_target(self.task_type, self.agent)  # type: ignore[assignment]
        if self.task_type == "code_review" and not (self.input.diff or "").strip():
            raise ValueError("input.diff is required for code_review")
        if self.task_type == "schema_agent" and not schema_goal_from_input(self.input.goal, self.input.instructions):
            raise ValueError("input.goal is required for schema_agent")
        return self


class CreateRunResponse(BaseModel):
    run_id: str
    status: Literal["queued", "running", "succeeded", "failed"]


class RunStatusResponse(BaseModel):
    """Nullable fields are sent as JSON null and may also be omitted.

    result, episode_id, client_ref, and error use type [value, "null"] and are not required.
    """

    run_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    task_type: str
    result: dict | None = None
    episode_id: str | None = None
    client_ref: str | None = None
    error: str | None = None


class EpisodeResponse(BaseModel):
    episode_id: str
    source: str
    inputs: dict
    plan: dict
    tool_calls: list
    model_choices: list
    outputs: dict
    cost: dict
    latency_ms: int
    outcome: str
    quality_score: float
    score_version: str
    created_at: str
    trace: list


class EpisodeSummary(BaseModel):
    episode_id: str
    source: str
    outcome: str
    quality_score: float
    score_version: str
    created_at: str
    latency_ms: int


class EpisodePage(BaseModel):
    """next_cursor is JSON null when there is no next page. The key is sent. It may also be omitted."""

    data: list[EpisodeSummary]
    next_cursor: str | None = None
    limit: int


class Agent(BaseModel):
    name: str
    title: str
    status: str


class AgentPage(BaseModel):
    """next_cursor is JSON null when there is no next page. The key is sent. It may also be omitted."""

    data: list[Agent]
    next_cursor: str | None = None
    limit: int


class HealthResponse(BaseModel):
    status: Literal["ok"]


def load_roster() -> list[dict]:
    payload = json.loads(Path(__file__).with_name("roster.json").read_text())
    if not isinstance(payload, list):
        raise RuntimeError("roster.json must be a list")
    return payload


def encode_cursor(value: str) -> str:
    return urlsafe_b64encode(value.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    padding = "=" * (-len(cursor) % 4)
    try:
        decoded = urlsafe_b64decode((cursor + padding).encode()).decode()
    except Exception as exc:
        raise APIError(400, "invalid_cursor", "The cursor is not valid.") from exc
    if not decoded:
        raise APIError(400, "invalid_cursor", "The cursor is not valid.")
    return decoded


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _rate_headers(request: Request) -> dict[str, str]:
    limit = getattr(request.state, "rate_limit", None)
    if not limit:
        return {}
    return {
        "RateLimit-Limit": str(limit),
        "RateLimit-Remaining": str(getattr(request.state, "rate_remaining", 0)),
        "RateLimit-Reset": str(getattr(request.state, "rate_reset", 1)),
    }


def _error_response(request: Request, error: APIError) -> JSONResponse:
    headers = {"X-Request-ID": _request_id(request)}
    headers.update(_rate_headers(request))
    if error.status_code == 429:
        headers["Retry-After"] = headers.get("RateLimit-Reset", "60")
    return JSONResponse(status_code=error.status_code, content=error.body(_request_id(request)), headers=headers)


def _format_sse(event: dict) -> str:
    data = json.dumps(event["data"], separators=(",", ":"), sort_keys=True)
    return f"id: {event['id']}\nevent: {event['event']}\ndata: {data}\n\n"


def _record_event(gateway: GatewayStore, run_id: str, name: str, data: dict) -> None:
    current = gateway.get_run(run_id)
    previous = current.events if current is not None else []
    next_id = int(previous[-1]["id"]) + 1 if previous else 1
    gateway.append_event(run_id, {"id": next_id, "event": name, "data": data})


def _resume_after(events: list, header: str | None) -> int:
    """Return the last id the client has. Unknown ids replay from the start."""

    if header is None or not str(header).strip():
        return 0
    try:
        requested = int(str(header).strip())
    except ValueError:
        return 0
    known = {int(event["id"]) for event in events if isinstance(event, dict) and "id" in event}
    if requested in known:
        return requested
    return 0


def _run_body(record: RunRecord) -> RunStatusResponse:
    return RunStatusResponse(
        run_id=record.run_id,
        status=record.status,  # type: ignore[arg-type]
        result=record.result,
        episode_id=record.episode_id,
        task_type=record.task_type,
        client_ref=record.client_ref,
        error=record.error_message,
    )


def create_app(
    database_url: str | None = None,
    rate_limit_per_minute: int | None = None,
    pepper: str | None = None,
    episode_store: EpisodeStore | None = None,
) -> FastAPI:
    url = database_url if database_url is not None else resolve_database_url()
    limit = (
        rate_limit_per_minute
        if rate_limit_per_minute is not None
        else int(os.environ.get("MIDPLANE_RATE_LIMIT_PER_MINUTE", "60"))
    )
    key_pepper = pepper if pepper is not None else pepper_from_env()
    gateway = GatewayStore(url)
    episodes = episode_store if episode_store is not None else open_store(url)
    limiter = RateLimiter(limit)
    roster = load_roster()

    app = FastAPI(
        title="Mid-plane Gateway API",
        version="0.1.0",
        summary="v0 HTTP contract for iOS and other clients.",
        description=API_DESCRIPTION,
        servers=[{"url": "http://localhost:8000", "description": "Local"}],
    )
    app.state.gateway = gateway
    app.state.episodes = episodes
    app.state.limiter = limiter
    app.state.pepper = key_pepper

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = FastAPI.openapi(app)
        app.openapi_schema = apply_contract(schema)
        return app.openapi_schema

    app.openapi = custom_openapi

    @app.middleware("http")
    async def request_ids(request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        request.state.request_id = incoming.strip() if incoming and incoming.strip() else f"req_{uuid.uuid4().hex}"
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        for name, value in _rate_headers(request).items():
            response.headers[name] = value
        return response

    @app.exception_handler(APIError)
    async def api_error(_request: Request, exc: APIError):
        return _error_response(_request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = []
        for item in exc.errors():
            field = ".".join(str(piece) for piece in item.get("loc", []) if piece != "body") or "body"
            issue = str(item.get("msg", "Invalid request"))
            details.append({"field": field, "issue": issue})
        summary = "; ".join(f"{item['field']}: {item['issue']}" for item in details) or "Invalid request."
        return _error_response(
            request,
            APIError(422, "validation_error", summary, details=details or None),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        codes = {
            400: "validation_error",
            401: "unauthorized",
            403: "unauthorized",
            404: "not_found",
            405: "method_not_allowed",
            422: "validation_error",
            429: "rate_limited",
        }
        detail = exc.detail if isinstance(exc.detail, str) else "The request failed."
        return _error_response(request, APIError(exc.status_code, codes.get(exc.status_code, "internal_error"), detail))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("unhandled gateway error", exc_info=exc)
        return _error_response(request, APIError(500, "internal_error", "The request failed."))

    def require_key(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> KeyRecord:
        if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
            raise APIError(401, "unauthorized", "Missing or invalid API key.")
        record = gateway.find_active_key(hash_token(credentials.credentials, key_pepper))
        if record is None:
            raise APIError(401, "unauthorized", "Missing or invalid API key.")
        request.state.rate_limit = limiter.per_minute
        allowed, remaining, reset = limiter.check(record.key_id)
        request.state.rate_remaining = remaining
        request.state.rate_reset = reset
        if not allowed:
            raise APIError(429, "rate_limited", "Rate limit exceeded.")
        return record

    def _owned_run(run_id: str, key: KeyRecord) -> RunRecord:
        record = gateway.get_run(run_id)
        if record is None or record.client_id != key.client_id:
            raise APIError(404, "not_found", "Run not found.")
        return record

    def _execute(run_id: str, payload: dict) -> None:
        gateway.mark_running(run_id)
        task_type = str(payload.get("task_type") or "code_review")
        observed = utc_now()
        _record_event(
            gateway,
            run_id,
            "run.started",
            {"run_id": run_id, "task_type": task_type, "at": observed},
        )
        worker_episodes = None
        try:
            worker_episodes = open_store(url)
            if task_type == "schema_agent":
                goal = schema_goal_from_input(payload.get("goal"), payload.get("instructions"))
                events = stream_review(
                    diff=payload.get("diff") or "",
                    repo=payload.get("repo") or "local",
                    instructions=goal,
                    run_id=run_id,
                    thread_id=run_id,
                    source="dev_guild",
                    role="schema_agent",
                    task=schema_task(
                        goal,
                        repo=payload.get("repo") or "local",
                        diff=payload.get("diff") or "",
                        constraints={
                            "allow_side_effects": bool(payload.get("allow_side_effects")),
                            "allow_writes": bool(payload.get("allow_writes")),
                        },
                    ),
                    store=worker_episodes,
                )
            else:
                events = stream_review(
                    diff=payload["diff"],
                    repo=payload.get("repo") or "local",
                    instructions=payload.get("instructions") or "Review this diff.",
                    run_id=run_id,
                    thread_id=run_id,
                    source="app",
                    role="code_review",
                    store=worker_episodes,
                )
            for event in events:
                if event["type"] == "node":
                    at = utc_now()
                    _record_event(gateway, run_id, "node.started", {"node": event["node"], "at": at})
                    _record_event(
                        gateway,
                        run_id,
                        "node.completed",
                        {"node": event["node"], "status": "completed", "at": at},
                    )
                elif event["type"] == "done":
                    succeeded = event["outcome"] == "success"
                    episode_id = event["episode_id"]
                    at = utc_now()
                    if succeeded:
                        _record_event(
                            gateway,
                            run_id,
                            "run.completed",
                            {
                                "run_id": run_id,
                                "status": "succeeded",
                                "episode_id": episode_id,
                                "at": at,
                            },
                        )
                    else:
                        _record_event(
                            gateway,
                            run_id,
                            "run.failed",
                            {
                                "run_id": run_id,
                                "status": "failed",
                                "episode_id": episode_id,
                                "error": "The graph run finished with outcome=error.",
                                "at": at,
                            },
                        )
                    gateway.finish_run(
                        run_id,
                        status="succeeded" if succeeded else "failed",
                        result=event["output"] if succeeded else None,
                        episode_id=episode_id,
                        error_message=None if succeeded else "The graph run finished with outcome=error.",
                    )
        except Exception as exc:
            _record_event(
                gateway,
                run_id,
                "run.failed",
                {
                    "run_id": run_id,
                    "status": "failed",
                    "episode_id": None,
                    "error": str(exc),
                    "at": utc_now(),
                },
            )
            gateway.finish_run(
                run_id,
                status="failed",
                result=None,
                episode_id=None,
                error_message=str(exc),
            )
        finally:
            if worker_episodes is not None:
                worker_episodes.close()

    @app.get("/healthz", response_model=HealthResponse, tags=["health"])
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.post("/v1/runs", response_model=CreateRunResponse, status_code=201, tags=["runs"])
    def create_run(body: CreateRunRequest, key: KeyRecord = Depends(require_key)) -> CreateRunResponse:
        run_id = str(uuid.uuid4())
        payload = body.input.model_dump()
        payload["task_type"] = body.task_type
        payload["agent"] = body.agent
        gateway.insert_run(
            run_id=run_id,
            client_id=key.client_id,
            client_ref=body.client_ref,
            task_type=body.task_type,
            payload=payload,
        )
        threading.Thread(target=_execute, args=(run_id, payload), name=f"run-{run_id}", daemon=True).start()
        return CreateRunResponse(run_id=run_id, status="queued")

    @app.get("/v1/runs/{run_id}", response_model=RunStatusResponse, tags=["runs"])
    def get_run(run_id: str, key: KeyRecord = Depends(require_key)) -> RunStatusResponse:
        return _run_body(_owned_run(run_id, key))

    @app.get("/v1/runs/{run_id}/events", tags=["runs"])
    def run_events(
        run_id: str,
        key: KeyRecord = Depends(require_key),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        _owned_run(run_id, key)

        def generate():
            record = gateway.get_run(run_id)
            seen = _resume_after(record.events if record is not None else [], last_event_id)
            deadline = time.monotonic() + 120
            while True:
                record = gateway.get_run(run_id)
                if record is None:
                    return
                for event in record.events:
                    if int(event["id"]) <= seen:
                        continue
                    yield _format_sse(event)
                    seen = int(event["id"])
                if record.status in {"succeeded", "failed"} and all(int(event["id"]) <= seen for event in record.events):
                    return
                if time.monotonic() > deadline:
                    return
                time.sleep(0.05)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/episodes", response_model=EpisodePage, tags=["episodes"])
    def list_episodes(
        source: Literal["dev_guild", "trading", "app"] | None = None,
        min_score: float | None = Query(default=None, ge=0, le=1),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
        key: KeyRecord = Depends(require_key),
    ) -> EpisodePage:
        try:
            page, next_cursor = episodes.list_episodes(
                source=source,
                min_score=min_score,
                run_ids=gateway.episode_ids_for_client(key.client_id),
                cursor=cursor,
                limit=limit,
            )
        except InvalidEpisodeCursor as exc:
            raise APIError(400, "invalid_cursor", "The cursor is not valid.") from exc
        return EpisodePage(
            data=[
                EpisodeSummary(
                    episode_id=item.run_id,
                    source=item.source,
                    outcome=item.outcome,
                    quality_score=item.quality_score,
                    score_version=item.score_version,
                    created_at=item.created_at,
                    latency_ms=item.latency_ms,
                )
                for item in page
            ],
            next_cursor=next_cursor,
            limit=limit,
        )

    @app.get("/v1/episodes/{episode_id}", response_model=EpisodeResponse, tags=["episodes"])
    def get_episode(episode_id: str, key: KeyRecord = Depends(require_key)) -> EpisodeResponse:
        owned = gateway.find_run_for_episode(episode_id, key.client_id)
        if owned is None:
            raise APIError(404, "not_found", "Episode not found.")
        episode = episodes.get(episode_id)
        if episode is None:
            raise APIError(404, "not_found", "Episode not found.")
        return EpisodeResponse(
            episode_id=episode.run_id,
            source=episode.source,
            inputs=episode.inputs,
            plan=episode.plan,
            tool_calls=episode.tool_calls,
            model_choices=episode.model_choices,
            outputs=episode.outputs,
            cost=episode.cost,
            latency_ms=episode.latency_ms,
            outcome=episode.outcome,
            quality_score=episode.quality_score,
            score_version=episode.score_version,
            created_at=episode.created_at,
            trace=episode.trace,
        )

    @app.get("/v1/agents", response_model=AgentPage, tags=["agents"])
    def list_agents(
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
        _key: KeyRecord = Depends(require_key),
    ) -> AgentPage:
        start = 0
        if cursor is not None:
            name = decode_cursor(cursor)
            names = [item["name"] for item in roster]
            if name not in names:
                raise APIError(400, "invalid_cursor", "The cursor is not valid.")
            start = names.index(name) + 1
        window = roster[start : start + limit]
        next_cursor = encode_cursor(window[-1]["name"]) if len(roster) > start + limit and window else None
        return AgentPage(
            data=[Agent(**item) for item in window],
            next_cursor=next_cursor,
            limit=limit,
        )

    return app
