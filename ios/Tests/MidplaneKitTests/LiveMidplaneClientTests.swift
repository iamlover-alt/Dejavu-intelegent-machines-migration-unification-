import Foundation
import HTTPTypes
import OpenAPIRuntime
import XCTest

@testable import MidplaneKit

final class LiveMidplaneClientTests: XCTestCase {
    func testHealthDoesNotSendTheKeyAndCreateRunEncodesClientRef() async throws {
        let transport = ScriptedTransport { request, body in
            if request.path == "/healthz" {
                XCTAssertNil(request.headerFields[.authorization])
                return (200, Data(#"{"status":"ok"}"#.utf8), "application/json")
            }
            XCTAssertEqual(request.headerFields[.authorization], "Bearer secret-token")
            let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(json?["task_type"] as? String, "code_review")
            XCTAssertEqual(json?["client_ref"] as? String, "draft-1")
            XCTAssertNil(json?["source"])
            XCTAssertNil(json?["model"])
            XCTAssertNil(json?["tools"])
            return (201, Data(#"{"run_id":"run-1","status":"queued"}"#.utf8), "application/json")
        }
        let log = CaptureLog()
        let client = LiveMidplaneClient(
            keyStore: InMemoryAPIKeyStore(key: "secret-token"),
            log: log,
            transport: transport
        )
        let health = try await client.healthz()
        XCTAssertEqual(health.status, .ok)
        let created = try await client.createRun(
            .init(taskType: .codeReview, input: .init(diff: "+print(\"ok\")\n"), clientRef: "draft-1")
        )
        XCTAssertEqual(created.runId, "run-1")
        XCTAssertEqual(created.status, .queued)
        let lines = log.lines
        XCTAssertTrue(lines.contains { $0.contains("request_id=") })
        XCTAssertFalse(lines.joined().contains("secret-token"))
        let recorded = await transport.requests
        let sent = recorded.first?.headerFields[HTTPField.Name("X-Request-ID")!]
        XCTAssertTrue(sent?.hasPrefix("req_") == true)
        XCTAssertTrue(lines.contains { $0.contains("request_id=\(sent ?? "")") })
        let rate = await client.latestRateLimit()
        XCTAssertEqual(rate, RateLimit(limit: 60, remaining: 59, resetSeconds: 30))
    }

    func testErrorEnvelopeDetailsRetryAfterAndRequestIDEcho() async throws {
        let transport = ScriptedTransport { request, _ in
            let sent = request.headerFields[HTTPField.Name("X-Request-ID")!] ?? "missing"
            if request.path == "/v1/runs" {
                let payload = """
                {"error":{"code":"validation_error","message":"Request failed validation.","request_id":"\(sent)","details":[{"field":"input.diff","issue":"Field required"}]}}
                """
                return (422, Data(payload.utf8), "application/json")
            }
            let payload = """
            {"error":{"code":"rate_limited","message":"Too many requests.","request_id":"\(sent)"}}
            """
            return (429, Data(payload.utf8), "application/json")
        }
        let client = LiveMidplaneClient(
            keyStore: InMemoryAPIKeyStore(key: "secret-token"),
            transport: transport
        )
        let bare = try JSONDecoder().decode(
            Components.Schemas.GatewayErrorEnvelope.self,
            from: Data(#"{"error":{"code":"not_found","message":"Run not found.","request_id":"req_plain"}}"#.utf8)
        )
        let plain = GatewayFailure.decoded(statusCode: 404, envelope: bare)
        XCTAssertEqual(plain.code, "not_found")
        XCTAssertEqual(plain.requestID, "req_plain")
        XCTAssertTrue(plain.details.isEmpty)

        do {
            _ = try await client.createRun(.init(taskType: .codeReview, input: .init(diff: "+x\n")))
            XCTFail("expected validation")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.statusCode, 422)
            XCTAssertEqual(failure.code, "validation_error")
            XCTAssertEqual(failure.details, [FieldIssue(field: "input.diff", issue: "Field required")])
            let sent = await transport.requests.first?.headerFields[HTTPField.Name("X-Request-ID")!]
            XCTAssertEqual(failure.requestID, sent)
            XCTAssertTrue(failure.requestID?.hasPrefix("req_") == true)
        } catch {
            XCTFail("unexpected error \(error)")
        }

        do {
            _ = try await client.listAgents(limit: 4, cursor: nil)
            XCTFail("expected rate limit")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.statusCode, 429)
            XCTAssertEqual(failure.code, "rate_limited")
            XCTAssertEqual(failure.retryAfterSeconds, 12)
            XCTAssertEqual(failure.rateLimit, RateLimit(limit: 60, remaining: 59, resetSeconds: 30))
            let sent = await transport.requests.last?.headerFields[HTTPField.Name("X-Request-ID")!]
            XCTAssertEqual(failure.requestID, sent)
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testGetRunDecodesNullableFields() async throws {
        let payload = """
        {"run_id":"run-1","status":"succeeded","result":{"summary":"done"},"episode_id":"episode-1","task_type":"code_review","client_ref":"draft-1","error":null}
        """
        let transport = ScriptedTransport { request, _ in
            XCTAssertTrue(request.path?.contains("/v1/runs/run-1") == true)
            return (200, Data(payload.utf8), "application/json")
        }
        let client = LiveMidplaneClient(
            keyStore: InMemoryAPIKeyStore(key: "secret-token"),
            transport: transport
        )
        let run = try await client.getRun(id: "run-1")
        XCTAssertEqual(run.status, .succeeded)
        XCTAssertEqual(run.episodeId, "episode-1")
        XCTAssertEqual(run.clientRef, "draft-1")
        XCTAssertNil(run.error)
        XCTAssertNotNil(run.result)
    }

    func testAgentsCursorIsAQueryParameter() async throws {
        let transport = ScriptedTransport { request, _ in
            if request.path?.contains("cursor=") == true {
                let payload = """
                {"data":[{"name":"money-desk","title":"Money Desk","status":"active"}],"next_cursor":null,"limit":4}
                """
                return (200, Data(payload.utf8), "application/json")
            }
            let payload = """
            {"error":{"code":"unauthorized","message":"Missing or invalid API key.","request_id":"req_from_body"}}
            """
            return (401, Data(payload.utf8), "application/json")
        }
        let client = LiveMidplaneClient(
            keyStore: InMemoryAPIKeyStore(key: "secret-token"),
            transport: transport
        )
        do {
            _ = try await client.listAgents(limit: 4, cursor: nil)
            XCTFail("expected unauthorized")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.statusCode, 401)
            XCTAssertEqual(failure.code, "unauthorized")
            XCTAssertEqual(failure.requestID, "req_from_body")
        } catch {
            XCTFail("unexpected error \(error)")
        }

        let page = try await client.listAgents(limit: 4, cursor: "cursor token")
        XCTAssertEqual(page.data.map(\.name), ["money-desk"])
        XCTAssertNil(page.nextCursor)
        let recorded = await transport.requests
        let path = try XCTUnwrap(recorded.last?.path)
        XCTAssertTrue(path.contains("cursor=cursor%20token") || path.contains("cursor=cursor+token"))
    }

    func testRunEventsSendsLastEventIDAndDecodesTheTypedPayload() async throws {
        let sse = "id: 5\nevent: run.completed\ndata: {\"at\":\"2026-11-25T00:00:00Z\",\"episode_id\":\"ep-1\",\"run_id\":\"run-1\",\"status\":\"succeeded\"}\n\n"
        let transport = ScriptedTransport { request, _ in
            return (200, Data(sse.utf8), "text/event-stream")
        }
        let client = LiveMidplaneClient(keyStore: InMemoryAPIKeyStore(key: "secret-token"), transport: transport)
        var events: [RunStreamEvent] = []
        for try await event in await client.runEvents(id: "run-1", lastEventID: "4") {
            events.append(event)
        }
        let recorded = await transport.requests
        let header = recorded.first?.headerFields[HTTPField.Name("Last-Event-ID")!]
        XCTAssertEqual(events.map(\.id), ["5"], "events=\(events) header=\(String(describing: header))")
        let frame = try XCTUnwrap(events.first)
        let decoded = try XCTUnwrap(RunEventDecoder.decode(name: frame.name, data: frame.data))
        guard case .runCompleted(let payload) = decoded else {
            return XCTFail("expected run.completed")
        }
        XCTAssertEqual(payload.episodeId, "ep-1")
        XCTAssertEqual(payload.status, .succeeded)
    }

    func testMissingKeyFailsBeforeTheTransport() async {
        let transport = ScriptedTransport { _, _ in
            XCTFail("transport should not be called")
            return (500, Data("{}".utf8), "application/json")
        }
        let client = LiveMidplaneClient(keyStore: InMemoryAPIKeyStore(), transport: transport)
        do {
            _ = try await client.listAgents(limit: nil, cursor: nil)
            XCTFail("expected unauthorized")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.code, "unauthorized")
            XCTAssertNotNil(failure.requestID)
        } catch {
            XCTFail("unexpected error \(error)")
        }
        let count = await transport.requests.count
        XCTAssertEqual(count, 0)
    }
}

private final class CaptureLog: MidplaneLog, @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String] = []
    var lines: [String] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }

