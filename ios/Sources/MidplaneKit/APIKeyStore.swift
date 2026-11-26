import Foundation

/// Stores the `ios-dev` bearer token outside the repository.
public protocol APIKeyStore: Sendable {
    func load() throws -> String?
    func save(_ key: String) throws
    func clear() throws
}

public final class InMemoryAPIKeyStore: APIKeyStore, @unchecked Sendable {
    private let lock = NSLock()
    private var stored: String?

    public init(key: String? = nil) {
        stored = key
    }

    public func load() throws -> String? {
        lock.lock()
        defer { lock.unlock() }
        return stored
    }

    public func save(_ key: String) throws {
        lock.lock()
        defer { lock.unlock() }
        stored = key
    }

    public func clear() throws {
        lock.lock()
        defer { lock.unlock() }
        stored = nil
    }
}

#if os(iOS)
import Security

/// Keychain placeholder for the bearer token. The token is never written into the repo.
public struct KeychainAPIKeyStore: APIKeyStore {
    public static let service = "com.dejavu.intelligentmachines.ios"
    public static let account = "midplane-ios-dev"

    public init() {}

    public func load() throws -> String? {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess, let data = item as? Data else {
            throw KeychainFailure(status: status)
        }
        return String(data: data, encoding: .utf8)
    }

    public func save(_ key: String) throws {
        try clear()
        var query = baseQuery()
        query[kSecValueData as String] = Data(key.utf8)
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw KeychainFailure(status: status)
        }
    }

    public func clear() throws {
        let status = SecItemDelete(baseQuery() as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainFailure(status: status)
        }
    }

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: Self.account,
        ]
    }
}

public struct KeychainFailure: Error, Equatable, Sendable {
    public var status: OSStatus
}
#endif
