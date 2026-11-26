# Schema agent

The schema agent is a Dev Guild worker that answers questions about the platform's schemas and connectors. It sits on the same graph as code review:

`supervisor` → `router` → `schema_agent` → `episode_writer`

An explicit `code_review` role stays on code review. A task whose role is `schema_agent`, `schema-agent`, or `auto`/`question` with a schema, connector, or integration question goes to this worker. The router still picks the model by task type, cost, and latency. Tests use the fake provider, so a run needs no network and no API key.

> RECONCILE with `dev_guild_prompt_templates.md`
>
> RECONCILE with `episode_schema.sql`
>
> RECONCILE with `trading_control_service_spec.md`

The prompt is `midplane/prompts/schema_agent.md`. Its version is `v0`. If the guild catalog renames the role or replaces the prompt, that file wins.

## Invoke it

From Python, one call writes one episode:

```python
from midplane.agents.schema_agent import run_schema_agent

result = run_schema_agent("Which connectors can read the episode schema?")
```

The default source is `dev_guild`. The episode records `agent=schema_agent` on the task, the plan, and the output. `POST /v1/runs` accepts the same worker as `task_type=schema_agent` or `agent=schema-agent`, with `input.goal`. Gateway schema runs also use `source=dev_guild`. `GET /v1/agents` lists `schema-agent`.

Constraints default to closed:

| Flag | Default | What it allows |
| --- | --- | --- |
| `allow_writes` | false | Capabilities whose effect is `write` |
| `allow_side_effects` | false | Capabilities whose effect is `side_effect`, and writes |
| `real_money` | false | Nothing. Setting it true does not enable live trading |

## Registries

`midplane/schema_registry/` builds a catalog from the repo. It does not keep a hand-copied second copy of those files.

| Entry | Source | What it records |
| --- | --- | --- |
| `episode` | `migrations/*.sql` and the `Episode` model | Columns, SQL types, nullability |
| `gateway.component.*`, `gateway.endpoint.*`, `gateway.error`, `gateway.sse.*` | `openapi/midplane-v0.yaml` | Components, parameters, the error envelope, SSE payloads |
| `midplane.state`, `midplane.node.<node>.reads`, `midplane.node.<node>.writes` | `MidPlaneState` and the node TypedDicts | Graph state and node I/O |
| `trading.control.action`, `trading.control.result` | `PaperOnlyTradingControlClient` | The action field the client reads, and the dict it returns |
| `export.jsonl`, `export.jsonl.message` | `training_record` | The JSONL object and one message |
| `schema_agent.result` | `SchemaAgentResultFields` | The worker's typed output |

Each entry has an id, a domain, a version, fields (name, type, nullable, required), a source file, and RECONCILE flags taken from `RECONCILE` lines in those sources. `episode` carries `episode_schema.sql` and `episode_example.json`. `export.jsonl` carries `dev_guild_prompt_templates.md`. `trading.control.result` carries `trading_control_service_spec.md` and `trading_risk_mitigation.md`.

`midplane/connectors/registry.yaml` is the connector catalog. Each connector has a name, kind, status (`wired` or `planned`), capabilities, auth, side effects, the schemas it produces or consumes, a safety tier, `real_money`, and example uses.

| Connector | Status | Notes |
| --- | --- | --- |
| `episode_store` | wired | SQLite and Postgres `EpisodeStore` |
| `gateway` | wired | REST and SSE. No trading path |
| `llm_router` | wired | Fake provider in tests. OpenAI and Anthropic need keys |
| `ios_swift_client` | wired | `clients/swift` compiles against the gateway. No app binary ships here |
| `github` | planned | The slice accepts a diff. It does not call GitHub |
| `trading_control` | planned | Paper-only interface. `real_money: false`. Hard external controls are required before any live use |
| `gmail`, `google_calendar`, `google_drive`, `x` | planned | No client in this repo |

## Tools

The worker calls these tools itself and stores each call on the episode. The tools are deterministic. The model call is only there so the router, the fallback, and the cost record stay on the same path as code review. Any text the model returns counts as a completed call. The structured result comes from the tools.

| Tool | Result |
| --- | --- |
| `lookup_schema` | One entry, or nothing |
| `search_schemas` | Ranked entries |
| `list_connectors` | The whole catalog, including planned connectors |
| `select_connectors(goal, constraints)` | Ranked connectors with reasons. Read capabilities stay. Write and side-effect capabilities are dropped unless allowed. A connector with `real_money: true` is never selected. A trading connector is also dropped when the goal asks for live or real-money trading |
| `plan_utilization(goal)` | Two to four strategies with different primary connectors |

A strategy has steps, the schema each step uses, the data that moves between two schemas, risks, and the `dev_guild` episodes the flywheel would store. Planned connectors are named as planned. A paper-trading strategy is available only when side effects are allowed and the goal is not a live trade. It uses `submit_paper`, says `real_money` is false, and says hard external controls are required before any live use. It does not place an order.

The output object matches `schema_agent.result`: `agent`, `agent_version`, `prompt_ref`, `prompt_version`, `goal`, `summary`, `schemas`, `connectors`, `excluded_connectors`, `strategies`.

## Retrieval

`MIDPLANE_RETRIEVAL` defaults to off. The worker then calls the tools and writes the episode exactly as it does with retrieval unset.

When the flag is `1`, `true`, `yes`, or `on`, the node loads documents through `SchemaDocumentLoader` into a `SchemaConnectorStore` before the model call. The loader covers every schema-registry entry and every connector-registry row. The store uses the local hashed embedder. The node keeps the top 5 schema hits and the top 5 connector hits, puts that text in front of the prompt, and records the doc ids on the `schema_agent` episode event and on a `schema_retrieval` decision trace. The tool results still come from the registries, not from the retrieved text. The graph edges stay `supervisor` → `router` → `schema_agent` → `episode_writer`. `retrieve_context` remains the code-review insertion.

## Tests

`tests/test_schema_agent.py` checks lookup and search, registry drift against `openapi/midplane-v0.yaml` and the episode migrations, connector filtering, the trading exclusion, plan diversity and schema references, routing, the episode row, and `POST /v1/runs`. `tests/test_schema_retrieval.py` checks the registry loader, flag-on retrieval, flag-off parity, and the recorded doc ids. The OpenAPI drift test in `tests/test_gateway.py` still compares `openapi/midplane-v0.yaml` to the served schema. Regenerate that file with `python -m midplane.gateway.export_openapi` after a contract change.
