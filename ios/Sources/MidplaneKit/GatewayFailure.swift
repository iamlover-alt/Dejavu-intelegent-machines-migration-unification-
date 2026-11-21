import Foundation

/// A failed gateway call.
///
/// Non-2xx bodies use the envelope described in the OpenAPI `info.description`:
/// `{"error": {"code", "message", "request_id"}}`. That shape is not a component
/// schema. See `CONTRACT_QUESTIONS.md`.
public struct GatewayFailure: Error, Equatable, Sendable, LocalizedError {
    public var statusCode: Int?
    public var code: String?
    public var message: String
    public var requestID: String?

    public init(statusCode: Int?, code: String?, message: String, requestID: String?) {
        self.statusCode = statusCode
        self.code = code
        self.message = message
        self.requestID = requestID
    }

    public var errorDescription: String? {
        if let requestID {
            return "\(message) Request ID \(requestID)."
        }
        return message
    }

    public static func notFound(_ message: String, requestID: String?) -> GatewayFailure {
        GatewayFailure(statusCode: 404, code: "not_found", message: message, requestID: requestID)
    }

    static func fromHTTP(
        status: Int,
        data: Data,
        headerRequestID: String?,
        fallbackRequestID: String
    ) -> GatewayFailure {
        if let envelope = try? JSONDecoder().decode(ErrorEnvelope.self, from: data) {
            return GatewayFailure(
                statusCode: status,
                code: envelope.error.code,
                message: envelope.error.message,
                requestID: envelope.error.requestID
            )
        }
        let requestID = headerRequestID ?? fallbackRequestID
        let fallback = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        let message = (fallback?.isEmpty == false) ? fallback! : "The request failed."
        return GatewayFailure(statusCode: status, code: nil, message: message, requestID: requestID)
    }
}

struct ErrorEnvelope: Decodable, Equatable {
    struct Body: Decodable, Equatable {
        var code: String
        var message: String
        var requestID: String

        enum CodingKeys: String, CodingKey {
            case code
            case message
            case requestID = "request_id"
        }
    }

    var error: Body
}
