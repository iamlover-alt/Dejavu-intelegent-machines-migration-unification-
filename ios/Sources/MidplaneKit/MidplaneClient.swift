import Foundation
import MidplaneGateway

@_exported import MidplaneGateway

/// App-facing gateway. JSON calls and the event-stream body go through the
/// generated `MidplaneGateway.Client`. Frame parsing stays local because the
/// generator returns the `text/event-stream` body as raw bytes.
public protocol MidplaneClient: Sendable {
    func healthz() async throws -> Components.Schemas.HealthResponse
    func createRun(_ body: Components.Schemas.CreateRunRequest) async throws -> Components.Schemas.CreateRunResponse
    func getRun(id: String) async throws -> Components.Schemas.RunStatusResponse
    func runEvents(id: String, lastEventID: String?) async -> AsyncThrowingStream<RunStreamEvent, Error>
    func getEpisode(id: String) async throws -> Components.Schemas.EpisodeResponse
    func listAgents(limit: Int?, cursor: String?) async throws -> Components.Schemas.AgentPage
    /// Rate-limit snapshot from the most recent authenticated response, when the server sent one.
    func latestRateLimit() async -> RateLimit?
}
