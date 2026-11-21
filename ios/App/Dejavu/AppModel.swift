import Foundation
import MidplaneKit

@MainActor
final class AppModel: ObservableObject {
    @Published var mode: ClientMode = .mock
    @Published var baseURL: String = MidplaneConfiguration.localCompose.absoluteString
    @Published var connection: ConnectionState = .unknown
    @Published var agents: AgentsState = .empty
    @Published var recentRunIDs: [String] = []
    @Published var hasAPIKey = false

    static let pageSize = 4

    let keyStore: any APIKeyStore
    private var clientCache: (mode: ClientMode, baseURL: String, client: any MidplaneClient)?

    init(keyStore: (any APIKeyStore)? = nil) {
        if let keyStore {
            self.keyStore = keyStore
        } else {
            self.keyStore = KeychainAPIKeyStore()
        }
        refreshKeyPresence()
    }

    func client() -> any MidplaneClient {
        if let clientCache, clientCache.mode == mode, clientCache.baseURL == baseURL {
            return clientCache.client
        }
        let made = MidplaneClientFactory.make(
            mode: mode,
            baseURL: URL(string: baseURL) ?? MidplaneConfiguration.localCompose,
            keyStore: keyStore
        )
        clientCache = (mode, baseURL, made)
        return made
    }

    func refreshKeyPresence() {
        let stored = (try? keyStore.load())?.trimmingCharacters(in: .whitespacesAndNewlines)
        hasAPIKey = stored?.isEmpty == false
    }

    func saveAPIKey(_ key: String) {
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            try? keyStore.clear()
        } else {
            try? keyStore.save(trimmed)
        }
        refreshKeyPresence()
    }

    func clearAPIKey() {
        try? keyStore.clear()
        refreshKeyPresence()
    }

    func applySettings() async {
        clientCache = nil
        await refresh()
    }

    func refresh() async {
        let current = client()
        var state = connection
        for action in await ConnectionLoader.refresh(current) {
            state = ConnectionReducer.reduce(state, action)
        }
        connection = state
        await reloadAgents()
    }

    func reloadAgents() async {
        agents = AgentsReducer.loading(AgentsState.empty)
        do {
            let page = try await client().listAgents(limit: Self.pageSize, cursor: nil)
            agents = AgentsReducer.apply(.empty, page: page, appending: false)
        } catch let failure as GatewayFailure {
            agents = AgentsReducer.failed(.empty, failure)
        } catch {
            agents = AgentsReducer.failed(
                .empty,
                GatewayFailure(statusCode: nil, code: nil, message: error.localizedDescription, requestID: nil)
            )
        }
    }

    func loadMoreAgents() async {
        guard let cursor = agents.nextCursor, !agents.isLoading else {
            return
        }
        agents = AgentsReducer.loading(agents)
        do {
            let page = try await client().listAgents(limit: Self.pageSize, cursor: cursor)
            agents = AgentsReducer.apply(agents, page: page, appending: true)
        } catch let failure as GatewayFailure {
            agents = AgentsReducer.failed(agents, failure)
        } catch {
            agents = AgentsReducer.failed(
                agents,
                GatewayFailure(statusCode: nil, code: nil, message: error.localizedDescription, requestID: nil)
            )
        }
    }

    func remember(runID: String) {
        recentRunIDs.removeAll { $0 == runID }
        recentRunIDs.insert(runID, at: 0)
    }
}
