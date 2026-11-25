import Foundation
import MidplaneGateway

public struct ConnectionState: Equatable, Sendable {
    public enum Phase: Equatable, Sendable {
        case unknown
        case checking
        case reachable
        case failed
    }

    public var phase: Phase
    public var failure: GatewayFailure?

    public static let unknown = ConnectionState(phase: .unknown, failure: nil)
}

public enum ConnectionAction: Equatable, Sendable {
    case checkStarted
    case succeeded
    case failed(GatewayFailure)
}

public enum ConnectionReducer {
    public static func reduce(_ state: ConnectionState, _ action: ConnectionAction) -> ConnectionState {
        switch action {
        case .checkStarted:
            return ConnectionState(phase: .checking, failure: nil)
        case .succeeded:
            return ConnectionState(phase: .reachable, failure: nil)
        case .failed(let failure):
            return ConnectionState(phase: .failed, failure: failure)
        }
    }
}

public enum ConnectionLoader {
    public static func refresh(_ client: any MidplaneClient) async -> [ConnectionAction] {
        var actions: [ConnectionAction] = [.checkStarted]
        do {
            _ = try await client.healthz()
            actions.append(.succeeded)
        } catch let failure as GatewayFailure {
            actions.append(.failed(failure))
        } catch {
            actions.append(
                .failed(GatewayFailure(statusCode: nil, code: nil, message: error.localizedDescription, requestID: nil))
            )
        }
        return actions
    }
}

public struct CreateRunState: Equatable, Sendable {
    public enum Phase: Equatable, Sendable {
        case editing
        case submitting
        case created(runID: String, status: String)
        case failed
    }

    public var diff: String
    public var repo: String
    public var instructions: String
    public var clientRef: String
    public var phase: Phase
    public var failure: GatewayFailure?

    public static let empty = CreateRunState(
        diff: "",
        repo: "local",
        instructions: "Review this diff.",
        clientRef: "",
        phase: .editing,
        failure: nil
    )
}

public enum CreateRunAction: Equatable, Sendable {
    case edit(diff: String, repo: String, instructions: String, clientRef: String)
    case submitStarted
    case created(runID: String, status: String)
    case failed(GatewayFailure)
}

public enum CreateRunReducer {
    public static func reduce(_ state: CreateRunState, _ action: CreateRunAction) -> CreateRunState {
        var state = state
        switch action {
        case .edit(let diff, let repo, let instructions, let clientRef):
            state.diff = diff
            state.repo = repo
            state.instructions = instructions
            state.clientRef = clientRef
            if state.phase == .failed {
                state.phase = .editing
                state.failure = nil
            }
        case .submitStarted:
            state.phase = .submitting
            state.failure = nil
        case .created(let runID, let status):
            state.phase = .created(runID: runID, status: status)
            state.failure = nil
        case .failed(let failure):
            state.phase = .failed
            state.failure = failure
        }
        return state
    }

    public static func fold(_ actions: [CreateRunAction], from state: CreateRunState = .empty) -> CreateRunState {
        actions.reduce(state, reduce)
    }
}

public enum CreateRunLoader {
    public static func submit(_ state: CreateRunState, client: any MidplaneClient) async -> [CreateRunAction] {
        let diff = state.diff.trimmingCharacters(in: .whitespacesAndNewlines)
        if diff.isEmpty {
            return [
                .failed(
                    GatewayFailure(
                        statusCode: nil,
                        code: nil,
                        message: "Enter a unified diff to review.",
                        requestID: nil
                    )
                )
            ]
        }
        var actions: [CreateRunAction] = [.submitStarted]
        do {
            let clientRef = state.clientRef.trimmingCharacters(in: .whitespacesAndNewlines)
            let created = try await client.createRun(
                Components.Schemas.CreateRunRequest(
                    taskType: .codeReview,
                    input: Components.Schemas.RunInput(
                        diff: state.diff,
                        repo: state.repo.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            ? nil : state.repo,
                        instructions: state.instructions.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            ? nil : state.instructions
                    ),
                    clientRef: clientRef.isEmpty ? nil : clientRef
                )
            )
            actions.append(.created(runID: created.runId, status: created.status.rawValue))
        } catch let failure as GatewayFailure {
            actions.append(.failed(failure))
        } catch {
            actions.append(
                .failed(GatewayFailure(statusCode: nil, code: nil, message: error.localizedDescription, requestID: nil))
            )
        }
        return actions
    }
}

public struct RunDetailState: Equatable, Sendable {
    public enum Phase: Equatable, Sendable {
        case idle
        case loading
        case streaming
        case finished
        case failed
    }

    public var phase: Phase
    public var status: String?
    public var taskType: String?
    public var clientRef: String?
    public var resultJSON: String?
    public var runError: String?
    public var episodeID: String?
    public var events: [RunStreamEvent]
    public var failure: GatewayFailure?
    /// The next event decides whether a reconnect appends or replaces the timeline.
    public var expectingResume: Bool

