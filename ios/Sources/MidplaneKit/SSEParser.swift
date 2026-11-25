import Foundation

/// One Server-Sent Events frame from `GET /v1/runs/{id}/events`.
///
/// The generated client returns that body as raw bytes. Payload schemas are
/// decoded separately by `RunEventDecoder`.
public struct RunStreamEvent: Hashable, Sendable {
    public var id: String?
    public var name: String?
    public var data: String
    public var retryMilliseconds: Int64?

    public init(id: String?, name: String?, data: String, retryMilliseconds: Int64? = nil) {
        self.id = id
        self.name = name
        self.data = data
        self.retryMilliseconds = retryMilliseconds
    }
}

/// Incremental parser for the HTML Server-Sent Events wire format.
///
/// The gateway stores decimal ids on the run. Reconnection is the caller's job:
/// send the last id as `Last-Event-ID`. This parser only splits frames.
public struct SSEParser: Sendable {
    private var pending: [UInt8] = []
    private var dataLines: [String] = []
    private var eventName: String?
    private var lastID: String?
    private var retry: Int64?

    public init() {}

    public mutating func append(_ chunk: String) -> [RunStreamEvent] {
        append(bytes: chunk.utf8)
    }

    public mutating func append<Bytes: Sequence>(bytes: Bytes) -> [RunStreamEvent] where Bytes.Element == UInt8 {
        pending.append(contentsOf: bytes)
        var events: [RunStreamEvent] = []
        while let line = nextLine() {
            if line.isEmpty {
                if let event = dispatch() {
                    events.append(event)
                }
                dataLines.removeAll(keepingCapacity: true)
                eventName = nil
                retry = nil
            } else if line.hasPrefix(":") {
                continue
            } else {
                consume(line)
            }
        }
        return events
    }

    private mutating func nextLine() -> String? {
        guard let index = pending.firstIndex(where: { $0 == 10 || $0 == 13 }) else {
            return nil
        }
        let lineBytes = pending[..<index]
        let line = String(decoding: lineBytes, as: UTF8.self)
        if pending[index] == 13 {
            let next = index + 1
            if next == pending.endIndex {
                return nil
            }
            pending.removeSubrange(..<(pending[next] == 10 ? next + 1 : next))
            return line
        }
        pending.removeSubrange(..<(index + 1))
        return line
    }

    private mutating func consume(_ line: String) {
        let name: String
        let value: String
        if let colon = line.firstIndex(of: ":") {
            name = String(line[..<colon])
            var rest = line[line.index(after: colon)...]
            if rest.first == " " {
                rest.removeFirst()
            }
            value = String(rest)
        } else {
            name = line
            value = ""
        }
        switch name {
        case "event":
            eventName = value
        case "data":
            dataLines.append(value)
        case "id":
            if !value.contains("\0") {
                lastID = value
            }
        case "retry":
            if !value.isEmpty, value.allSatisfy(\.isASCIIDigit) {
                retry = Int64(value)
            }
        default:
            break
        }
    }

    private func dispatch() -> RunStreamEvent? {
        guard !dataLines.isEmpty else {
            return nil
        }
        return RunStreamEvent(
            id: lastID,
            name: eventName,
            data: dataLines.joined(separator: "\n"),
            retryMilliseconds: retry
        )
    }
}

private extension Character {
    var isASCIIDigit: Bool {
        unicodeScalars.count == 1 && unicodeScalars.first.map { (48 ... 57).contains($0.value) } == true
    }
}
