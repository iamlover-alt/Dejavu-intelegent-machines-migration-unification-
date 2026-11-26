import Foundation
import HTTPTypes
import MidplaneGateway
import OpenAPIRuntime

struct GatewayCall: Sendable {
    var requestID: String
}

enum GatewayContext {
    @TaskLocal static var current: GatewayCall?
}

struct GatewayMiddleware: ClientMiddleware {
    let keyStore: any APIKeyStore
    let log: any MidplaneLog

    func intercept(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String,
        next: @Sendable (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
    ) async throws -> (HTTPResponse, HTTPBody?) {
        let fallbackID = GatewayContext.current?.requestID ?? LiveMidplaneClient.makeRequestID()
        var request = request
        let requestIDField = HTTPField.Name("X-Request-ID")!
        if request.headerFields[requestIDField] == nil {
            request.headerFields[requestIDField] = fallbackID
        }
        let sentID = request.headerFields[requestIDField] ?? fallbackID

        if operationID != Operations.HealthzHealthzGet.id {
            let key = try keyStore.load()?.trimmingCharacters(in: .whitespacesAndNewlines)
            guard let key, !key.isEmpty else {
                let failure = GatewayFailure(
                    statusCode: 401,
                    code: "unauthorized",
                    message: "Missing or invalid API key.",
                    requestID: sentID
                )
                log.log("\(operationID) failed code=unauthorized request_id=\(sentID)")
                throw failure
            }
            request.headerFields[.authorization] = "Bearer \(key)"
        }

        do {
            let (response, responseBody) = try await next(request, body, baseURL)
            let echoed = response.headerFields[requestIDField] ?? sentID
            log.log("\(operationID) status=\(response.status.code) request_id=\(echoed)")
            return (response, responseBody)
        } catch let failure as GatewayFailure {
            throw failure
        } catch {
            let failure = GatewayFailure(
                statusCode: nil,
                code: nil,
                message: error.localizedDescription,
                requestID: sentID
            )
            log.log("\(operationID) failed request_id=\(sentID)")
            throw failure
        }
    }
}
