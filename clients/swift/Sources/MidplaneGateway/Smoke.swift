import Foundation
import OpenAPIRuntime
import OpenAPIURLSession

enum GatewaySmoke {
    /// Referenced so the generated client has to compile, not only be parsed.
    static func make(baseURL: URL = URL(string: "http://localhost:8000")!) throws -> Client {
        Client(
            serverURL: baseURL,
            transport: URLSessionTransport()
        )
    }
}
