import Foundation
import MidplaneGateway
import OpenAPIRuntime
import OpenAPIURLSession

/// Generated client for the gateway. `GET /healthz` is open. Every other call
/// sends `Authorization: Bearer` from the key store. The key is never logged.
/// Each call sends `X-Request-ID`. Authenticated responses update `latestRateLimit()`.
public final class LiveMidplaneClient: MidplaneClient, @unchecked Sendable {
    private let client: Client
    private let keyStore: any APIKeyStore
    private let log: any MidplaneLog
    private let rates = RateLimitStore()

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

    public func latestRateLimit() async -> RateLimit? {
        await rates.current()
    }

    public func healthz() async throws -> Components.Schemas.HealthResponse {
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.healthzHealthzGet(.init(headers: .init(xRequestID: call.requestID)))
            }
        }
        switch output {
        case .ok(let ok):
            return try ok.body.json
        case .unprocessableContent(let response):
            try await fail(422, try response.body.json, requestID: response.headers.xRequestID)
        case .internalServerError(let response):
            try await fail(500, try response.body.json, requestID: response.headers.xRequestID)
        case .undocumented(let statusCode, let payload):
            try await failUndocumented(statusCode, payload, requestID: call.requestID)
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
                requestID: nil,
                details: [FieldIssue(field: "client_ref", issue: "Must be at most 200 characters.")]
            )
        }
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.createRunV1RunsPost(
                    .init(headers: .init(xRequestID: call.requestID), body: .json(body))
                )
            }
        }
        switch output {
        case .created(let created):
            await remember(created.headers)
            return try created.body.json
        case .unprocessableContent(let response):
            try await fail(422, try response.body.json, headers: response.headers)
        case .internalServerError(let response):
            try await fail(500, try response.body.json, headers: response.headers)
        case .unauthorized(let response):
            try await fail(401, try response.body.json, headers: response.headers)
        case .tooManyRequests(let response):
            try await fail(429, try response.body.json, headers: response.headers, retryAfter: response.headers.retryAfter)
        case .undocumented(let statusCode, let payload):
            try await failUndocumented(statusCode, payload, requestID: call.requestID)
        }
    }

    public func getRun(id: String) async throws -> Components.Schemas.RunStatusResponse {
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.getRunV1RunsRunIdGet(
                    .init(path: .init(runId: id), headers: .init(xRequestID: call.requestID))
                )
            }
        }
        switch output {
        case .ok(let ok):
            await remember(ok.headers)
            return try ok.body.json
        case .unprocessableContent(let response):
            try await fail(422, try response.body.json, headers: response.headers)
        case .internalServerError(let response):
            try await fail(500, try response.body.json, headers: response.headers)
        case .unauthorized(let response):
            try await fail(401, try response.body.json, headers: response.headers)
        case .tooManyRequests(let response):
            try await fail(429, try response.body.json, headers: response.headers, retryAfter: response.headers.retryAfter)
        case .notFound(let response):
            try await fail(404, try response.body.json, headers: response.headers)
        case .undocumented(let statusCode, let payload):
            try await failUndocumented(statusCode, payload, requestID: call.requestID)
        }
    }

    public func getEpisode(id: String) async throws -> Components.Schemas.EpisodeResponse {
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.getEpisodeV1EpisodesEpisodeIdGet(
                    .init(path: .init(episodeId: id), headers: .init(xRequestID: call.requestID))
                )
            }
        }
        switch output {
        case .ok(let ok):
            await remember(ok.headers)
            return try ok.body.json
        case .unprocessableContent(let response):
            try await fail(422, try response.body.json, headers: response.headers)
        case .internalServerError(let response):
            try await fail(500, try response.body.json, headers: response.headers)
        case .unauthorized(let response):
            try await fail(401, try response.body.json, headers: response.headers)
        case .tooManyRequests(let response):
            try await fail(429, try response.body.json, headers: response.headers, retryAfter: response.headers.retryAfter)
        case .notFound(let response):
            try await fail(404, try response.body.json, headers: response.headers)
        case .undocumented(let statusCode, let payload):
            try await failUndocumented(statusCode, payload, requestID: call.requestID)
        }
    }

    public func listAgents(limit: Int?, cursor: String?) async throws -> Components.Schemas.AgentPage {
        let call = GatewayCall(requestID: Self.makeRequestID())
        let output = try await Self.unwrapping(requestID: call.requestID) {
            try await GatewayContext.$current.withValue(call) {
                try await client.listAgentsV1AgentsGet(
                    .init(query: .init(limit: limit, cursor: cursor), headers: .init(xRequestID: call.requestID))
                )
            }
        }
        switch output {
        case .ok(let ok):
            await remember(ok.headers)
            return try ok.body.json
        case .unprocessableContent(let response):
            try await fail(422, try response.body.json, headers: response.headers)
        case .internalServerError(let response):
            try await fail(500, try response.body.json, headers: response.headers)
        case .unauthorized(let response):
            try await fail(401, try response.body.json, headers: response.headers)
        case .tooManyRequests(let response):
            try await fail(429, try response.body.json, headers: response.headers, retryAfter: response.headers.retryAfter)
        case .undocumented(let statusCode, let payload):
            try await failUndocumented(statusCode, payload, requestID: call.requestID)
        }
    }

    /// Opens `GET /v1/runs/{id}/events` through the generated client.
    ///
    /// `lastEventID` is sent as `Last-Event-ID`. The server resumes after a known
    /// id and replays the whole log when the id is missing or unknown.
    public func runEvents(id: String, lastEventID: String?) async -> AsyncThrowingStream<RunStreamEvent, Error> {
        let client = self.client
        let log = self.log
        let rates = self.rates
        return AsyncThrowingStream { continuation in
            let task = Task {
                let requestID = Self.makeRequestID()
                do {
                    let output = try await Self.unwrapping(requestID: requestID) {
                        try await GatewayContext.$current.withValue(GatewayCall(requestID: requestID)) {
                            try await client.runEventsV1RunsRunIdEventsGet(
                                .init(
                                    path: .init(runId: id),
                                    headers: .init(lastEventID: lastEventID, xRequestID: requestID)
                                )
                            )
                        }
                    }
                    switch output {
                    case .ok(let ok):
                        await rates.store(Self.rateLimit(ok.headers))
                        let body = try ok.body.textEventStream
                        var parser = SSEParser()
                        for try await chunk in body {
                            try Task.checkCancellation()
                            for event in parser.append(bytes: chunk) {
                                continuation.yield(event)
                            }
                        }
                        continuation.finish()
                    case .unprocessableContent(let response):
                        try await Self.throwFailure(422, try response.body.json, headers: response.headers, rates: rates, log: log)
                    case .internalServerError(let response):
                        try await Self.throwFailure(500, try response.body.json, headers: response.headers, rates: rates, log: log)
                    case .unauthorized(let response):
                        try await Self.throwFailure(401, try response.body.json, headers: response.headers, rates: rates, log: log)
                    case .tooManyRequests(let response):
                        try await Self.throwFailure(
                            429,
                            try response.body.json,
                            headers: response.headers,
                            retryAfter: response.headers.retryAfter,
                            rates: rates,
                            log: log
                        )
                    case .notFound(let response):
                        try await Self.throwFailure(404, try response.body.json, headers: response.headers, rates: rates, log: log)
                    case .undocumented(let statusCode, let payload):
                        try await Self.throwUndocumented(statusCode, payload, requestID: requestID, log: log)
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

    private func remember<Headers: RateLimitedHeaders>(_ headers: Headers) async {
        await rates.store(Self.rateLimit(headers))
    }

    private func fail(
        _ status: Int,
        _ envelope: Components.Schemas.GatewayErrorEnvelope,
        requestID: String
    ) async throws -> Never {
        let failure = GatewayFailure.decoded(statusCode: status, envelope: envelope)
        log.log("code=\(failure.code ?? "-") request_id=\(failure.requestID ?? requestID)")
        throw failure
    }

    private func fail<Headers: RateLimitedHeaders>(
        _ status: Int,
        _ envelope: Components.Schemas.GatewayErrorEnvelope,
        headers: Headers,
        retryAfter: Int? = nil
    ) async throws -> Never {
        try await Self.throwFailure(status, envelope, headers: headers, retryAfter: retryAfter, rates: rates, log: log)
    }

    private func failUndocumented(
        _ status: Int,
        _ payload: OpenAPIRuntime.UndocumentedPayload,
        requestID: String
    ) async throws -> Never {
        try await Self.throwUndocumented(status, payload, requestID: requestID, log: log)
    }

    private static func throwFailure<Headers: RateLimitedHeaders>(
        _ status: Int,
        _ envelope: Components.Schemas.GatewayErrorEnvelope,
        headers: Headers,
        retryAfter: Int? = nil,
        rates: RateLimitStore,
        log: any MidplaneLog
    ) async throws -> Never {
        let rate = rateLimit(headers)
        await rates.store(rate)
        let failure = GatewayFailure.decoded(
            statusCode: status,
            envelope: envelope,
            retryAfterSeconds: retryAfter,
            rateLimit: rate
        )
        log.log("code=\(failure.code ?? "-") request_id=\(failure.requestID ?? headers.xRequestID)")
        throw failure
    }

    private static func throwUndocumented(
        _ status: Int,
        _ payload: OpenAPIRuntime.UndocumentedPayload,
        requestID: String,
        log: any MidplaneLog
    ) async throws -> Never {
        let data = try await Data(collecting: payload.body ?? HTTPBody(), upTo: 1_000_000)
        if let envelope = try? JSONDecoder().decode(Components.Schemas.GatewayErrorEnvelope.self, from: data) {
            let failure = GatewayFailure.decoded(statusCode: status, envelope: envelope)
            log.log("code=\(failure.code ?? "-") request_id=\(failure.requestID ?? requestID)")
            throw failure
        }
        throw GatewayFailure(
            statusCode: status,
            code: nil,
            message: "Unexpected response status \(status).",
            requestID: requestID
        )
    }

    private static func rateLimit<Headers: RateLimitedHeaders>(_ headers: Headers) -> RateLimit {
        RateLimit(
            limit: headers.rateLimitLimit,
            remaining: headers.rateLimitRemaining,
            resetSeconds: headers.rateLimitReset
        )
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

private protocol RateLimitedHeaders {
    var xRequestID: String { get }
    var rateLimitLimit: Int { get }
    var rateLimitRemaining: Int { get }
    var rateLimitReset: Int { get }
}

extension Operations.CreateRunV1RunsPost.Output.Created.Headers: RateLimitedHeaders {}
extension Operations.CreateRunV1RunsPost.Output.UnprocessableContent.Headers: RateLimitedHeaders {}
extension Operations.CreateRunV1RunsPost.Output.InternalServerError.Headers: RateLimitedHeaders {}
extension Operations.CreateRunV1RunsPost.Output.Unauthorized.Headers: RateLimitedHeaders {}
extension Operations.CreateRunV1RunsPost.Output.TooManyRequests.Headers: RateLimitedHeaders {}
extension Operations.GetRunV1RunsRunIdGet.Output.Ok.Headers: RateLimitedHeaders {}
extension Operations.GetRunV1RunsRunIdGet.Output.UnprocessableContent.Headers: RateLimitedHeaders {}
extension Operations.GetRunV1RunsRunIdGet.Output.InternalServerError.Headers: RateLimitedHeaders {}
extension Operations.GetRunV1RunsRunIdGet.Output.Unauthorized.Headers: RateLimitedHeaders {}
extension Operations.GetRunV1RunsRunIdGet.Output.TooManyRequests.Headers: RateLimitedHeaders {}
extension Operations.GetRunV1RunsRunIdGet.Output.NotFound.Headers: RateLimitedHeaders {}
extension Operations.GetEpisodeV1EpisodesEpisodeIdGet.Output.Ok.Headers: RateLimitedHeaders {}
extension Operations.GetEpisodeV1EpisodesEpisodeIdGet.Output.UnprocessableContent.Headers: RateLimitedHeaders {}
extension Operations.GetEpisodeV1EpisodesEpisodeIdGet.Output.InternalServerError.Headers: RateLimitedHeaders {}
extension Operations.GetEpisodeV1EpisodesEpisodeIdGet.Output.Unauthorized.Headers: RateLimitedHeaders {}
extension Operations.GetEpisodeV1EpisodesEpisodeIdGet.Output.TooManyRequests.Headers: RateLimitedHeaders {}
extension Operations.GetEpisodeV1EpisodesEpisodeIdGet.Output.NotFound.Headers: RateLimitedHeaders {}
extension Operations.ListAgentsV1AgentsGet.Output.Ok.Headers: RateLimitedHeaders {}
extension Operations.ListAgentsV1AgentsGet.Output.UnprocessableContent.Headers: RateLimitedHeaders {}
extension Operations.ListAgentsV1AgentsGet.Output.InternalServerError.Headers: RateLimitedHeaders {}
extension Operations.ListAgentsV1AgentsGet.Output.Unauthorized.Headers: RateLimitedHeaders {}
extension Operations.ListAgentsV1AgentsGet.Output.TooManyRequests.Headers: RateLimitedHeaders {}
extension Operations.RunEventsV1RunsRunIdEventsGet.Output.Ok.Headers: RateLimitedHeaders {}
extension Operations.RunEventsV1RunsRunIdEventsGet.Output.UnprocessableContent.Headers: RateLimitedHeaders {}
extension Operations.RunEventsV1RunsRunIdEventsGet.Output.InternalServerError.Headers: RateLimitedHeaders {}
extension Operations.RunEventsV1RunsRunIdEventsGet.Output.Unauthorized.Headers: RateLimitedHeaders {}
extension Operations.RunEventsV1RunsRunIdEventsGet.Output.TooManyRequests.Headers: RateLimitedHeaders {}
extension Operations.RunEventsV1RunsRunIdEventsGet.Output.NotFound.Headers: RateLimitedHeaders {}

private actor RateLimitStore {
    private var latest: RateLimit?

    func store(_ value: RateLimit) {
        latest = value
    }

    func current() -> RateLimit? {
        latest
    }
}
