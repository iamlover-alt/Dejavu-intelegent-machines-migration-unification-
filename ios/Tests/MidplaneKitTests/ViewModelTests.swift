import XCTest

@testable import MidplaneKit

final class ViewModelTests: XCTestCase {
    func testConnectionTransitions() async {
        let checking = ConnectionReducer.reduce(.unknown, .checkStarted)
        XCTAssertEqual(checking.phase, .checking)
        let reachable = ConnectionReducer.reduce(checking, .succeeded)
        XCTAssertEqual(reachable.phase, .reachable)

        let failure = GatewayFailure(statusCode: 500, code: "internal_error", message: "The request failed.", requestID: "req_abc")
        let failed = ConnectionReducer.reduce(checking, .failed(failure))
        XCTAssertEqual(failed.phase, .failed)
        XCTAssertEqual(failed.failure?.requestID, "req_abc")

        let client = MockMidplaneClient()
        let actions = await ConnectionLoader.refresh(client)
        XCTAssertEqual(actions, [.checkStarted, .succeeded])
        XCTAssertEqual(actions.reduce(ConnectionState.unknown, ConnectionReducer.reduce).phase, .reachable)
    }

    func testCreateRunTransitions() async throws {
        var state = CreateRunState.empty
        state = CreateRunReducer.reduce(state, .edit(diff: "", repo: "local", instructions: "Review this diff.", clientRef: ""))
        let empty = await CreateRunLoader.submit(state, client: MockMidplaneClient())
        let rejected = CreateRunReducer.fold(empty, from: state)
        XCTAssertEqual(rejected.phase, .failed)
        XCTAssertNil(rejected.failure?.requestID)

        state = CreateRunReducer.reduce(
            state,
            .edit(diff: "+print(\"ok\")\n", repo: "demo", instructions: "Look for bugs.", clientRef: "draft-1")
        )
        let actions = await CreateRunLoader.submit(state, client: MockMidplaneClient())
        XCTAssertEqual(actions.first, .submitStarted)
        let submitted = CreateRunReducer.fold(actions, from: state)
        guard case .created(let runID, let status) = submitted.phase else {
            return XCTFail("expected a created run")
        }
        XCTAssertEqual(status, "queued")
        XCTAssertFalse(runID.isEmpty)
    }

    func testRunDetailCollectsNodeEventsThenEpisode() async throws {
        let client = MockMidplaneClient()
        let created = try await client.createRun(
            .init(taskType: .codeReview, input: .init(diff: "+print(\"ok\")\n"))
        )
        var actions: [RunDetailAction] = []
        for await action in RunDetailLoader.load(runID: created.runId, client: client) {
            actions.append(action)
        }
        let state = RunDetailReducer.fold(actions)
        XCTAssertEqual(state.phase, .finished)
        XCTAssertEqual(state.status, "succeeded")
        XCTAssertEqual(state.events.first?.name, "run.started")
        XCTAssertEqual(state.events.last?.name, "run.completed")
        let completedNodes = state.events.compactMap { event -> String? in
            guard event.name == "node.completed" else { return nil }
            return nodeName(event.data)
        }
        XCTAssertEqual(completedNodes, ["supervisor", "router", "code_review", "episode_writer"])
        XCTAssertEqual(state.episodeID, created.runId)
        XCTAssertNil(state.failure)
    }

    func testRunDetailFailureKeepsTheRequestID() async {
        var actions: [RunDetailAction] = []
        for await action in RunDetailLoader.load(runID: "missing", client: MockMidplaneClient()) {
            actions.append(action)
        }
        let state = RunDetailReducer.fold(actions)
        XCTAssertEqual(state.phase, .failed)
        XCTAssertEqual(state.failure?.code, "not_found")
        XCTAssertNotNil(state.failure?.requestID)
        XCTAssertEqual(actions.first, .loadStarted(resuming: false))
    }

