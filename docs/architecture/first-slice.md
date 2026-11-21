# First vertical slice

The first implementation of the [mid-plane](mid-plane.md) is one vertical path:

`supervisor` → `router` → `code_review` → `episode_writer`

`code_review` is the first Dev Guild worker. `episode_writer` upserts one row into the Postgres `episodes` table with a stub quality score and the full trace. A failed worker still reaches `episode_writer`.

The slice proves the identity loop. It leaves the supervisor re-plan loop, extra guild roles, the trading tool, the iOS client, and the real quality rubric for later milestones in the [tracker](../plan/90-day-tracker.md).

> RECONCILE with `langgraph_nodes_pseudocode.py`
>
> RECONCILE with `langgraph_state_management.md`
>
> RECONCILE with `episode_schema.sql`
>
> RECONCILE with `episode_example.json`
>
> RECONCILE with `dev_guild_prompt_templates.md`

If a name or a column in those artifacts differs from this plan, the artifact wins and this plan is updated before the code freezes.

## Nodes

Happy path, in order:

| Node | Responsibility | Reads | Writes |
| --- | --- | --- | --- |
| `supervisor` | Validates the task role is `code_review`. Writes a one-step plan. Deterministic in this slice: model calls belong to the worker. | `task`, `source` | `plan`, `trace`, `error` on rejection |
| `router` | Selects the model for task type `code_review` and records task type, cost, latency, and confidence criteria. Performs no model call. | `plan`, route-table config | `route`, `model_choices`, `trace` |
| `code_review` | Runs the guild prompt for `code_review` on the routed model. On provider error, timeout, or unparseable output, tries `route.fallback` once. | `task`, `route`, prompt template | `output`, `model_choices`, `tool_calls`, `cost`, `latency_ms`, `trace`, `error` |
| `episode_writer` | Computes the stub quality score and upserts one `episodes` row, including the full trace. | the whole state | `quality_score`, the `episodes` row, a final `trace` event included in that row |

Error path: a node that cannot finish sets `error` and `outcome=error`. The conditional edge goes to `episode_writer`. The writer is the only success and failure exit before `END`.

```python
# Illustrative wiring. langgraph_nodes_pseudocode.py wins on structure.
graph = StateGraph(MidPlaneState)
graph.add_node("supervisor", supervisor)
graph.add_node("router", router)
graph.add_node("code_review", code_review)
graph.add_node("episode_writer", episode_writer)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_or_write, ["router", "episode_writer"])
graph.add_conditional_edges("router", review_or_write, ["code_review", "episode_writer"])
graph.add_conditional_edges("code_review", write_episode, ["episode_writer"])
graph.add_edge("episode_writer", END)
```

`route_or_write` and `review_or_write` return the next node name: `episode_writer` when `error` is set, otherwise the happy-path successor. When the route table has no eligible model, `router` sets `error` and the run goes to `episode_writer`.

Tool allowlist for `code_review` in this slice: empty. Read-only repository inspection, if the prompt templates require it, is added when those templates are reconciled, and it is still an allowlist with no trading tool and no broker client.

Prompt text is loaded by `prompt_ref=code_review`.

> RECONCILE with `dev_guild_prompt_templates.md`

## State fields

Slice proposal. `langgraph_state_management.md` overrides these names.

| Field | Type (illustrative) | Who sets it |
| --- | --- | --- |
| `run_id` | string, unique | caller, at graph entry |
| `thread_id` | string | caller; checkpointer key |
| `source` | `dev_guild` in this slice | caller |
| `task` | object: `role`, `repo`, `diff`, `instructions` | caller |
| `plan` | object: `steps: ["code_review"]` | `supervisor` |
| `route` | object: `primary`, `fallback`, `criteria` | `router` |
| `model_choices` | append-only list | `router`, `code_review` |
| `tool_calls` | append-only list | `code_review` (empty is valid) |
| `output` | object, worker result | `code_review` |
| `cost` | object: token counts and a total when the provider prices the call | `code_review` |
| `latency_ms` | int, sum of node durations | each node adds its duration |
| `outcome` | `success` or `error` | last worker node, or `episode_writer` if still unset |
| `quality_score` | float | `episode_writer` |
| `trace` | append-only list of events | every node |
| `error` | string or null | the node that failed |

```python
# Illustrative. langgraph_state_management.md wins.
class MidPlaneState(TypedDict):
    run_id: str
    thread_id: str
    source: Literal["dev_guild", "trading", "app"]
    task: dict
    plan: dict
    route: dict
    model_choices: Annotated[list[dict], operator.add]
    tool_calls: Annotated[list[dict], operator.add]
    output: dict
    cost: dict
    latency_ms: int
    outcome: Literal["success", "error"] | None
    quality_score: float | None
    trace: Annotated[list[dict], operator.add]
    error: str | None
```

Checkpoint after each node. Backend: whatever `langgraph_state_management.md` specifies. If it is silent, use the LangGraph Postgres saver in the same database as `episodes`, keyed by `thread_id`.

> RECONCILE with `langgraph_state_management.md`

