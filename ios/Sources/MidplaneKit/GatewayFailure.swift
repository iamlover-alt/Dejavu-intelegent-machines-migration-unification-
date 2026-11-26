import Foundation
import MidplaneGateway

/// Requests remaining in the current authenticated window.
public struct RateLimit: Equatable, Sendable {
    public var limit: Int
    public var remaining: Int
    public var resetSeconds: Int

    public init(limit: Int, remaining: Int, resetSeconds: Int) {
        self.limit = limit
        self.remaining = remaining
        self.resetSeconds = resetSeconds
    }
}

/// One field from a `validation_error` details array.
public struct FieldIssue: Equatable, Sendable {
    public var field: String
    public var issue: String

    public init(field: String, issue: String) {
        self.field = field
        self.issue = issue
    }
}

/// A failed gateway call.
///
/// Every 4xx and 5xx body is `Components.Schemas.GatewayErrorEnvelope`:
/// `{"error": {"code", "message", "request_id", "details"?}}`.
public struct GatewayFailure: Error, Equatable, Sendable, LocalizedError {
    public var statusCode: Int?
    public var code: String?
    public var message: String
    public var requestID: String?
    public var details: [FieldIssue]
    public var retryAfterSeconds: Int?
    public var rateLimit: RateLimit?

    public init(
        statusCode: Int?,
        code: String?,
        message: String,
        requestID: String?,
        details: [FieldIssue] = [],
        retryAfterSeconds: Int? = nil,
        rateLimit: RateLimit? = nil
    ) {
        self.statusCode = statusCode
        self.code = code
        self.message = message
        self.requestID = requestID
        self.details = details
        self.retryAfterSeconds = retryAfterSeconds
        self.rateLimit = rateLimit
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

    /// Maps the generated error envelope. `details` is present for `validation_error`.
    public static func decoded(
        statusCode: Int,
        envelope: Components.Schemas.GatewayErrorEnvelope,
        retryAfterSeconds: Int? = nil,
        rateLimit: RateLimit? = nil
    ) -> GatewayFailure {
        let body = envelope.error
        return GatewayFailure(
            statusCode: statusCode,
            code: body.code,
            message: body.message,
            requestID: body.requestId,
            details: (body.details ?? []).map { FieldIssue(field: $0.field, issue: $0.issue) },
            retryAfterSeconds: retryAfterSeconds,
            rateLimit: rateLimit
        )
    }
}
