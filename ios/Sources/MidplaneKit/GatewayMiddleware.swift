import Foundation
import HTTPTypes
import MidplaneGateway
import OpenAPIRuntime

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
        let call = GatewayContext.current
        let requestID = call?.requestID ?? LiveMidplaneClient.makeRequestID()
        var request = request
        let requestIDField = HTTPField.Name("X-Request-ID")!
        request.headerFields[requestIDField] = requestID

        if operationID != Operations.HealthzHealthzGet.id {
            let key = try keyStore.load()?.trimmingCharacters(in: .whitespacesAndNewlines)
            guard let key, !key.isEmpty else {
                let failure = GatewayFailure(
                    statusCode: 401,
                    code: "unauthorized",
                    message: "Missing or invalid API key.",
                    requestID: requestID
                )
                log.log("\(operationID) failed code=unauthorized request_id=\(requestID)")
                throw failure
            }
            request.headerFields[.authorization] = "Bearer \(key)"
        }

        var body = body
        if operationID == Operations.CreateRunV1RunsPost.id,
            let clientRef = call?.clientRef,
            !clientRef.isEmpty,
            let current = body
        {
            let data = try await Data(collecting: current, upTo: 1_000_000)
            body = HTTPBody(try Self.addingClientRef(clientRef, to: data))
        }
        if operationID == Operations.ListAgentsV1AgentsGet.id, let cursor = call?.cursor, !cursor.isEmpty {
            request.path = Self.appendingCursor(cursor, to: request.path)
        }

        do {
            let (response, responseBody) = try await next(request, body, baseURL)
            let echoed = response.headerFields[requestIDField]
            let loggedID = echoed ?? requestID
            if response.status.code >= 400 {
                let data = try await Data(collecting: responseBody ?? HTTPBody(), upTo: 1_000_000)
                let failure = GatewayFailure.fromHTTP(
                    status: response.status.code,
                    data: data,
                    headerRequestID: echoed,
                    fallbackRequestID: requestID
                )
                log.log(
                    "\(operationID) status=\(response.status.code) code=\(failure.code ?? "-") request_id=\(failure.requestID ?? loggedID)"
                )
                throw failure
            }
            log.log("\(operationID) status=\(response.status.code) request_id=\(loggedID)")
            if let call,
                let responseBody,
                operationID == Operations.GetRunV1RunsRunIdGet.id || operationID == Operations.ListAgentsV1AgentsGet.id
            {
                let data = try await Data(collecting: responseBody, upTo: 8_000_000)
                call.capture.read(operationID: operationID, data: data)
                return (response, HTTPBody(data))
            }
            return (response, responseBody)
        } catch let failure as GatewayFailure {
            throw failure
        } catch {
            let failure = GatewayFailure(
                statusCode: nil,
                code: nil,
                message: error.localizedDescription,
                requestID: requestID
            )
            log.log("\(operationID) failed request_id=\(requestID)")
            throw failure
        }
    }

    static func addingClientRef(_ clientRef: String, to data: Data) throws -> Data {
        guard var object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw GatewayFailure(
                statusCode: nil,
                code: nil,
                message: "Could not encode the create-run body.",
                requestID: nil
            )
        }
        object["client_ref"] = clientRef
        return try JSONSerialization.data(withJSONObject: object)
    }

    static func appendingCursor(_ cursor: String, to path: String?) -> String {
        let base = path ?? "/v1/agents"
        let encoded = cursor.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? cursor
        if base.contains("?") {
            return base + "&cursor=" + encoded
        }
        return base + "?cursor=" + encoded
    }
}
