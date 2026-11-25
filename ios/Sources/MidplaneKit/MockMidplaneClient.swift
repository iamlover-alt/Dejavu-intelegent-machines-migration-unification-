import Foundation
import MidplaneGateway
import OpenAPIRuntime

/// In-process stand-in for the gateway. Fixture data follows the YAML and the
/// roster in `midplane/gateway/roster.json`. It does not open a network connection.
public actor MockMidplaneClient: MidplaneClient {
    public var eventDelayNanoseconds: UInt64

    private var runs: [String: FixtureRun] = [:]
    private var episodes: [String: Components.Schemas.EpisodeResponse] = [:]
    private var requestCount = 0

    public init(eventDelayNanoseconds: UInt64 = 0) {
        self.eventDelayNanoseconds = eventDelayNanoseconds
    }

    public func healthz() async throws -> Components.Schemas.HealthResponse {
        Components.Schemas.HealthResponse(status: .ok)
    }

    public func createRun(
        _ body: Components.Schemas.CreateRunRequest
    ) async throws -> Components.Schemas.CreateRunResponse {
        let requestID = nextRequestID()
        let diff = body.input.diff.trimmingCharacters(in: .whitespacesAndNewlines)
        if diff.isEmpty {
            throw GatewayFailure(
                statusCode: 422,
                code: "validation_error",
                message: "input.diff: field required",
                requestID: requestID
            )
        }
        let ref = body.clientRef?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let ref, ref.count > 200 {
            throw GatewayFailure(
                statusCode: 422,
                code: "validation_error",
                message: "client_ref must be at most 200 characters.",
                requestID: requestID
            )
        }
        let runID = UUID().uuidString.lowercased()
        let storedRef = (ref?.isEmpty == false) ? ref : nil
        runs[runID] = FixtureRun(
            taskType: body.taskType.rawValue,
            diff: body.input.diff,
            repo: body.input.repo ?? "local",
            instructions: body.input.instructions ?? "Review this diff.",
            clientRef: storedRef,
            status: .queued,
            episodeID: nil,
            yielded: 0
        )
        return Components.Schemas.CreateRunResponse(runId: runID, status: .queued)
    }

    public func getRun(id: String) async throws -> Components.Schemas.RunStatusResponse {
        let requestID = nextRequestID()
        guard let run = runs[id] else {
            throw GatewayFailure.notFound("Run not found.", requestID: requestID)
        }
        return run.record(id: id)
    }

    public func getEpisode(id: String) async throws -> Components.Schemas.EpisodeResponse {
        let requestID = nextRequestID()
        guard let episode = episodes[id] else {
            throw GatewayFailure.notFound("Episode not found.", requestID: requestID)
        }
        return episode
    }

    public func listAgents(limit: Int?, cursor: String?) async throws -> Components.Schemas.AgentPage {
        let requestID = nextRequestID()
        let pageLimit = limit ?? 50
        if pageLimit < 1 || pageLimit > 100 {
            throw GatewayFailure(
                statusCode: 422,
                code: "validation_error",
                message: "limit: must be from 1 to 100",
                requestID: requestID
            )
        }
        let roster = Self.roster
        var start = 0
        if let cursor {
            guard let name = Self.decodeCursor(cursor), let index = roster.firstIndex(where: { $0.name == name }) else {
                throw GatewayFailure(
                    statusCode: 400,
                    code: "invalid_cursor",
                    message: "The cursor is not valid.",
                    requestID: requestID
                )
            }
            start = index + 1
        }
        let window = Array(roster.dropFirst(start).prefix(pageLimit))
        let next = (start + pageLimit < roster.count && !window.isEmpty) ? Self.encodeCursor(window[window.count - 1].name) : nil
        let agents = window.map {
            Components.Schemas.Agent(name: $0.name, title: $0.title, status: $0.status)
        }
        return Components.Schemas.AgentPage(data: agents, nextCursor: next, limit: pageLimit)
    }

    public func runEvents(id: String, lastEventID: String?) async -> AsyncThrowingStream<RunStreamEvent, Error> {
        let requestID = nextRequestID()
        guard runs[id] != nil else {
            return AsyncThrowingStream { continuation in
                continuation.finish(throwing: GatewayFailure.notFound("Run not found.", requestID: requestID))
            }
        }
        let delay = eventDelayNanoseconds
        return AsyncThrowingStream { continuation in
            let task = Task {
                let events = self.events(for: id, lastEventID: lastEventID)
                do {
                    for event in events {
                        try Task.checkCancellation()
                        if delay > 0 {
                            try await Task.sleep(nanoseconds: delay)
                        }
                        self.advance(runID: id, event: event)
                        continuation.yield(event)
                    }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { @Sendable _ in
                task.cancel()
            }
        }
    }

    private func events(for runID: String, lastEventID: String?) -> [RunStreamEvent] {
        guard let run = runs[runID] else {
            return []
        }
        let wire = Self.eventWire(runID: runID, diff: run.diff)
        var parser = SSEParser()
        let frames = parser.append(wire)
        return Array(frames.dropFirst(EventResume.startIndex(events: frames, lastEventID: lastEventID)))
    }

    private func advance(runID: String, event: RunStreamEvent) {
        guard var run = runs[runID] else {
            return
        }
        run.yielded += 1
        switch RunEventDecoder.decode(name: event.name, data: event.data) {
        case .runStarted, .nodeStarted, .nodeCompleted:
            if run.status != .succeeded && run.status != .failed {
                run.status = .running
            }
        case .runCompleted(let payload):
            run.status = .succeeded
            run.episodeID = payload.episodeId ?? runID
            if let episodeID = run.episodeID {
                episodes[episodeID] = Self.episode(runID: runID, episodeID: episodeID, run: run)
            }
        case .runFailed(let payload):
            run.status = .failed
            run.errorMessage = payload.error
            run.episodeID = payload.episodeId
            if let episodeID = payload.episodeId {
                episodes[episodeID] = Self.episode(runID: runID, episodeID: episodeID, run: run)
            }
        case nil:
            break
        }
        runs[runID] = run
    }

    private func nextRequestID() -> String {
        requestCount += 1
        return String(format: "req_mock%04d", requestCount)
    }

    private struct RosterEntry {
        var name: String
        var title: String
        var status: String
    }

    /// Copy of `midplane/gateway/roster.json`. The cursor is opaque to callers.
    private static let roster: [RosterEntry] = [
        RosterEntry(name: "bot-builder", title: "Bot Builder", status: "active"),
        RosterEntry(name: "storefront", title: "Storefront", status: "active"),
        RosterEntry(name: "front-desk", title: "Front Desk", status: "active"),
        RosterEntry(name: "grok-bot", title: "Grok Bot", status: "active"),
        RosterEntry(name: "money-desk", title: "Money Desk", status: "active"),
        RosterEntry(name: "skeptic-lab", title: "Skeptic Lab", status: "active"),
        RosterEntry(name: "neutral-ops", title: "Neutral Ops", status: "active"),
        RosterEntry(name: "platform-architect", title: "Platform Architect", status: "active"),
        RosterEntry(name: "ghost-engineer", title: "Ghost Engineer", status: "active"),
        RosterEntry(name: "ios-studio", title: "iOS Studio", status: "active"),
    ]

    static func encodeCursor(_ value: String) -> String {
        Data(value.utf8).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    static func decodeCursor(_ cursor: String) -> String? {
        var base64 = cursor.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        let remainder = base64.count % 4
        if remainder > 0 {
            base64 += String(repeating: "=", count: 4 - remainder)
        }
        guard let data = Data(base64Encoded: base64), let text = String(data: data, encoding: .utf8), !text.isEmpty else {
            return nil
        }
        return text
    }

    private static func eventWire(runID: String, diff: String) -> String {
        let stamp = "2026-11-25T00:00:00Z"
        let nodes = ["supervisor", "router", "code_review", "episode_writer"]
        var blocks: [String] = []
        var nextID = 1
        func frame(_ event: String, _ data: String) {
            blocks.append("id: \(nextID)\nevent: \(event)\ndata: \(data)\n\n")
            nextID += 1
        }
        frame(
            "run.started",
            #"{"at":"\#(stamp)","run_id":"\#(runID)","task_type":"code_review"}"#
        )
        for node in nodes {
            frame("node.started", #"{"at":"\#(stamp)","node":"\#(node)"}"#)
            frame(
                "node.completed",
                #"{"at":"\#(stamp)","node":"\#(node)","status":"completed"}"#
            )
        }
        let failed = diff.lowercased().contains("force-fail")
        if failed {
            frame(
                "run.failed",
                #"{"at":"\#(stamp)","episode_id":null,"error":"The graph run finished with outcome=error.","run_id":"\#(runID)","status":"failed"}"#
            )
        } else {
            frame(
                "run.completed",
                #"{"at":"\#(stamp)","episode_id":"\#(runID)","run_id":"\#(runID)","status":"succeeded"}"#
            )
        }
        return blocks.joined()
    }

    private static func episode(
        runID: String,
        episodeID: String,
        run: FixtureRun
    ) -> Components.Schemas.EpisodeResponse {
        let blocked = run.diff.lowercased().contains("password") || run.diff.lowercased().contains("secret")
        let finding = blocked
            ? #"{"detail":"Sensitive or unsafe pattern in an added line.","ref":"diff","severity":"block"}"#
            : #"{"detail":"No blocking issues in the added lines.","ref":"diff","severity":"info"}"#
        let outputs = """
        {"confidence":0.9,"findings":[\(finding)],"summary":"Fixture review."}
        """
        let inputs = """
        {"diff":\(jsonString(run.diff)),"instructions":\(jsonString(run.instructions)),"repo":\(jsonString(run.repo))}
        """
        let plan = #"{"steps":["code_review"]}"#
        let cost = #"{"total_usd":0.001}"#
        let choice = #"{"criteria":{"confidence":0.5,"cost":1,"latency":1,"task_type":"code_review"},"fallback":"fixture-large","primary":"fixture-small"}"#
        let trace = nodesTrace()
        func object(_ json: String) -> OpenAPIObjectContainer {
            (try? JSONDecoder().decode(OpenAPIObjectContainer.self, from: Data(json.utf8))) ?? OpenAPIObjectContainer()
        }
        func value(_ json: String) -> OpenAPIValueContainer {
            (try? JSONDecoder().decode(OpenAPIValueContainer.self, from: Data(json.utf8)))
                ?? (try! OpenAPIValueContainer(unvalidatedValue: json))
        }
        return Components.Schemas.EpisodeResponse(
            episodeId: episodeID,
            source: "app",
            inputs: .init(additionalProperties: object(inputs)),
            plan: .init(additionalProperties: object(plan)),
            toolCalls: [],
            modelChoices: [value(choice)],
            outputs: .init(additionalProperties: object(outputs)),
            cost: .init(additionalProperties: object(cost)),
            latencyMs: 1280,
            outcome: "success",
            qualityScore: 0.5,
            trace: trace.map(value)
        )
    }

    private static func nodesTrace() -> [String] {
        ["supervisor", "router", "code_review", "episode_writer"].map { node in
            #"{"node":"\#(node)","status":"completed"}"#
        }
    }

    private static func jsonString(_ value: String) -> String {
        let data = try! JSONSerialization.data(withJSONObject: [value])
        let text = String(data: data, encoding: .utf8)!
        return String(text.dropFirst().dropLast())
    }
}

private struct FixtureRun: Sendable {
    var taskType: String
    var diff: String
    var repo: String
    var instructions: String
    var clientRef: String?
    var status: Components.Schemas.RunStatusResponse.StatusPayload
    var episodeID: String?
    var errorMessage: String? = nil
    var yielded: Int

    func record(id: String) -> Components.Schemas.RunStatusResponse {
        let result: Components.Schemas.RunStatusResponse.ResultPayload? = status == .succeeded
            ? (try? JSONDecoder().decode(
                Components.Schemas.RunStatusResponse.ResultPayload.self,
                from: Data(#"{"summary":"Fixture review."}"#.utf8)
            ))
            : nil
        return Components.Schemas.RunStatusResponse(
            runId: id,
            status: status,
            taskType: taskType,
            result: result,
            episodeId: episodeID,
            clientRef: clientRef,
            error: errorMessage
        )
    }
}