    func log(_ line: String) {
        lock.lock()
        storage.append(line)
        lock.unlock()
    }
}

private actor ScriptedTransport: ClientTransport {
    typealias Handler = @Sendable (HTTPRequest, Data) throws -> (Int, Data, String)

    private let handler: Handler
    private(set) var requests: [HTTPRequest] = []

    init(handler: @escaping Handler) {
        self.handler = handler
    }

    func send(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        let data = try await Data(collecting: body ?? HTTPBody(), upTo: 1_000_000)
        let (status, payload, contentType) = try handler(request, data)
        requests.append(request)
        var headers = HTTPFields()
        let requestID = request.headerFields[HTTPField.Name("X-Request-ID")!] ?? "req_header"
        headers[HTTPField.Name("X-Request-ID")!] = requestID
        if operationID != Operations.HealthzHealthzGet.id {
            headers[HTTPField.Name("RateLimit-Limit")!] = "60"
            headers[HTTPField.Name("RateLimit-Remaining")!] = "59"
            headers[HTTPField.Name("RateLimit-Reset")!] = "30"
        }
        if status == 429 {
            headers[HTTPField.Name("Retry-After")!] = "12"
        }
        headers[.contentType] = contentType
        let response = HTTPResponse(status: .init(code: status), headerFields: headers)
        return (response, HTTPBody(payload))
    }
}
