# Dejavú iOS

SwiftUI client for the Mid-plane Gateway API v0. It starts a `code_review` run, follows the run's event stream, and reads the episode the gateway writes. The agent runtime stays on the mid-plane: the app does not choose a model or a tool list, and it does not send `source`.

The app targets iOS 17. The generated gateway package requires Swift 6.

## Layout

| Path | Role |
| --- | --- |
| `ios/Package.swift` | `MidplaneKit`: protocol, mock, live client, view models, tests |
| `ios/App/Dejavu.xcodeproj` | SwiftUI app |
| `clients/swift` | Generated `MidplaneGateway` client. The app depends on it as a local package. |
| `openapi/midplane-v0.yaml` | Contract. Do not edit it from the iOS app. |

`MidplaneClient` is the app boundary. `LiveMidplaneClient` calls the generated client, including the `text/event-stream` body. `MockMidplaneClient` implements the same protocol with fixture data and no network. The generator returns the event stream as bytes, so the app parses frames locally and decodes `RunStartedData`, `NodeStartedData`, `NodeCompletedData`, `RunCompletedData`, and `RunFailedData`. Reconnect sends `Last-Event-ID`. An unknown id replays the whole log.

Errors are `GatewayErrorEnvelope`: `error.code`, `error.message`, `error.request_id`, and `error.details` on `validation_error`. The client sends `X-Request-ID` and shows the echoed id. Authenticated responses expose `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset`. A 429 keeps `Retry-After` and does not send another create until that wait has passed. This matches gateway `0169be4`.

`clients/swift` generates its symbols as `public` so this package can import them. The OpenAPI file is unchanged.

## Open and run

Requirements: Xcode 16 or newer, iOS 17 simulator.

```bash
open ios/App/Dejavu.xcodeproj
```

The scheme is `Dejavu`. The default mode is **Mock**, so the app runs without a gateway and without an API key. Debug settings (the sliders button) switch between Mock and Live.

Live mode:

```bash
docker compose up --build
```

Compose listens on port 8000 and prints a dev key as `dev api key:`. Paste that into Debug settings. The default base URL is `http://localhost:8000`. The key is stored in the Keychain and is not part of the repo. Mint another with:

```bash
python -m midplane.gateway.keys mint --client ios-dev
```

`GET /healthz` is open. Every `/v1` call sends `Authorization: Bearer`.

The agents list asks for 4 at a time and follows `next_cursor`. Recent runs on the home screen are this session only. The contract has no list-runs route.

This project does not upload to TestFlight or the App Store.

## Test

From the repo root, with Swift 6:

```bash
swift test --package-path ios
```

That builds `MidplaneKit` and `clients/swift` and runs the package tests: the mock client, the SSE line parser, live request headers against a scripted transport, and view-model state transitions. The SwiftUI target is built by Xcode, not by `swift test`.

## CI

`.github/workflows/ios.yml` runs on a macOS runner when `ios/**`, the workflow, `clients/swift/**`, or `openapi/midplane-v0.yaml` changes. It runs `swift test` and an iOS Simulator `xcodebuild`. It does not modify the Python test workflow.