    public static let idle = RunDetailState(
        phase: .idle,
        status: nil,
        taskType: nil,
        clientRef: nil,
        resultJSON: nil,
        runError: nil,
        episodeID: nil,
        events: [],
        failure: nil,
        expectingResume: false
    )
}

public enum RunDetailAction: Equatable, Sendable {
    case loadStarted(resuming: Bool)
    case snapshot(Components.Schemas.RunStatusResponse)
    case event(RunStreamEvent)
    case streamEnded
    case failed(GatewayFailure)
}

public enum RunDetailReducer {
    public static func reduce(_ state: RunDetailState, _ action: RunDetailAction) -> RunDetailState {
        var state = state
        switch action {
        case .loadStarted(let resuming):
            state.failure = nil
            if resuming {
                state.phase = .streaming
                state.expectingResume = true
            } else {
                state.phase = .loading
                state.events = []
                state.expectingResume = false
            }
        case .snapshot(let record):
            state.status = record.status.rawValue
            state.taskType = record.taskType
            state.clientRef = record.clientRef
            state.resultJSON = record.result.map { JSONDisplay.pretty($0) }
            state.runError = record.error
            if let episodeID = record.episodeId {
                state.episodeID = episodeID
            }
            if state.phase == .loading {
                state.phase = .streaming
            }
        case .event(let event):
            if state.expectingResume {
                state.expectingResume = false
                let incoming = event.id.flatMap(Int.init)
                let previous = state.events.compactMap { $0.id.flatMap(Int.init) }.max()
                if let incoming, let previous, incoming > previous {
                    state.events.append(event)
                } else {
                    state.events = [event]
                }
            } else {
                state.events.append(event)
            }
            state.phase = .streaming
            apply(event, to: &state)
        case .streamEnded:
            state.phase = .finished
        case .failed(let failure):
            state.phase = .failed
            state.failure = failure
        }
        return state
    }

    public static func fold(_ actions: [RunDetailAction]) -> RunDetailState {
        actions.reduce(.idle, reduce)
    }

    private static func apply(_ event: RunStreamEvent, to state: inout RunDetailState) {
        switch RunEventDecoder.decode(name: event.name, data: event.data) {
        case .runStarted, .nodeStarted, .nodeCompleted:
            if state.status != "succeeded" && state.status != "failed" {
                state.status = "running"
            }
        case .runCompleted(let payload):
            state.status = "succeeded"
            state.episodeID = payload.episodeId ?? state.episodeID
        case .runFailed(let payload):
            state.status = "failed"
            state.episodeID = payload.episodeId ?? state.episodeID
            state.runError = payload.error ?? state.runError
        case nil:
            break
        }
    }
}

public enum RunDetailLoader {
    public static func load(
        runID: String,
        client: any MidplaneClient,
        lastEventID: String? = nil
    ) -> AsyncStream<RunDetailAction> {
        AsyncStream { continuation in
            let task = Task {
                continuation.yield(.loadStarted(resuming: lastEventID != nil))
                do {
                    let record = try await client.getRun(id: runID)
                    continuation.yield(.snapshot(record))
                    let stream = await client.runEvents(id: runID, lastEventID: lastEventID)
                    for try await event in stream {
                        continuation.yield(.event(event))
                    }
                    let refreshed = try await client.getRun(id: runID)
                    continuation.yield(.snapshot(refreshed))
                    continuation.yield(.streamEnded)
                } catch let failure as GatewayFailure {
                    continuation.yield(.failed(failure))
                } catch {
                    continuation.yield(
                        .failed(
                            GatewayFailure(
                                statusCode: nil,
                                code: nil,
                                message: error.localizedDescription,
                                requestID: nil
                            )
                        )
                    )
                }
                continuation.finish()
            }
            continuation.onTermination = { @Sendable _ in
                task.cancel()
            }
        }
    }
}

public struct AgentsState: Equatable, Sendable {
    public struct Row: Equatable, Sendable, Identifiable {
        public var name: String
        public var title: String
        public var status: String
        public var id: String { name }

        public init(name: String, title: String, status: String) {
            self.name = name
            self.title = title
            self.status = status
        }
    }

    public var rows: [Row]
    public var nextCursor: String?
    public var isLoading: Bool
    public var failure: GatewayFailure?

    public static let empty = AgentsState(rows: [], nextCursor: nil, isLoading: false, failure: nil)
}

public enum AgentsReducer {
    public static func loading(_ state: AgentsState) -> AgentsState {
        var state = state
        state.isLoading = true
        state.failure = nil
        return state
    }

    public static func failed(_ state: AgentsState, _ failure: GatewayFailure) -> AgentsState {
        var state = state
        state.isLoading = false
        state.failure = failure
        return state
    }

    public static func apply(_ state: AgentsState, page: Components.Schemas.AgentPage, appending: Bool) -> AgentsState {
        var state = state
        let rows = page.data.map { AgentsState.Row(name: $0.name, title: $0.title, status: $0.status) }
        state.rows = appending ? state.rows + rows : rows
        state.nextCursor = page.nextCursor
        state.isLoading = false
        state.failure = nil
        return state
    }
}

