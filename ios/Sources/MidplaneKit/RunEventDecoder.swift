import Foundation
import MidplaneGateway

/// A decoded `data` payload. The generator streams the event-stream body as bytes
/// and does not select among these schemas, so the client decodes by event name.
public enum DecodedRunEvent: Equatable, Sendable {
    case runStarted(Components.Schemas.RunStartedData)
    case nodeStarted(Components.Schemas.NodeStartedData)
    case nodeCompleted(Components.Schemas.NodeCompletedData)
    case runCompleted(Components.Schemas.RunCompletedData)
    case runFailed(Components.Schemas.RunFailedData)
}

public enum RunEventDecoder {
    public static func decode(name: String?, data: String) -> DecodedRunEvent? {
        guard let name, let payload = data.data(using: .utf8) else {
            return nil
        }
        let decoder = JSONDecoder()
        switch name {
        case Components.Schemas.RunEvent.EventPayload.run_started.rawValue:
            return (try? decoder.decode(Components.Schemas.RunStartedData.self, from: payload)).map(DecodedRunEvent.runStarted)
        case Components.Schemas.RunEvent.EventPayload.node_started.rawValue:
            return (try? decoder.decode(Components.Schemas.NodeStartedData.self, from: payload)).map(DecodedRunEvent.nodeStarted)
        case Components.Schemas.RunEvent.EventPayload.node_completed.rawValue:
            return (try? decoder.decode(Components.Schemas.NodeCompletedData.self, from: payload)).map(DecodedRunEvent.nodeCompleted)
        case Components.Schemas.RunEvent.EventPayload.run_completed.rawValue:
            return (try? decoder.decode(Components.Schemas.RunCompletedData.self, from: payload)).map(DecodedRunEvent.runCompleted)
        case Components.Schemas.RunEvent.EventPayload.run_failed.rawValue:
            return (try? decoder.decode(Components.Schemas.RunFailedData.self, from: payload)).map(DecodedRunEvent.runFailed)
        default:
            return nil
        }
    }
}

enum EventResume {
    /// Same rule as the gateway: a missing, non-integer, or unknown id replays from the start.
    static func startIndex(events: [RunStreamEvent], lastEventID: String?) -> Int {
        guard let header = lastEventID?.trimmingCharacters(in: .whitespacesAndNewlines), !header.isEmpty else {
            return 0
        }
        guard let requested = Int(header) else {
            return 0
        }
        let known = Set(events.compactMap { $0.id.flatMap(Int.init) })
        guard known.contains(requested) else {
            return 0
        }
        guard let index = events.firstIndex(where: { Int($0.id ?? "") == requested }) else {
            return 0
        }
        return index + 1
    }
}
