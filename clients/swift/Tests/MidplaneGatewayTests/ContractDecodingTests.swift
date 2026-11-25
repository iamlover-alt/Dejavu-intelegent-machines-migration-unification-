import XCTest

@testable import MidplaneGateway

/// Fails the macOS job when swift-openapi-generator drops nullable fields.
final class ContractDecodingTests: XCTestCase {
    func testRunStatusDecodesNullAndAbsentFields() throws {
        let withNulls = """
        {"run_id":"run-1","status":"queued","task_type":"code_review","result":null,"episode_id":null,"client_ref":null,"error":null}
        """.data(using: .utf8)!
        let nulled = try JSONDecoder().decode(Components.Schemas.RunStatusResponse.self, from: withNulls)
        XCTAssertNil(nulled.result)
        XCTAssertNil(nulled.episodeId)
        XCTAssertNil(nulled.clientRef)
        XCTAssertNil(nulled.error)

        let absent = """
        {"run_id":"run-1","status":"queued","task_type":"code_review"}
        """.data(using: .utf8)!
        let omitted = try JSONDecoder().decode(Components.Schemas.RunStatusResponse.self, from: absent)
        XCTAssertNil(omitted.episodeId)
        XCTAssertNil(omitted.clientRef)
        XCTAssertNil(omitted.result)
        XCTAssertNil(omitted.error)
    }

    func testCreateRunAndAgentPageKeepOptionalFields() throws {
        let request = """
        {"task_type":"code_review","input":{"diff":"+x"},"client_ref":null}
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(Components.Schemas.CreateRunRequest.self, from: request)
        XCTAssertNil(decoded.clientRef)

        let page = """
        {"data":[],"limit":50,"next_cursor":null}
        """.data(using: .utf8)!
        let agents = try JSONDecoder().decode(Components.Schemas.AgentPage.self, from: page)
        XCTAssertNil(agents.nextCursor)

        let pageWithoutCursor = """
        {"data":[],"limit":50}
        """.data(using: .utf8)!
        let omitted = try JSONDecoder().decode(Components.Schemas.AgentPage.self, from: pageWithoutCursor)
        XCTAssertNil(omitted.nextCursor)
    }

    func testTerminalEventPayloadsDecodeNullEpisodeId() throws {
        let failed = """
        {"run_id":"run-1","status":"failed","episode_id":null,"error":"boom","at":"2026-11-25T00:00:00.000000Z"}
        """.data(using: .utf8)!
        let payload = try JSONDecoder().decode(Components.Schemas.RunFailedData.self, from: failed)
        XCTAssertNil(payload.episodeId)
        XCTAssertEqual(payload.error, "boom")
    }
}
