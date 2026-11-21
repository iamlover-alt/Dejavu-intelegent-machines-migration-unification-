import XCTest

@testable import MidplaneKit

final class MockMidplaneClientTests: XCTestCase {
    func testHealthAndCreateRunStayOnTheContract() async throws {
        let client = MockMidplaneClient()
        let health = try await client.healthz()
        XCTAssertEqual(health.status, .ok)

        let created = try await client.createRun(sampleRequest(), clientRef: "draft-1")
        XCTAssertEqual(created.status, .queued)
        XCTAssertFalse(created.runId.isEmpty)

        let run = try await client.getRun(id: created.runId)
        XCTAssertEqual(run.response.status, .queued)
        XCTAssertEqual(run.response.taskType, "code_review")
        XCTAssertEqual(run.clientRef, "draft-1")
        XCTAssertNil(run.episodeID)
        do {
            _ = try await client.getEpisode(id: created.runId)
            XCTFail("episode is written after the stream finishes")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.code, "not_found")
            XCTAssertNotNil(failure.requestID)
        }
    }

    func testCreateRejectsAnEmptyDiffAndALongClientRef() async throws {
        let client = MockMidplaneClient()
        do {
            _ = try await client.createRun(
                Components.Schemas.CreateRunRequest(taskType: .codeReview, input: .init(diff: "  ")),
                clientRef: nil
            )
            XCTFail("expected validation")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.code, "validation_error")
            XCTAssertEqual(failure.statusCode, 422)
        }
        do {
            _ = try await client.createRun(sampleRequest(), clientRef: String(repeating: "a", count: 201))
            XCTFail("expected validation")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.code, "validation_error")
        }
    }

    func testEventStreamThenEpisode() async throws {
        let client = MockMidplaneClient()
        let created = try await client.createRun(sampleRequest(diff: sampleDiff), clientRef: nil)
        var names: [String] = []
        let stream = await client.runEvents(id: created.runId)
        for try await event in stream {
            names.append(event.name ?? "")
        }
        XCTAssertEqual(names, ["node", "node", "node", "node", "status"])

        let run = try await client.getRun(id: created.runId)
        XCTAssertEqual(run.response.status, .succeeded)
        let episodeID = try XCTUnwrap(run.episodeID)
        let episode = try await client.getEpisode(id: episodeID)
        XCTAssertEqual(episode.source, "app")
        XCTAssertEqual(episode.outcome, "success")
        XCTAssertEqual(episode.qualityScore, 0.5)
        XCTAssertEqual(episode.latencyMs, 1280)
        XCTAssertEqual(episode.toolCalls.count, 0)
        let outputs = JSONDisplay.pretty(episode.outputs)
        XCTAssertTrue(outputs.contains("findings"))
        XCTAssertTrue(outputs.contains("block"))
        XCTAssertTrue(JSONDisplay.pretty(episode.trace).contains("episode_writer"))
    }

    func testUnknownRunFailsWithARequestID() async throws {
        let client = MockMidplaneClient()
        do {
            _ = try await client.getRun(id: "missing")
            XCTFail("expected not found")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.statusCode, 404)
            XCTAssertEqual(failure.code, "not_found")
            XCTAssertTrue(failure.requestID?.hasPrefix("req_") == true)
        }
    }

    func testAgentsPageInRosterOrder() async throws {
        let client = MockMidplaneClient()
        let first = try await client.listAgents(limit: 4, cursor: nil)
        XCTAssertEqual(first.page.limit, 4)
        XCTAssertEqual(first.page.data.map(\.title), ["Bot Builder", "Storefront", "Front Desk", "Grok Bot"])
        let cursor = try XCTUnwrap(first.nextCursor)
        let second = try await client.listAgents(limit: 4, cursor: cursor)
        XCTAssertEqual(second.page.data.first?.title, "Money Desk")
        let whole = try await client.listAgents(limit: nil, cursor: nil)
        XCTAssertEqual(whole.page.data.count, 10)
        XCTAssertNil(whole.nextCursor)
        XCTAssertEqual(whole.page.data.last?.name, "ios-studio")
        do {
            _ = try await client.listAgents(limit: 4, cursor: "not-a-cursor")
            XCTFail("expected invalid cursor")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.code, "invalid_cursor")
            XCTAssertEqual(failure.statusCode, 400)
        }
        do {
            _ = try await client.listAgents(limit: 0, cursor: nil)
            XCTFail("expected validation")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.code, "validation_error")
        }
    }

    private func sampleRequest(diff: String = "+print(\"ok\")\n") -> Components.Schemas.CreateRunRequest {
        Components.Schemas.CreateRunRequest(
            taskType: .codeReview,
            input: .init(diff: diff, repo: "demo", instructions: "Look for bugs.")
        )
    }

    private var sampleDiff: String {
        """
        diff --git a/hello.py b/hello.py
        --- a/hello.py
        +++ b/hello.py
        @@ -1 +1,2 @@
        -print("hi")
        +print("hi")
        +password = "hunter2"
        """
    }
}