    func testAgentsPagingAppendsAndClearsTheCursor() async throws {
        let client = MockMidplaneClient()
        var state = AgentsState.empty
        state = AgentsReducer.loading(state)
        XCTAssertTrue(state.isLoading)
        let first = try await client.listAgents(limit: 4, cursor: nil)
        state = AgentsReducer.apply(state, page: first, appending: false)
        XCTAssertEqual(state.rows.count, 4)
        XCTAssertNotNil(state.nextCursor)
        let second = try await client.listAgents(limit: 4, cursor: state.nextCursor)
        state = AgentsReducer.apply(state, page: second, appending: true)
        XCTAssertEqual(state.rows.count, 8)
        XCTAssertEqual(state.rows[4].title, "Money Desk")
        let third = try await client.listAgents(limit: 4, cursor: state.nextCursor)
        state = AgentsReducer.apply(state, page: third, appending: true)
        XCTAssertEqual(state.rows.count, 10)
        XCTAssertNil(state.nextCursor)
        XCTAssertFalse(state.isLoading)

        let invalid = GatewayFailure(statusCode: 400, code: "invalid_cursor", message: "The cursor is not valid.", requestID: "req_page")
        let failed = AgentsReducer.failed(state, invalid)
        XCTAssertEqual(failed.rows.count, 10)
        XCTAssertEqual(failed.failure?.requestID, "req_page")
    }

    func testEpisodeDetailShowsTheRecordedFields() async throws {
        let client = MockMidplaneClient()
        let created = try await client.createRun(
            .init(taskType: .codeReview, input: .init(diff: "+print(\"ok\")\n"))
        )
        for try await _ in await client.runEvents(id: created.runId, lastEventID: nil) {}
        let actions = await EpisodeDetailLoader.load(id: created.runId, client: client)
        let state = EpisodeDetailReducer.fold(actions)
        XCTAssertEqual(state.phase, .loaded)
        XCTAssertEqual(state.source, "app")
        XCTAssertEqual(state.outcome, "success")
        XCTAssertEqual(state.qualityScore, "0.5")
        XCTAssertEqual(state.latencyMS, 1280)
        XCTAssertTrue(state.outputs?.contains("findings") == true)
        XCTAssertTrue(state.cost?.contains("total_usd") == true)
        XCTAssertTrue(state.trace?.contains("code_review") == true)
    }

    func testDebugSettingsKeepsTheBaseURLWhenTheModeChanges() {
        var state = DebugSettingsState(mode: .mock, baseURL: "http://localhost:8000", hasAPIKey: false)
        state = DebugSettingsReducer.reduce(state, .setMode(.live))
        state = DebugSettingsReducer.reduce(state, .apiKeyStored)
        XCTAssertEqual(state.mode, .live)
        XCTAssertEqual(state.baseURL, "http://localhost:8000")
        XCTAssertTrue(state.hasAPIKey)
        state = DebugSettingsReducer.reduce(state, .apiKeyCleared)
        XCTAssertFalse(state.hasAPIKey)
        XCTAssertEqual(state.baseURL, "http://localhost:8000")
    }

    func testResumeAppendsNewerIDsAndReplacesTheTimelineOnAFullReplay() {
        let first = RunStreamEvent(id: "1", name: "run.started", data: #"{"at":"t","run_id":"r","task_type":"code_review"}"#)
        let second = RunStreamEvent(id: "2", name: "node.started", data: #"{"at":"t","node":"supervisor"}"#)
        let third = RunStreamEvent(id: "3", name: "node.completed", data: #"{"at":"t","node":"supervisor","status":"completed"}"#)
        var state = RunDetailReducer.fold([.event(first), .event(second)])
        state = RunDetailReducer.reduce(state, .loadStarted(resuming: true))
        state = RunDetailReducer.reduce(state, .event(third))
        XCTAssertEqual(state.events.map(\.id), ["1", "2", "3"])
        XCTAssertFalse(state.expectingResume)

        state = RunDetailReducer.reduce(state, .loadStarted(resuming: true))
        state = RunDetailReducer.reduce(state, .event(first))
        state = RunDetailReducer.reduce(state, .event(second))
        XCTAssertEqual(state.events.map(\.id), ["1", "2"])
    }

    func testFactorySwitchesBetweenMockAndLive() {
        let store = InMemoryAPIKeyStore()
        let mock = MidplaneClientFactory.make(mode: .mock, baseURL: MidplaneConfiguration.localCompose, keyStore: store)
        let live = MidplaneClientFactory.make(mode: .live, baseURL: MidplaneConfiguration.localCompose, keyStore: store)
        XCTAssertTrue(mock is MockMidplaneClient)
        XCTAssertTrue(live is LiveMidplaneClient)
    }

    private func nodeName(_ data: String) -> String? {
        guard let raw = data.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: raw) as? [String: Any]
        else {
            return nil
        }
        return object["node"] as? String
    }

}
