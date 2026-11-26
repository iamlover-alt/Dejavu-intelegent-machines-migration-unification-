import Foundation
import OpenAPIRuntime

enum JSONDisplay {
    static func pretty(_ value: some Encodable) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(value), let text = String(data: data, encoding: .utf8) else {
            return ""
        }
        return text
    }

    static func prettyJSONObject(_ value: Any) -> String? {
        guard JSONSerialization.isValidJSONObject(value),
            let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]),
            let text = String(data: data, encoding: .utf8)
        else {
            return nil
        }
        return text
    }

    static func prettyRaw(_ data: String) -> String {
        guard let raw = data.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: raw),
            let text = prettyJSONObject(object)
        else {
            return data
        }
        return text
    }
}
