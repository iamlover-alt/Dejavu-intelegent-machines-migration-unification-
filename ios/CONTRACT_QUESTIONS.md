# Contract questions

`openapi/midplane-v0.yaml` is the source of truth. This file only lists places where that file does not say what the iOS client needs, or where the generated Swift client cannot represent a field the YAML already defines. Nothing here is a change to the contract.

## 1. Run events are not described as a stream

`GET /v1/runs/{run_id}/events` declares a `200` response of `application/json` with `schema: {}`. It does not name `text/event-stream`, event names, the JSON in `data`, or `id` lines.

The running server emits HTML Server-Sent Events. Each frame is `id`, `event`, and one JSON `data` line. The frames observed today are:

- `event: node` with `{"node","status","at"}`
- `event: status` with `{"status","episode_id","error"}`

The app parses that framing so the timeline can show node progress, and it reads `episode_id` off a `status` frame when the key is present. That is the server's current stream, not a schema in the YAML.

Proposal: document the response as `text/event-stream` and schema the two event payloads. The generated client can then stream them. Until then, `LiveMidplaneClient.runEvents` uses `URLSession` instead of the generated operation, because that operation expects a single JSON value.

## 2. Reconnection is not defined

The YAML does not mention `Last-Event-ID`, durable event ids, or a retry interval. The server's `id` values are a per-connection counter, and the handler does not read `Last-Event-ID`.

The client does not send `Last-Event-ID`. Retry opens `GET /v1/runs/{run_id}/events` again from the start. The HTML `retry` field is parsed if it appears and is otherwise ignored.

Proposal: say that resume is unsupported, or define a durable id the server will honor.

## 3. Nullable fields disappear from the generated Swift client

swift-openapi-generator 1.14.4 warns `Schema "null" is not supported` and drops properties written as `anyOf: [<type>, {type: "null"}]`. The generated types are missing:

| YAML field | Where |
| --- | --- |
| `client_ref` | `CreateRunRequest` |
| `cursor` | `GET /v1/agents` query |
| `next_cursor` | `AgentPage` |
| `result`, `episode_id`, `client_ref`, `error` | `RunStatusResponse` |

The app still sends `client_ref` and `cursor`, and still reads `next_cursor`, `episode_id`, `result`, `client_ref`, and `error`, by wrapping the generated client. `AgentList` and `RunRecord` exist only to hold those dropped fields next to the generated values.

Proposal: keep the same JSON and write nullability in a form the generator retains, for example `type: ["string", "null"]`, so the next `scripts/generate-swift-client.sh` run exposes the fields.

## 4. Error responses are prose, and 422 points at a different body

`info.description` says every non-2xx body is `{"error": {"code", "message", "request_id"}}`. The operations do not declare that schema. The only error response in the operations is `422` as `HTTPValidationError` (`detail`).

The server returns the error envelope for 401, 404, 400, 422, and 429. The app decodes that envelope from any non-2xx body and shows `request_id` in the error UI and logs.

Proposal: add the error schema and the status codes the server already returns, and stop advertising `HTTPValidationError` for the live 422 body.

## 5. `X-Request-ID` and the rate-limit headers are not header parameters

The description says every response carries `X-Request-ID`, that clients may send it, and that responses include `X-RateLimit-Limit` and `X-RateLimit-Remaining`. None of those headers are parameters on the operations, so the generated client does not round-trip them. `Retry-After` is not in the YAML.

The app sends `X-Request-ID` on every call and logs the echoed value. On failure it prefers `error.request_id` from the body.

Proposal: declare the request header, the response header, and the rate-limit headers on the operations.
