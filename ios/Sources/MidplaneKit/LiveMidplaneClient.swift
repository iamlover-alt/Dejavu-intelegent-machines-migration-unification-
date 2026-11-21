import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import MidplaneGateway
import OpenAPIRuntime
import OpenAPIURLSession

/// Generated client for the JSON operations, plus a URLSession stream for run events.
///
/// `GET /healthz` is open. Every other call sends `Authorization: Bearer` from the
/// key store. The key is never logged or committed.
public final class LiveMidplaneClient: MidplaneClient, @unchecked Sendable {
    private let baseURL: URL
    private let client: Client
    private let keyStore: any APIKeyStore
    private let session: URLSession
    private let log: any MidplaneLog

    public init(
        baseURL: URL = MidplaneConfiguration.localCompose,
        keyStore: any APIKeyStore = InMemoryAPIKeyStore(),
        session: URLSession = .shared,
        log: any MidplaneLog = StandardMidplaneLog(),
        transport: (any ClientTransport)? = nil
    ) {
        self.baseURL = baseURL
        self.keyStore = keyStore
        self.session = session
        self.log = log
        let middleware = GatewayMiddleware(keyStore: keyStore, log: log)
        client = Client(
            serverURL: baseURL,
            transport: transport ?? URLSessionTransport(),
            middlewares: [middleware]
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
        _ body: Components.Schemas.CreateRunRequest,
        clientRef: String?
    ) async throws -> Components.Schemas.CreateRunResponse {
        let trimmed = clientRef?.trimmingCharacters(in: .whitespacesAndNewlines)
        let ref = (trimmed?.isEmpty == false) ? trimmed : nil
        if let ref, ref.count > 200 {
            throw GatewayFailure(
                statusCode: 422,
                code: "validation_error",
                message: "client_ref must be at most 200 characters.",
                requestID: nil
            )
        }
        let call = GatewayCall(requestID: Self.makeRequestID(), clientRef: ref)
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

    public func getRun(id: String) async throws -> RunRecord {
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.getRunV1RunsRunIdGet(.init(path: .init(runId: id)))
            }
        }
        switch output {
        case .ok(let ok):
            return RunRecord(
                response: try ok.body.json,
                episodeID: call.capture.episodeID,
                resultJSON: call.capture.resultJSON,
                clientRef: call.capture.clientRef,
                errorMessage: call.capture.runError
            )
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

    public func listAgents(limit: Int?, cursor: String?) async throws -> AgentList {
        let call = GatewayCall(requestID: Self.makeRequestID(), cursor: cursor)
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.listAgentsV1AgentsGet(.init(query: .init(limit: limit)))
            }
        }
        switch output {
        case .ok(let ok):
            return AgentList(page: try ok.body.json, nextCursor: call.capture.nextCursor)
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

    /// Opens `GET /v1/runs/{id}/events`.
    ///
    /// The YAML does not define `Last-Event-ID` or a durable event id. This method
    /// does not send that header. A later call reads the stream from the start.
    public func runEvents(id: String) async -> AsyncThrowingStream<RunStreamEvent, Error> {
        let baseURL = self.baseURL
        let keyStore = self.keyStore
        let session = self.session
        let log = self.log
        return AsyncThrowingStream { continuation in
            let task = Task {
                let requestID = Self.makeRequestID()
                do {
                    let request = try Self.eventsRequest(
                        baseURL: baseURL,
                        runID: id,
                        apiKey: try keyStore.load(),
                        requestID: requestID
                    )
                    try await Self.readEvents(
                        session: session,
                        request: request,
                        requestID: requestID,
                        log: log,
                        continuation: continuation
                    )
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch let failure as GatewayFailure {
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

    /// Apple `URLSession` can stream the body. Linux Foundation only offers `data(for:)`,
    /// so that build reads the frames after the response finishes.
    private static func readEvents(
        session: URLSession,
        request: URLRequest,
        requestID: String,
        log: any MidplaneLog,
        continuation: AsyncThrowingStream<RunStreamEvent, Error>.Continuation
    ) async throws {
        #if canImport(Darwin)
        let (bytes, response) = try await session.bytes(for: request)
        let http = try httpResponse(response, requestID: requestID)
        let echoed = http.value(forHTTPHeaderField: "X-Request-ID") ?? requestID
        guard (200 ..< 300).contains(http.statusCode) else {
            var data = Data()
            for try await byte in bytes {
                data.append(byte)
            }
            throw loggedHTTPFailure(
                status: http.statusCode,
                data: data,
                echoed: echoed,
                requestID: requestID,
                log: log
            )
        }
        log.log("run_events status=\(http.statusCode) request_id=\(echoed)")
        var parser = SSEParser()
        for try await line in bytes.lines {
            try Task.checkCancellation()
            for event in parser.append(line + "\n") {
                continuation.yield(event)
            }
        }
        #else
        let (data, response) = try await session.data(for: request)
        let http = try httpResponse(response, requestID: requestID)
        let echoed = http.value(forHTTPHeaderField: "X-Request-ID") ?? requestID
        guard (200 ..< 300).contains(http.statusCode) else {
            throw loggedHTTPFailure(
                status: http.statusCode,
                data: data,
                echoed: echoed,
                requestID: requestID,
                log: log
            )
        }
        log.log("run_events status=\(http.statusCode) request_id=\(echoed)")
        let text = String(data: data, encoding: .utf8) ?? ""
        var parser = SSEParser()
        for event in parser.append(text) {
            continuation.yield(event)
        }
        #endif
    }

    private static func httpResponse(_ response: URLResponse, requestID: String) throws -> HTTPURLResponse {
        guard let http = response as? HTTPURLResponse else {
            throw GatewayFailure(
                statusCode: nil,
                code: nil,
                message: "The events response was not HTTP.",
                requestID: requestID
            )
        }
        return http
    }

    private static func loggedHTTPFailure(
        status: Int,
        data: Data,
        echoed: String,
        requestID: String,
        log: any MidplaneLog
    ) -> GatewayFailure {
        let failure = GatewayFailure.fromHTTP(
            status: status,
            data: data,
            headerRequestID: echoed,
            fallbackRequestID: requestID
        )
        log.log(
            "run_events status=\(status) code=\(failure.code ?? "-") request_id=\(failure.requestID ?? echoed)"
        )
        return failure
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

    static func eventsRequest(baseURL: URL, runID: String, apiKey: String?, requestID: String) throws -> URLRequest {
        let key = apiKey?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let key, !key.isEmpty else {
            throw GatewayFailure(
                statusCode: 401,
                code: "unauthorized",
                message: "Missing or invalid API key.",
                requestID: requestID
            )
        }
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw GatewayFailure(statusCode: nil, code: nil, message: "Invalid base URL.", requestID: requestID)
        }
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/")
        let encodedID = runID.addingPercentEncoding(withAllowedCharacters: allowed) ?? runID
        let prefix = components.percentEncodedPath.hasSuffix("/")
            ? String(components.percentEncodedPath.dropLast())
            : components.percentEncodedPath
        components.percentEncodedPath = prefix + "/v1/runs/" + encodedID + "/events"
        guard let url = components.url else {
            throw GatewayFailure(statusCode: nil, code: nil, message: "Invalid events URL.", requestID: requestID)
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.setValue(requestID, forHTTPHeaderField: "X-Request-ID")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        return request
    }
}
