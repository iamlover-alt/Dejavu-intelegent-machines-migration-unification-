# Gateway API v0

The first HTTP surface an iPhone app can build against. The implementation is [in review](https://github.com/iamlover-alt/Dejavu-intelegent-machines-migration-unification-/pull/3). It wraps the [first vertical slice](../architecture/first-slice.md): `supervisor` → `router` → `code_review` → `episode_writer`. Runs started here are ordinary graph runs with `source=app`.

The contract file is [openapi/midplane-v0.yaml](../../openapi/midplane-v0.yaml) (OpenAPI 3.1). The served schema is generated from the same FastAPI app. CI fails if the two differ. After an intentional change, regenerate and commit the file:

```bash
python -m midplane.gateway.export_openapi
```

> RECONCILE with `integration_guide.md`
>
> If that guide already names paths, auth, or payloads, it wins and this contract is updated to match.

There are no trading endpoints. The paper-only trading client stays inside the process and is not routed.

## Run it

One command from the repo root:

```bash
docker compose up --build
```

`scripts/dev-up.sh` is the same command. Compose starts Postgres 16 and the API on port 8000. The API log line `dev api key:` is the bearer token for client `ios-dev`. It is rewritten to the same value on later starts because compose keeps `/data/dev-api-key` in a volume.

Without Docker, against the default SQLite file:

```bash
python -m pip install -e ".[dev,postgres]"
python -m midplane.gateway.seed
python -m midplane.gateway
```

`GET /healthz` does not use the key. Every `/v1` route does: `Authorization: Bearer <token>`.

Mint and revoke keys yourself:

```bash
python -m midplane.gateway.keys mint --client ios-dev
python -m midplane.gateway.keys revoke --key-id key_...
```

## Paths

| Method and path | Auth | Behavior |
| --- | --- | --- |
| `GET /healthz` | none | `{"status": "ok"}` |
| `POST /v1/runs` | bearer | Body `{task_type, input, client_ref?}`. v0 accepts `task_type=code_review` and `input.diff`. Response `{run_id, status}` with status `queued`. |
| `GET /v1/runs/{run_id}` | bearer | `queued`, `running`, `succeeded`, or `failed`, plus `result`, `episode_id`, and `error`. |
| `GET /v1/runs/{run_id}/events` | bearer | `text/event-stream`. Frames are `run.started`, `node.started`, `node.completed`, `run.completed`, and `run.failed`. |
| `GET /v1/episodes/{episode_id}` | bearer | The persisted episode, including `trace` and `quality_score`. |
| `GET /v1/agents` | bearer | Static roster: name, title, status. |

A key is scoped to its client id. `ios-dev` cannot read a run created by another client. The response is `404`, so the other client's ids are not confirmed.

`episode_id` is the graph run id. The episode row is written even when the graph outcome is `error`; the HTTP status is then `failed` and `episode_id` is still set. A crash before the episode writer leaves `episode_id` null.

## Run events

`GET /v1/runs/{run_id}/events` returns `text/event-stream`. Each frame is:

```text
id: 1
event: run.started
data: {"at":"...","run_id":"...","task_type":"code_review"}

```

`id` is a decimal integer stored on the run. It starts at 1 and increases by 1 for each frame. It is not reset per connection.

`event` is one of:

| Event | `data` schema | Payload |
| --- | --- | --- |
| `run.started` | `RunStartedData` | `run_id`, `task_type`, `at` |
| `node.started` | `NodeStartedData` | `node`, `at` |
| `node.completed` | `NodeCompletedData` | `node`, `status` (`completed`), `at` |
| `run.completed` | `RunCompletedData` | `run_id`, `status` (`succeeded`), `episode_id`, `at` |
| `run.failed` | `RunFailedData` | `run_id`, `status` (`failed`), `episode_id`, `error`, `at` |

The graph reports a node when that node returns. The gateway then appends `node.started` and `node.completed` for it, in that order, with the same `at` timestamp.

Resume with the request header `Last-Event-ID`. The server replays stored frames whose `id` is greater than that value, then waits for new ones if the run is still going. If the header is missing, not an integer, or not an id stored for this run, the server replays the full log.

`episode_id` and `error` on the terminal payloads are JSON null when there is no value, and the keys are still sent.

## Nullability

swift-openapi-generator 1.14 drops `anyOf` with `null`. These fields use `type: [<type>, "null"]` and are not in `required`, so a client can omit them. The server still sends the response keys, using JSON null when there is no value.

| Field | Where | Absent | JSON null | Server |
| --- | --- | --- | --- | --- |
| `client_ref` | `POST /v1/runs` body | allowed | allowed, stored as no reference | response always includes the key |
| `cursor` | `GET /v1/agents` query | omit the query | schema allows it; clients should omit it | query is not echoed |
| `next_cursor` | `AgentPage` | allowed | allowed | key is sent; null when the page is done |
| `result` | `RunStatusResponse` | allowed | allowed | key is sent; null until the run succeeds |
| `episode_id` | `RunStatusResponse`, `RunCompletedData`, `RunFailedData` | allowed | allowed | key is sent; null until an episode row exists |
| `client_ref` | `RunStatusResponse` | allowed | allowed | key is sent; null when the request omitted it |
| `error` | `RunStatusResponse`, `RunFailedData` | allowed | allowed | key is sent; null when the run did not fail |

## Errors, request ids, pagination

Every non-2xx body is:

```json
{"error": {"code": "unauthorized", "message": "Missing or invalid API key.", "request_id": "req_..."}}
```

Codes used in v0: `unauthorized`, `not_found`, `method_not_allowed`, `validation_error`, `invalid_cursor`, `rate_limited`, `internal_error`.

Every response includes `X-Request-ID`. Send one to correlate a call; otherwise the server mints `req_` plus a random hex string and echoes it.

List responses are `{data, next_cursor, limit}`. `limit` defaults to 50 and must be from 1 to 100. `cursor` is opaque. Pass `next_cursor` back as `cursor`. A cursor that is not in the collection is `invalid_cursor`. `GET /v1/agents` is the list that uses this rule. The roster order is the order in `midplane/gateway/roster.json`.

Authenticated calls are limited per key, inside one process, default 60 per minute (`MIDPLANE_RATE_LIMIT_PER_MINUTE`, `0` disables it). Responses include `X-RateLimit-Limit` and `X-RateLimit-Remaining`. The 429 body uses code `rate_limited` and sets `Retry-After: 60`. This limiter does not span multiple machines.

## Auth v0 and the v1 path

v0 keys are bearer tokens. The database stores HMAC-SHA256 of the token with `MIDPLANE_KEY_PEPPER`, not the token. The mint command prints the token once. Revocation sets `revoked_at`. The dev seed file `.dev-api-key` (or `MIDPLANE_DEV_KEY_FILE`) holds the local plaintext so restarts can print it. Do not use that file or the default pepper outside a laptop.

The default pepper is the literal `dev-pepper-not-for-production`. Any shared or hosted process has to set `MIDPLANE_KEY_PEPPER` to a secret. Changing the pepper invalidates existing keys.

Auth v1 is not implemented. The planned path is:

1. The iOS app uses Sign in with Apple and sends the identity token to the gateway.
2. The gateway checks the token with Apple and returns a short-lived JWT.
3. API calls send `Authorization: Bearer <jwt>` until it expires.
4. Bearer API keys remain for service clients such as `ios-dev` during development.

## iOS-buildable milestone

| Check | Status |
| --- | --- |
| OpenAPI 3.1 file checked in at `openapi/midplane-v0.yaml` | done |
| CI fails if the served schema drifts from that file | done |
| `GET /healthz` without auth | done |
| Create a run, read its status, read the episode | done |
| SSE stream of node progress (`supervisor`, `router`, `code_review`, `episode_writer`) | done |
| Agent roster with the ten named agents | done |
| Bearer API keys, hashed at rest, mint and revoke CLI, scoped per client | done |
| One error shape, request ids, pagination on list responses | done |
| Rate limit on authenticated calls (single process) | done |
| No trading endpoints | done |
| Swift client generated with Apple's `swift-openapi-generator` and compiled in CI | in CI (`swift-client` job, `scripts/generate-swift-client.sh`) |
| Sign in with Apple and a short-lived JWT | not in v0; planned above |
| App installed on a device, calling a hosted URL | needs a Fly.io app and an Apple Developer team; see below |
| Hosted deploy | config is in `fly.toml`; this repository does not deploy |

## What William needs before a hosted deploy

This repository does not deploy. `fly.toml` is only the config. William needs:

1. A Fly.io account and the `fly` CLI logged in (`fly auth login`). Fly app names are global, so replace `app = "dejavu-midplane-gateway"` in `fly.toml` with a name that is free, or pick one at `fly launch` time. Do not run that from this change.
2. A Fly Postgres database. `fly postgres create` (or Fly Managed Postgres) and attach it, or set the connection string by hand.
3. Secrets, set with `fly secrets set`, never committed:
   - `MIDPLANE_DATABASE_URL` — Postgres URL for episodes and gateway tables (`postgresql://...`).
   - `MIDPLANE_KEY_PEPPER` — long random string. The default pepper must not be used here.
   - Leave `MIDPLANE_SEED_DEV_KEY` unset or `0`. Do not bake a dev API key into the image.
   - Optional, only to leave the fake model: `MIDPLANE_MODEL_PROVIDER` (`openai` or `anthropic`), `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`, and `MIDPLANE_PRIMARY_MODEL`.
4. After the app is up, mint a real client key with `fly ssh console -C "python -m midplane.gateway.keys mint --client ios-dev"` and give that token to the app through an internal channel, not the repo.
5. An Apple Developer team only when someone installs the iOS app on a device or ships it. The CI compile of the generated Swift package does not need that account.

Local compose credentials (`midplane` / `midplane` on the database, pepper `dev-pepper-not-for-production`) are for the laptop only.

## Related documents

- [README](../../README.md)
- [Mid-plane architecture](../architecture/mid-plane.md)
- [First vertical slice](../architecture/first-slice.md)
- [90-day tracker](../plan/90-day-tracker.md)
