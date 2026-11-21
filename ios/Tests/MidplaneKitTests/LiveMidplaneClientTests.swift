import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import HTTPTypes
import OpenAPIRuntime
import XCTest

@testable import MidplaneKit

final class LiveMidplaneClientTests: XCTestCase {
    func testEventsRequestSendsBearerAndRequestIDWithoutLastEventID() throws {
        let request = try LiveMidplaneClient.eventsRequest(
            baseURL: URL(string: "http://localhost:8000")!,
            runID: "run/1",
            apiKey: "secret-token",
            requestID: "req_fixed"
        )
        XCTAssertEqual(request.url?.host, "localhost")
        let absolute = request.url?.absoluteString ?? ""
        XCTAssertTrue(absolute.contains("run%2F1") || absolute.contains("run%2f1"), absolute)
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer secret-token")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Request-ID"), "req_fixed")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Accept"), "text/event-stream")
        XCTAssertNil(request.value(forHTTPHeaderField: "Last-Event-ID"))
        XCTAssertFalse(request.url?.absoluteString.contains("secret-token") == true)
    }

    func testEventsRequestRequiresAKey() {
        XCTAssertThrowsError(
            try LiveMidplaneClient.eventsRequest(
                baseURL: MidplaneConfiguration.localCompose,
                runID: "1",
                apiKey: "  ",
                requestID: "req_fixed"
            )
        ) { error in
            let failure = error as? GatewayFailure
            XCTAssertEqual(failure?.code, "unauthorized")
            XCTAssertEqual(failure?.requestID, "req_fixed")
        }
    }

    func testHealthDoesNotSendTheKeyAndCreateRunAddsClientRef() async throws {
        let transport = ScriptedTransport { request, body in
            if request.path == "/healthz" {
                XCTAssertNil(request.headerFields[.authorization])
                return (200, Data(#"{"status":"ok"}"#.utf8))
            }
            XCTAssertEqual(request.headerFields[.authorization], "Bearer secret-token")
            let json = try JSONSerialization.jsonObject(with: body) as? [String: Any]
            XCTAssertEqual(json?["task_type"] as? String, "code_review")
            XCTAssertEqual(json?["client_ref"] as? String, "draft-1")
            XCTAssertNil(json?["source"])
            XCTAssertNil(json?["model"])
            XCTAssertNil(json?["tools"])
            return (201, Data(#"{"run_id":"run-1","status":"queued"}"#.utf8))
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
            .init(taskType: .codeReview, input: .init(diff: "+print(\"ok\")\n")),
            clientRef: "draft-1"
        )
        XCTAssertEqual(created.runId, "run-1")
        XCTAssertEqual(created.status, .queued)
        let lines = log.lines
        XCTAssertTrue(lines.contains { $0.contains("request_id=") })
        XCTAssertFalse(lines.joined().contains("secret-token"))
    }

    func testGetRunReadsFieldsTheGeneratorDrops() async throws {
        let transport = ScriptedTransport { request, _ in
            XCTAssertTrue(request.path?.contains("/v1/runs/run-1") == true)
            let payload = """
            {"run_id":"run-1","status":"succeeded","result":{"summary":"done"},"episode_id":"episode-1","task_type":"code_review","client_ref":"draft-1","error":null}
            """
            return (200, Data(payload.utf8))
        }
        let client = LiveMidplaneClient(
            keyStore: InMemoryAPIKeyStore(key: "secret-token"),
            transport: transport
        )
        let run = try await client.getRun(id: "run-1")
        XCTAssertEqual(run.response.status, .succeeded)
        XCTAssertEqual(run.episodeID, "episode-1")
        XCTAssertEqual(run.clientRef, "draft-1")
        XCTAssertNil(run.errorMessage)
        XCTAssertTrue(run.resultJSON?.contains("summary") == true)
    }

    func testAgentsCursorRoundTripAndErrorEnvelope() async throws {
        let transport = ScriptedTransport { request, _ in
            if request.path?.contains("cursor=") == true {
                let payload = """
                {"data":[{"name":"money-desk","title":"Money Desk","status":"active"}],"next_cursor":null,"limit":4}
                """
                return (200, Data(payload.utf8))
            }
            let payload = """
            {"error":{"code":"unauthorized","message":"Missing or invalid API key.","request_id":"req_from_body"}}
            """
            return (401, Data(payload.utf8))
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
            XCTAssertTrue(failure.errorDescription?.contains("req_from_body") == true)
        } catch {
            XCTFail("unexpected error \(error)")
        }

        let page = try await client.listAgents(limit: 4, cursor: "cursor token")
        XCTAssertEqual(page.page.data.map(\.name), ["money-desk"])
        XCTAssertNil(page.nextCursor)
        let recorded = await transport.requests
        let path = try XCTUnwrap(recorded.last?.path)
        XCTAssertTrue(path.contains("cursor=cursor%20token") || path.contains("cursor=cursor+token"))
    }

    func testMissingKeyFailsBeforeTheTransport() async {
        let transport = ScriptedTransport { _, _ in
            XCTFail("transport should not be called")
            return (500, Data("{}".utf8))
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
    typealias Handler = @Sendable (HTTPRequest, Data) throws -> (Int, Data)

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
        let (status, payload) = try handler(request, data)
        requests.append(request)
        var headers = HTTPFields()
        headers[HTTPField.Name("X-Request-ID")!] = "req_header"
        headers[.contentType] = "application/json"
        let response = HTTPResponse(status: .init(code: status), headerFields: headers)
        return (response, HTTPBody(payload))
    }
}