`route.criteria` always contains the keys `task_type`, `cost`, `latency`, and `confidence`. Values come from the route-table config. This slice needs a config fixture with one primary model and one fallback model; the real catalog is an [open question](mid-plane.md#open-questions).

## Persistence

`episode_writer` upserts on `run_id`. A replay of the same run leaves one row.

The row carries the flywheel fields from the [episode section](mid-plane.md#episode-data-flywheel): inputs, plan, tool calls, model choices, outputs, cost, latency, outcome, quality score, `source=dev_guild`, and the full trace. The trace stored on the row includes the `episode_writer` event, appended before the write.

```sql
-- ILLUSTRATIVE ONLY. episode_schema.sql is the table of record.
create table episodes (
  run_id         text primary key,
  source         text not null check (source in ('dev_guild', 'trading', 'app')),
  inputs         jsonb not null,
  plan           jsonb not null,
  tool_calls     jsonb not null,
  model_choices  jsonb not null,
  outputs        jsonb not null,
  cost           jsonb not null,
  latency_ms     integer not null,
  outcome        text not null check (outcome in ('success', 'error')),
  quality_score  double precision not null,
  trace          jsonb not null
);
```

> RECONCILE with `episode_schema.sql`
>
> RECONCILE with `episode_example.json`

### Stub quality score

```python
def stub_quality_score(state: dict) -> float:
    complete = all(state.get(k) is not None for k in (
        "run_id", "source", "task", "plan", "model_choices",
        "tool_calls", "cost", "latency_ms", "outcome", "trace",
    ))
    traced = {event.get("node") for event in (state.get("trace") or [])} >= {
        "supervisor", "router", "code_review", "episode_writer",
    }
    if (
        state.get("outcome") == "success"
        and state.get("error") is None
        and complete
        and traced
        and state.get("output")
    ):
        return 0.5
    return 0.0
```

Call order inside `episode_writer`: append the writer trace event, set `outcome` to `error` when `error` is set and `outcome` is still empty, otherwise set `outcome` to `success` when it is empty, call `stub_quality_score`, then upsert. On the error path the writer stores `{}` for a missing `output` and `[]` for missing list fields so the row still has every required column. `1.0` is reserved for the real rubric, so stubbed rows stay distinguishable. Scale and column type follow the schema if it disagrees.

> RECONCILE with `episode_schema.sql`

## Acceptance criteria

1. A valid `code_review` task visits `supervisor`, `router`, `code_review`, and `episode_writer` in that order.
2. The run upserts exactly one `episodes` row for that `run_id`, with `source=dev_guild`.
3. The row contains inputs, plan, tool calls, model choices, outputs, cost, latency, outcome, quality score, and trace.
4. `quality_score` equals `stub_quality_score` for the persisted state.
5. On a run that reaches `code_review`, the stored trace contains an event whose node is each of `supervisor`, `router`, `code_review`, and `episode_writer`.
6. `model_choices` includes a route record whose criteria keys are `task_type`, `cost`, `latency`, and `confidence`.
7. When `code_review` fails on the primary model and the fallback succeeds, `outcome=success` and `model_choices` lists both attempts.
8. When `code_review` fails on both models, one row is still written, `outcome=error`, and `quality_score=0.0`.
9. Writing the same `run_id` again leaves a single row.
10. The slice's tool allowlist contains no trading-control tool and no broker client.
11. After the run, a checkpoint exists for `thread_id`.

## Test plan

Tests map to the acceptance criteria. Integration tests use a disposable Postgres. The model provider is a fake that returns scripted completions, errors, and token counts.

| Test | Proves | Criteria |
| --- | --- | --- |
| Compiled edges: happy path is supervisor → router → code_review → episode_writer, and each earlier node can edge to `episode_writer` when `error` is set | Topology | 1 |
| Invoke a fixture task; assert node order from the trace | Happy path | 1, 5 |
| Query `episodes` by `run_id`; assert one row, `source=dev_guild`, and the required fields non-null | Persistence | 2, 3 |
| Compare `quality_score` to `stub_quality_score(state)` | Stub score | 4 |
| Assert criteria keys on the recorded route | Router contract | 6 |
| Fake a primary-model error and a successful fallback | Fallback | 7 |
| Fake errors from both models | Failure still recorded | 8 |
| Run `episode_writer` twice with the same `run_id` | Idempotency | 9 |
| Inspect the registered tool objects | Trading boundary holds on day one | 10 |
| Look up the checkpoint by `thread_id` | Checkpoint | 11 |
| Unit-test `choose_model` records all four criteria and a fallback when the table has two eligible models | Router is pure and testable | 6 |

Provider fakes must not open a network connection. Trading and broker packages are absent from the slice's dependency list; the tool-allowlist test is the runtime check, the dependency list is the build check.

A slice is done when every row in the table passes and the reconciled schema still satisfies criteria 2 through 5. If reconciliation changes a column or a field name, update the test and this plan in the same change.

## Related documents

- [README](../../README.md)
- [Mid-plane architecture](mid-plane.md)
- [90-day tracker](../plan/90-day-tracker.md)
