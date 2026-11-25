# Contract questions

`openapi/midplane-v0.yaml` is the source of truth. The event stream, `Last-Event-ID` resume, and nullable fields are specified there, and the generated Swift client now keeps those fields. This file only lists what that document still does not declare as response schemas.

## 1. Error responses are prose, and 422 points at a different body

`info.description` says every non-2xx body is `{"error": {"code", "message", "request_id"}}`. The operations do not declare that schema. The only error response in the operations is `422` as `HTTPValidationError` (`detail`).

The server returns the error envelope for 401, 404, 400, 422, and 429. The app decodes that envelope from any non-2xx body and shows `request_id` in the error UI and logs.

Proposal: add the error schema and the status codes the server already returns, and stop advertising `HTTPValidationError` for the live 422 body.

## 2. `X-Request-ID` and the rate-limit headers are not header parameters

The description says every response carries `X-Request-ID`, that clients may send it, and that responses include `X-RateLimit-Limit` and `X-RateLimit-Remaining`. None of those headers are parameters on the operations, so the generated client does not round-trip them. `Retry-After` is not in the YAML.

The app sends `X-Request-ID` on every call and logs the echoed value. On failure it prefers `error.request_id` from the body.

Proposal: declare the request header, the response header, and the rate-limit headers on the operations.
