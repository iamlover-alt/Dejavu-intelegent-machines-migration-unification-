import XCTest

@testable import MidplaneKit

final class MockMidplaneClientTests: XCTestCase {
    func testHealthAndCreateRunStayOnTheContract() async throws {
        let client = MockMidplaneClient()
        let health = try await client.healthz()
        XCTAssertEqual(health.status, .ok)

        let created = try await client.createRun(sampleRequest(clientRef: "draft-1"))
        XCTAssertEqual(created.status, .queued)
        XCTAssertFalse(created.runId.isEmpty)

        let run = try await client.getRun(id: created.runId)
        XCTAssertEqual(run.status, .queued)
        XCTAssertEqual(run.taskType, "code_review")
        XCTAssertEqual(run.clientRef, "draft-1")
        XCTAssertNil(run.episodeId)
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
                Components.Schemas.CreateRunRequest(taskType: .codeReview, input: .init(diff: "  "))
            )
            XCTFail("expected validation")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.code, "validation_error")
            XCTAssertEqual(failure.statusCode, 422)
            XCTAssertEqual(failure.details.map(\.field), ["input.diff"])
            XCTAssertEqual(failure.details.map(\.issue), ["Field required"])
        }
        do {
            _ = try await client.createRun(sampleRequest(clientRef: String(repeating: "a", count: 201)))
            XCTFail("expected validation")
        } catch let failure as GatewayFailure {
            XCTAssertEqual(failure.code, "validation_error")
            XCTAssertEqual(failure.details.map(\.field), ["client_ref"])
        }
    }

    func testEventStreamThenEpisode() async throws {
        let client = MockMidplaneClient()
        let created = try await client.createRun(sampleRequest(diff: sampleDiff))
        var names: [String] = []
        let stream = await client.runEvents(id: created.runId, lastEventID: nil)
        for try await event in stream {
            names.append(event.name ?? "")
        }
        XCTAssertEqual(
            names,
            [
                "run.started",
                "node.started", "node.completed",
                "node.started", "node.completed",
                "node.started", "node.completed",
                "node.started", "node.completed",
                "run.completed",
            ]
        )

        let run = try await client.getRun(id: created.runId)
        XCTAssertEqual(run.status, .succeeded)
        let episodeID = try XCTUnwrap(run.episodeId)
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

    func testResumeSkipsKnownIDsAndReplaysWhenTheIDIsUnknown() async throws {
        let client = MockMidplaneClient()
        let created = try await client.createRun(sampleRequest())
        var first: [RunStreamEvent] = []
        for try await event in await client.runEvents(id: created.runId, lastEventID: nil) {
            first.append(event)
        }
        XCTAssertEqual(first.map(\.id), (1 ... first.count).map(String.init))

        var resumed: [String] = []
        for try await event in await client.runEvents(id: created.runId, lastEventID: "4") {
            resumed.append(event.id ?? "")
        }
        XCTAssertEqual(resumed.first, "5")
        XCTAssertEqual(resumed.last, String(first.count))
        XCTAssertFalse(resumed.contains("4"))

        var replayed: [String] = []
        for try await event in await client.runEvents(id: created.runId, lastEventID: "999") {
            replayed.append(event.id ?? "")
        }
        XCTAssertEqual(replayed, first.map { $0.id ?? "" })

        var notAnInteger: [String] = []
        for try await event in await client.runEvents(id: created.runId, lastEventID: "nope") {
            notAnInteger.append(event.id ?? "")
        }
        XCTAssertEqual(notAnInteger, first.map { $0.id ?? "" })
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
        XCTAssertEqual(first.limit, 4)
        XCTAssertEqual(first.data.map(\.title), ["Bot Builder", "Storefront", "Front Desk", "Grok Bot"])
        let cursor = try XCTUnwrap(first.nextCursor)
        let second = try await client.listAgents(limit: 4, cursor: cursor)
        XCTAssertEqual(second.data.first?.title, "Money Desk")
        let whole = try await client.listAgents(limit: nil, cursor: nil)
        XCTAssertEqual(whole.data.count, 10)
        XCTAssertNil(whole.nextCursor)
        XCTAssertEqual(whole.data.last?.name, "ios-studio")
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

    private func sampleRequest(
        diff: String = "+print(\"ok\")\n",
        clientRef: String? = nil
    ) -> Components.Schemas.CreateRunRequest {
        Components.Schemas.CreateRunRequest(
            taskType: .codeReview,
            input: .init(diff: diff, repo: "demo", instructions: "Look for bugs."),
            clientRef: clientRef
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
