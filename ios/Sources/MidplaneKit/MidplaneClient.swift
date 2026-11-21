import Foundation
import MidplaneGateway

@_exported import MidplaneGateway

/// App-facing gateway. JSON calls go through the generated `MidplaneGateway.Client`.
/// Run events are streamed separately because the generated events operation is
/// declared as `application/json` with an empty schema.
public protocol MidplaneClient: Sendable {
    func healthz() async throws -> Components.Schemas.HealthResponse
    func createRun(
        _ body: Components.Schemas.CreateRunRequest,
        clientRef: String?
    ) async throws -> Components.Schemas.CreateRunResponse
    func getRun(id: String) async throws -> RunRecord
    func runEvents(id: String) async -> AsyncThrowingStream<RunStreamEvent, Error>
    func getEpisode(id: String) async throws -> Components.Schemas.EpisodeResponse
    func listAgents(limit: Int?, cursor: String?) async throws -> AgentList
}
