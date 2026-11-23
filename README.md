# Dejavú Intelligent Machines — Unified AI Platform mid-plane

This repository is the home of the **mid-plane**: the persistent digital identity layer of the Dejavú Intelligent Machines Unified AI Platform.

The mid-plane gives every platform run a durable identity — what was asked, which agent ran, which models and tools were used, what it cost, and how it turned out — and keeps that identity available to the rest of the platform. Three surfaces sit on it:

- **Dev Guild** — AI coding agents that do the engineering work. Each agent is a worker subgraph under a hierarchical supervisor.
- **Trading** — the trading bot that funds the platform. Operation is paper-only until hard external controls allow anything else. Real-money actions stay behind those controls. Agents reach trading only by calling an external trading control service.
- **iOS apps** — product surfaces. They start runs and read the resulting identity. The agent runtime stays on the mid-plane.

Every graph run writes one **episode**. The episode database is the closed-loop corpus for training and eval.

The [first vertical slice](docs/architecture/first-slice.md) is implemented in the `midplane` package: `supervisor` → `router` → `code_review` → `episode_writer`.

## Run the first slice

```bash
python -m pip install -e ".[dev]"
python -m midplane.run --diff path/to/change.diff
```

The command prints the episode id. With no API key it uses the built-in fake model provider and writes episodes to `sqlite:///midplane_episodes.sqlite`. Override the database with `--database` or `MIDPLANE_DATABASE_URL` (`sqlite:///...` or `postgres://...`).

`MIDPLANE_MODEL_PROVIDER=openai` or `anthropic` selects a real provider. Those modes read `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`, and they require `MIDPLANE_PRIMARY_MODEL` (optional `MIDPLANE_FALLBACK_MODEL`). Apply `migrations/001_episodes.sql` before using Postgres, and install the Postgres driver with `pip install -e ".[postgres]"`.

Routing thresholds, when you want to override the defaults: `MIDPLANE_MAX_COST`, `MIDPLANE_MAX_LATENCY_MS`, `MIDPLANE_MIN_CONFIDENCE`.

## Export train and eval sets

```bash
python -m midplane.export --out-dir out/ --eval-fraction 0.2
```

Writes `train.jsonl` and `eval.jsonl`, one episode per line. The split is a deterministic hash of `--seed` and `run_id`, so the sets are disjoint and an episode keeps its set across exports. `--min-quality` and `--outcome` filter first. The quality rubric is not defined yet, so the filter uses whatever score the episode carries.

## Doc map

| Document | What it decides |
| --- | --- |
| [Mid-plane architecture](docs/architecture/mid-plane.md) | Supervisor, model routing, shared state, the episode flywheel, Dev Guild workers, the trading boundary, observability, the iOS surface, and open questions |
| [First vertical slice](docs/architecture/first-slice.md) | What to build first: supervisor → router → code-review worker → `episode_writer`, plus acceptance criteria and a test plan |
| [90-day tracker](docs/plan/90-day-tracker.md) | Placeholder milestones. Replace the rows from `dejavu_90day_plan.xlsx` |

## How to read

1. [Mid-plane architecture](docs/architecture/mid-plane.md) for the decisions.
2. [First vertical slice](docs/architecture/first-slice.md) for the build order and the tests that prove it.
3. [90-day tracker](docs/plan/90-day-tracker.md) for the placeholder plan skeleton.

## Invariants

- One episode is written at the end of every graph run, including failed runs.
- Provenance on every episode is `dev_guild`, `trading`, or `app`.
- Trading actions go through the external trading control service. Broker credentials stay in that service. Paper mode, hard limits, and the kill switch are enforced there, outside the LLM.
- The first registered Dev Guild worker is `code_review`.

## External artifacts

The team already holds the artifacts below. They are the source of truth for the details they own. They are not vendored in this repository, so this README links only to documents that exist here. Where a section depends on an artifact, that section is marked `RECONCILE with <artifact>`, and the artifact wins over any illustrative snippet in these docs.

| Artifact | What it owns |
| --- | --- |
| `episode_schema.sql` | The `episodes` table |
| `episode_example.json` | The canonical episode payload |
| `langgraph_nodes_pseudocode.py` | Node behavior |
| `langgraph_state_management.md` | State fields and checkpointing |
| `dev_guild_prompt_templates.md` | Guild role list and prompts |
| `trading_control_service_spec.md` | Trading control service contract |
| `trading_risk_mitigation.md` | Trading risk controls |
| `risk_register.md` | Platform risk register |
| `integration_guide.md` | iOS and service integration |
| `dejavu_90day_plan.xlsx` | 90-day plan of record |