public struct EpisodeDetailState: Equatable, Sendable {
    public enum Phase: Equatable, Sendable {
        case idle
        case loading
        case loaded
        case failed
    }

    public var phase: Phase
    public var episodeID: String?
    public var source: String?
    public var outcome: String?
    public var qualityScore: String?
    public var latencyMS: Int?
    public var outputs: String?
    public var cost: String?
    public var trace: String?
    public var inputs: String?
    public var plan: String?
    public var toolCalls: String?
    public var modelChoices: String?
    public var failure: GatewayFailure?

    public static let idle = EpisodeDetailState(
        phase: .idle,
        episodeID: nil,
        source: nil,
        outcome: nil,
        qualityScore: nil,
        latencyMS: nil,
        outputs: nil,
        cost: nil,
        trace: nil,
        inputs: nil,
        plan: nil,
        toolCalls: nil,
        modelChoices: nil,
        failure: nil
    )
}

public enum EpisodeDetailAction: Equatable, Sendable {
    case loadStarted
    case loaded(Components.Schemas.EpisodeResponse)
    case failed(GatewayFailure)
}

public enum EpisodeDetailReducer {
    public static func reduce(_ state: EpisodeDetailState, _ action: EpisodeDetailAction) -> EpisodeDetailState {
        switch action {
        case .loadStarted:
            return EpisodeDetailState(
                phase: .loading,
                episodeID: state.episodeID,
                source: nil,
                outcome: nil,
                qualityScore: nil,
                latencyMS: nil,
                outputs: nil,
                cost: nil,
                trace: nil,
                inputs: nil,
                plan: nil,
                toolCalls: nil,
                modelChoices: nil,
                failure: nil
            )
        case .loaded(let episode):
            return EpisodeDetailState(
                phase: .loaded,
                episodeID: episode.episodeId,
                source: episode.source,
                outcome: episode.outcome,
                qualityScore: Self.score(episode.qualityScore),
                latencyMS: episode.latencyMs,
                outputs: JSONDisplay.pretty(episode.outputs),
                cost: JSONDisplay.pretty(episode.cost),
                trace: JSONDisplay.pretty(episode.trace),
                inputs: JSONDisplay.pretty(episode.inputs),
                plan: JSONDisplay.pretty(episode.plan),
                toolCalls: JSONDisplay.pretty(episode.toolCalls),
                modelChoices: JSONDisplay.pretty(episode.modelChoices),
                failure: nil
            )
        case .failed(let failure):
            var state = state
            state.phase = .failed
            state.failure = failure
            return state
        }
    }

    public static func fold(_ actions: [EpisodeDetailAction]) -> EpisodeDetailState {
        actions.reduce(.idle, reduce)
    }

    private static func score(_ value: Double) -> String {
        String(value)
    }
}

public enum EpisodeDetailLoader {
    public static func load(id: String, client: any MidplaneClient) async -> [EpisodeDetailAction] {
        var actions: [EpisodeDetailAction] = [.loadStarted]
        do {
            actions.append(.loaded(try await client.getEpisode(id: id)))
        } catch let failure as GatewayFailure {
            actions.append(.failed(failure))
        } catch {
            actions.append(
                .failed(GatewayFailure(statusCode: nil, code: nil, message: error.localizedDescription, requestID: nil))
            )
        }
        return actions
    }
}

public struct DebugSettingsState: Equatable, Sendable {
    public var mode: ClientMode
    public var baseURL: String
    public var hasAPIKey: Bool

    public init(mode: ClientMode, baseURL: String, hasAPIKey: Bool) {
        self.mode = mode
        self.baseURL = baseURL
        self.hasAPIKey = hasAPIKey
    }
}

public enum DebugSettingsAction: Equatable, Sendable {
    case setMode(ClientMode)
    case setBaseURL(String)
    case apiKeyStored
    case apiKeyCleared
}

public enum DebugSettingsReducer {
    public static func reduce(_ state: DebugSettingsState, _ action: DebugSettingsAction) -> DebugSettingsState {
        var state = state
        switch action {
        case .setMode(let mode):
            state.mode = mode
        case .setBaseURL(let baseURL):
            state.baseURL = baseURL
        case .apiKeyStored:
            state.hasAPIKey = true
        case .apiKeyCleared:
            state.hasAPIKey = false
        }
        return state
    }
}

public enum MidplaneClientFactory {
    public static func make(
        mode: ClientMode,
        baseURL: URL,
        keyStore: any APIKeyStore,
        log: any MidplaneLog = StandardMidplaneLog()
    ) -> any MidplaneClient {
        switch mode {
        case .mock:
            return MockMidplaneClient(eventDelayNanoseconds: 180_000_000)
        case .live:
            return LiveMidplaneClient(baseURL: baseURL, keyStore: keyStore, log: log)
        }
    }
}
