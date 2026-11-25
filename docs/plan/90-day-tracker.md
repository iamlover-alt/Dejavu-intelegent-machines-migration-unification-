# 90-day tracker

Placeholder skeleton for the Unified AI Platform mid-plane. **Every row below is a placeholder.** Replace the milestone, definition of done, owner, status, and evidence link from `dejavu_90day_plan.xlsx`. Do not treat this table as the plan of record, and do not merge it with the spreadsheet: transcribe the spreadsheet over these rows.

`dejavu_90day_plan.xlsx` is a team artifact and is not in this repository.

> RECONCILE with `dejavu_90day_plan.xlsx`

Owners allowed in this tracker:

| Owner | Scope |
| --- | --- |
| Platform Architect | Mid-plane architecture, episode contract, iOS surface contract |
| Skeptic Lab (trading research, paper only) | Trading research. Paper mode only. Real money stays behind the external trading control service |
| Neutral Ops (general engineering) | General engineering: graph implementation, schema migration, observability plumbing |

Status for every seeded row is `placeholder`, except these rows, which are `in review`: **Ship the first vertical slice** ([pull request #2](https://github.com/iamlover-alt/Dejavu-intelegent-machines-migration-unification-/pull/2)), **Expose Gateway API v0** ([pull request #3](https://github.com/iamlover-alt/Dejavu-intelegent-machines-migration-unification-/pull/3)), and **Episode data flywheel v0** ([pull request #7](https://github.com/iamlover-alt/Dejavu-intelegent-machines-migration-unification-/pull/7)). Those rows' wording is still a placeholder relative to `dejavu_90day_plan.xlsx`.

Context for the placeholders: [mid-plane architecture](../architecture/mid-plane.md), [first vertical slice](../architecture/first-slice.md), [episode flywheel](../architecture/episode-flywheel.md), [Gateway API v0](../api/gateway-v0.md), [README](../../README.md).

| Milestone | Definition of done | Owner | Status | Evidence link |
| --- | --- | --- | --- | --- |
| PLACEHOLDER: Adopt the mid-plane architecture baseline | README, mid-plane spec, and first-slice plan are merged and the links between them resolve | Platform Architect | placeholder | [mid-plane architecture](../architecture/mid-plane.md) |
| PLACEHOLDER: Ship the first vertical slice | A `code_review` run writes one `episodes` row with a stub quality score and a full trace, and the [acceptance criteria](../architecture/first-slice.md#acceptance-criteria) pass | Neutral Ops (general engineering) | in review | [pull request #2](https://github.com/iamlover-alt/Dejavu-intelegent-machines-migration-unification-/pull/2) |
| PLACEHOLDER: Reconcile and apply the episode schema | `episode_schema.sql` is applied, and `episode_writer` persists a payload that matches `episode_example.json` | Platform Architect | placeholder | placeholder |
| PLACEHOLDER: Register the Dev Guild role catalog | Every role in `dev_guild_prompt_templates.md` is registered as a worker subgraph under the supervisor contract in the [Dev Guild section](../architecture/mid-plane.md#dev-guild-workers) | Neutral Ops (general engineering) | placeholder | placeholder |
| PLACEHOLDER: Enforce the trading control boundary in paper mode | Trading actions succeed only as tool calls to the trading control service; paper mode, hard limits, and the kill switch are enforced in that service; agent deployments have no broker credentials | Skeptic Lab (trading research, paper only) | placeholder | [trading integration](../architecture/mid-plane.md#trading-integration) |
| PLACEHOLDER: Stand up traces, metrics, and per-run cost | Every run emits a trace, the measurements in the [observability section](../architecture/mid-plane.md#observability), and a per-run cost stored on the episode | Neutral Ops (general engineering) | placeholder | [observability](../architecture/mid-plane.md#observability) |
| PLACEHOLDER: Expose Gateway API v0 | An iPhone app can build against the checked-in OpenAPI file: health, runs, SSE, episodes, and the agent roster, with bearer API keys. The [iOS checklist](../api/gateway-v0.md#ios-buildable-milestone) records what is done. | Platform Architect | in review | [pull request #3](https://github.com/iamlover-alt/Dejavu-intelegent-machines-migration-unification-/pull/3) |
| PLACEHOLDER: Expose the iOS mid-plane surface | An iOS client can create a run with `source=app` and read the episode, using the contract in `integration_guide.md` once reconciled with the [iOS section](../architecture/mid-plane.md#ios-integration-surface) | Platform Architect | placeholder | [iOS integration surface](../architecture/mid-plane.md#ios-integration-surface) |
| PLACEHOLDER: Episode data flywheel v0 | Episodes have a `score_version`, a heuristic score, export indexes, a JSONL export command, and `GET /v1/episodes` with source, min score, and cursor filters. Details are in the [flywheel doc](../architecture/episode-flywheel.md). | Platform Architect | in review | [pull request #7](https://github.com/iamlover-alt/Dejavu-intelegent-machines-migration-unification-/pull/7) |
| PLACEHOLDER: Replace the stub score and export train/eval data | `quality_score` comes from the rubric in the [flywheel section](../architecture/mid-plane.md#episode-data-flywheel), and an export writes disjoint train and eval sets keyed by episode `run_id` | Platform Architect | placeholder | [episode data flywheel](../architecture/mid-plane.md#episode-data-flywheel) |

## Related documents

- [README](../../README.md)
- [Mid-plane architecture](../architecture/mid-plane.md)
- [First vertical slice](../architecture/first-slice.md)
- [Gateway API v0](../api/gateway-v0.md)
- [Episode flywheel v0](../architecture/episode-flywheel.md)
