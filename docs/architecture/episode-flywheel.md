# Episode data flywheel v0

Every graph run already writes one episode. The implementation is [in review](https://github.com/iamlover-alt/Dejavu-intelegent-machines-migration-unification-/pull/7). This slice makes that table usable as training data: a Postgres store, a replaceable quality score, an export command, and a list API.

The [first slice](first-slice.md) still owns the graph. The [gateway](../api/gateway-v0.md) is what an iOS client calls. This document owns scoring, listing, and the JSONL export.

> RECONCILE with `episode_schema.sql`
>
> RECONCILE with `episode_example.json`
>
> RECONCILE with `dev_guild_prompt_templates.md`

`quality_score`, `score_version`, and `created_at` are guesses. If `episode_schema.sql` names or types them differently, that file wins and this code changes with it. The export `prompt` is reconstructed from the code-review inputs. If `dev_guild_prompt_templates.md` defines the training text, that template replaces `render_code_review_prompt` in the exporter.

## Store

`EpisodeStore` is the interface (`upsert`, `get`, `count`, `list_episodes`). `SqliteEpisodeStore` remains the default for tests and the CLI. `PostgresEpisodeStore` is selected when the URL starts with `postgres://` or `postgresql://`.

Connection order:

1. An explicit URL argument.
2. `DATABASE_URL`.
3. `MIDPLANE_DATABASE_URL`.
4. `sqlite:///midplane_episodes.sqlite`.

`migrations/001_episodes.sql` creates the table. `migrations/002_gateway.sql` creates API keys and runs. Indexes and the two new episode columns are `migrations/003_episode_indexes.sql`, because 002 was already taken:

| Index | Supports |
| --- | --- |
| `episodes_source_quality_score_idx` | `source` plus `min_score` |
| `episodes_source_created_at_idx` | `source` plus `--since` |
| `episodes_created_at_run_id_idx` | newest-first pages and the cursor |

`created_at` is UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`, so string comparison matches time order. `score_version` records which scorer wrote `quality_score`. Rows migrated from the first slice default to `stub-v0`.

## Scoring

`QualityScorer` is `version` plus `score(state) -> float`. The writer calls whatever scorer is on the graph. The default is `HeuristicQualityScorer` (`heuristic-v0`):

| Check | Points |
| --- | --- |
| Run completed: `outcome=success`, no error, trace contains `supervisor`, `router`, `episode_writer`, and either `code_review` or `schema_agent` | 0.40 |
| Review `output.findings` has at least one finding with a detail, or schema-agent `output.strategies` has a strategy with steps | 0.30 |
| `latency_ms` is at most 30000 | 0.15 |
| `cost.total_usd` is at most 1.0 | 0.15 |

The sum is capped at 0.99. `1.0` stays unused so a later judge rubric can occupy it. `StubQualityScorer` (`stub-v0`) is the first-slice function, still injectable via `run_review(..., scorer=StubQualityScorer())`.

A failed run can still score above 0 when it produced findings or stayed inside the latency and cost budgets. The checks are independent. Before scoring, a missing cost object is stored as zero tokens and zero dollars, so a run that never called a model can still earn the cost point.

## Export

```bash
python -m midplane.export --since 2026-11-01T00:00:00.000000Z --min-score 0.5 --source dev_guild --out train.jsonl
```

`--since` is inclusive. `--source` is `dev_guild`, `trading`, or `app`. The command prints the row count on stderr and writes JSONL. Each line is one object:

| Field | Meaning |
| --- | --- |
| `episode_id` | Episode `run_id` |
| `source` | Provenance |
| `created_at` | When the writer stored the row |
| `score` | `quality_score` |
| `score_version` | Scorer that produced `score` |
| `outcome` | `success` or `error` |
| `model_choice` | The router record (`kind=route`) when one was stored |
| `prompt` | User text reconstructed from the code-review input |
| `messages` | `user` then `assistant`. The assistant content is the episode outputs as JSON |

Trading episodes can be exported. They are paper-mode research records. This command does not call a broker and does not add a trading tool.

## List API

`GET /v1/episodes` is on the gateway contract. Query parameters: `source`, `min_score` (0 to 1), `limit` (default 50, max 100), `cursor`. The body is `{data, next_cursor, limit}`. Each item is a summary: `episode_id`, `source`, `outcome`, `quality_score`, `score_version`, `created_at`, `latency_ms`. The full trace stays on `GET /v1/episodes/{episode_id}`.

The list is scoped to the API key's client. A key cannot page another client's episodes. The export command reads the database directly and is not client-scoped; it is an operator tool.

`openapi/midplane-v0.yaml` is still the source of truth. CI fails if the served schema drifts, and the Swift client job compiles the generated client.

## Related documents

- [README](../../README.md)
- [Mid-plane architecture](mid-plane.md)
- [First vertical slice](first-slice.md)
- [Gateway API v0](../api/gateway-v0.md)
- [90-day tracker](../plan/90-day-tracker.md)
