import Foundation
import MidplaneGateway
import OpenAPIRuntime
import OpenAPIURLSession

/// Generated client for the gateway. `GET /healthz` is open. Every other call
/// sends `Authorization: Bearer` from the key store. The key is never logged.
public final class LiveMidplaneClient: MidplaneClient, @unchecked Sendable {
    private let client: Client
    private let keyStore: any APIKeyStore
    private let log: any MidplaneLog

    public init(
        baseURL: URL = MidplaneConfiguration.localCompose,
        keyStore: any APIKeyStore = InMemoryAPIKeyStore(),
        log: any MidplaneLog = StandardMidplaneLog(),
        transport: (any ClientTransport)? = nil
    ) {
        self.keyStore = keyStore
        self.log = log
        client = Client(
            serverURL: baseURL,
            transport: transport ?? URLSessionTransport(),
            middlewares: [GatewayMiddleware(keyStore: keyStore, log: log)]
        )
    }

    public func healthz() async throws -> Components.Schemas.HealthResponse {
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.healthzHealthzGet(.init())
            }
        }
        switch output {
        case .ok(let ok):
            return try ok.body.json
        case .undocumented(let statusCode, _):
            throw GatewayFailure(
                statusCode: statusCode,
                code: nil,
                message: "Unexpected response status \(statusCode).",
                requestID: call.requestID
            )
        }
    }

    public func createRun(
        _ body: Components.Schemas.CreateRunRequest
    ) async throws -> Components.Schemas.CreateRunResponse {
        if let ref = body.clientRef?.trimmingCharacters(in: .whitespacesAndNewlines), ref.count > 200 {
            throw GatewayFailure(
                statusCode: 422,
                code: "validation_error",
                message: "client_ref must be at most 200 characters.",
                requestID: nil
            )
        }
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.createRunV1RunsPost(.init(body: .json(body)))
            }
        }
        switch output {
        case .created(let created):
            return try created.body.json
        case .unprocessableContent:
            throw GatewayFailure(
                statusCode: 422,
                code: "validation_error",
                message: "The request was rejected.",
                requestID: call.requestID
            )
        case .undocumented(let statusCode, _):
            throw GatewayFailure(
                statusCode: statusCode,
                code: nil,
                message: "Unexpected response status \(statusCode).",
                requestID: call.requestID
            )
        }
    }

    public func getRun(id: String) async throws -> Components.Schemas.RunStatusResponse {
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.getRunV1RunsRunIdGet(.init(path: .init(runId: id)))
            }
        }
        switch output {
        case .ok(let ok):
            return try ok.body.json
        case .unprocessableContent:
            throw GatewayFailure(
                statusCode: 422,
                code: "validation_error",
                message: "The request was rejected.",
                requestID: call.requestID
            )
        case .undocumented(let statusCode, _):
            throw GatewayFailure(
                statusCode: statusCode,
                code: nil,
                message: "Unexpected response status \(statusCode).",
                requestID: call.requestID
            )
        }
    }

    public func getEpisode(id: String) async throws -> Components.Schemas.EpisodeResponse {
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.getEpisodeV1EpisodesEpisodeIdGet(.init(path: .init(episodeId: id)))
            }
        }
        switch output {
        case .ok(let ok):
            return try ok.body.json
        case .unprocessableContent:
            throw GatewayFailure(
                statusCode: 422,
                code: "validation_error",
                message: "The request was rejected.",
                requestID: call.requestID
            )
        case .undocumented(let statusCode, _):
            throw GatewayFailure(
                statusCode: statusCode,
                code: nil,
                message: "Unexpected response status \(statusCode).",
                requestID: call.requestID
            )
        }
    }

    public func listAgents(limit: Int?, cursor: String?) async throws -> Components.Schemas.AgentPage {
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.listAgentsV1AgentsGet(.init(query: .init(limit: limit, cursor: cursor)))
            }
        }
        switch output {
        case .ok(let ok):
            return try ok.body.json
        case .unprocessableContent:
            throw GatewayFailure(
                statusCode: 422,
                code: "validation_error",
                message: "The request was rejected.",
                requestID: call.requestID
            )
        case .undocumented(let statusCode, _):
            throw GatewayFailure(
                statusCode: statusCode,
                code: nil,
                message: "Unexpected response status \(statusCode).",
                requestID: call.requestID
            )
        }
    }

    /// Opens `GET /v1/runs/{id}/events` through the generated client.
    ///
    /// `lastEventID` is sent as `Last-Event-ID`. The server resumes after a known
    /// id and replays the whole log when the id is missing or unknown.
    public func runEvents(id: String, lastEventID: String?) async -> AsyncThrowingStream<RunStreamEvent, Error> {
        let client = self.client
        let log = self.log
        return AsyncThrowingStream { continuation in
            let task = Task {
                let requestID = Self.makeRequestID()
                do {
                    let output = try await Self.unwrapping(requestID: requestID) {
                        try await GatewayContext.$current.withValue(GatewayCall(requestID: requestID)) {
                            try await client.runEventsV1RunsRunIdEventsGet(
                                .init(
                                    path: .init(runId: id),
                                    headers: .init(lastEventID: lastEventID)
                                )
                            )
                        }
                    }
                    switch output {
                    case .ok(let ok):
                        let body = try ok.body.textEventStream
                        var parser = SSEParser()
                        for try await chunk in body {
                            try Task.checkCancellation()
                            for event in parser.append(bytes: chunk) {
                                continuation.yield(event)
                            }
                        }
                        continuation.finish()
                    case .unprocessableContent:
                        throw GatewayFailure(
                            statusCode: 422,
                            code: "validation_error",
                            message: "The request was rejected.",
                            requestID: requestID
                        )
                    case .undocumented(let statusCode, _):
                        throw GatewayFailure(
                            statusCode: statusCode,
                            code: nil,
                            message: "Unexpected response status \(statusCode).",
                            requestID: requestID
                        )
                    }
                } catch is CancellationError {
                    continuation.finish()
                } catch let failure as GatewayFailure {
                    log.log("run_events failed code=\(failure.code ?? "-") request_id=\(failure.requestID ?? requestID)")
                    continuation.finish(throwing: failure)
                } catch {
                    log.log("run_events failed request_id=\(requestID)")
                    continuation.finish(
                        throwing: GatewayFailure(
                            statusCode: nil,
                            code: nil,
                            message: error.localizedDescription,
                            requestID: requestID
                        )
                    )
                }
            }
            continuation.onTermination = { @Sendable _ in
                task.cancel()
            }
        }
    }

    private static func unwrapping<T>(
        requestID: String,
        _ operation: () async throws -> T
    ) async throws -> T {
        do {
            return try await operation()
        } catch let error as ClientError {
            if let failure = error.underlyingError as? GatewayFailure {
                throw failure
            }
            throw GatewayFailure(
                statusCode: nil,
                code: nil,
                message: error.causeDescription,
                requestID: requestID
            )
        }
    }

    static func makeRequestID() -> String {
        "req_" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    }
}
