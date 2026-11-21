import Foundation
import MidplaneGateway

/// Fields the YAML defines and swift-openapi-generator 1.14 drops.
///
/// `anyOf: [<type>, {type: null}]` is skipped, so `AgentPage` has no `next_cursor`
/// and `RunStatusResponse` has no `result`, `episode_id`, `client_ref`, or `error`.
/// The list operation also loses its `cursor` query. These wrappers keep the YAML
/// fields beside the generated values. See `CONTRACT_QUESTIONS.md`.
public struct AgentList: Sendable, Equatable {
    public var page: Components.Schemas.AgentPage
    public var nextCursor: String?

    public init(page: Components.Schemas.AgentPage, nextCursor: String?) {
        self.page = page
        self.nextCursor = nextCursor
    }
}

public struct RunRecord: Sendable, Equatable {
    public var response: Components.Schemas.RunStatusResponse
    public var episodeID: String?
    public var resultJSON: String?
    public var clientRef: String?
    public var errorMessage: String?

    public init(
        response: Components.Schemas.RunStatusResponse,
        episodeID: String?,
        resultJSON: String?,
        clientRef: String?,
        errorMessage: String?
    ) {
        self.response = response
        self.episodeID = episodeID
        self.resultJSON = resultJSON
        self.clientRef = clientRef
        self.errorMessage = errorMessage
    }
}

final class ResponseCapture: @unchecked Sendable {
    private let lock = NSLock()
    private var nextCursorStorage: String?
    private var sawCursor = false
    private var episodeIDStorage: String?
    private var resultStorage: String?
    private var clientRefStorage: String?
    private var errorStorage: String?

    var nextCursor: String? {
        lock.lock()
        defer { lock.unlock() }
        return sawCursor ? nextCursorStorage : nil
    }

    var episodeID: String? {
        lock.lock()
        defer { lock.unlock() }
        return episodeIDStorage
    }

    var resultJSON: String? {
        lock.lock()
        defer { lock.unlock() }
        return resultStorage
    }

    var clientRef: String? {
        lock.lock()
        defer { lock.unlock() }
        return clientRefStorage
    }

    var runError: String? {
        lock.lock()
        defer { lock.unlock() }
        return errorStorage
    }

    func read(operationID: String, data: Data) {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return
        }
        lock.lock()
        defer { lock.unlock() }
        if operationID == Operations.ListAgentsV1AgentsGet.id, object.keys.contains("next_cursor") {
            sawCursor = true
            nextCursorStorage = object["next_cursor"] as? String
        }
        if operationID == Operations.GetRunV1RunsRunIdGet.id {
            episodeIDStorage = object["episode_id"] as? String
            clientRefStorage = object["client_ref"] as? String
            errorStorage = object["error"] as? String
            if let result = object["result"], !(result is NSNull) {
                resultStorage = JSONDisplay.prettyJSONObject(result)
            }
        }
    }
}

struct GatewayCall: Sendable {
    var requestID: String
    var clientRef: String?
    var cursor: String?
    var capture: ResponseCapture

    init(
        requestID: String,
        clientRef: String? = nil,
        cursor: String? = nil,
        capture: ResponseCapture = ResponseCapture()
    ) {
        self.requestID = requestID
        self.clientRef = clientRef
        self.cursor = cursor
        self.capture = capture
    }
}

enum GatewayContext {
    @TaskLocal static var current: GatewayCall?
}
