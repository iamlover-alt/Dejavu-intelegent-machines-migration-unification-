import Foundation

public struct MidplaneConfiguration: Sendable, Equatable {
    /// `servers[0]` in `openapi/midplane-v0.yaml`, which is the compose port.
    public static let localCompose = URL(string: "http://localhost:8000")!

    public var baseURL: URL

    public init(baseURL: URL = MidplaneConfiguration.localCompose) {
        self.baseURL = baseURL
    }
}

public enum ClientMode: String, Sendable, Equatable, CaseIterable {
    case mock
    case live
}

public protocol MidplaneLog: Sendable {
    func log(_ line: String)
}

public struct StandardMidplaneLog: MidplaneLog {
    public init() {}

    public func log(_ line: String) {
        print("midplane \(line)")
    }
}
