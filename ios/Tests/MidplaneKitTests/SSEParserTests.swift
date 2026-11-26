import XCTest

@testable import MidplaneKit

final class SSEParserTests: XCTestCase {
    func testParsesTheGatewayNodeFrame() {
        var parser = SSEParser()
        let events = parser.append(
            "id: 1\nevent: node\ndata: {\"at\":\"2026-11-21T00:00:00+00:00\",\"node\":\"supervisor\",\"status\":\"completed\"}\n\n"
        )
        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].id, "1")
        XCTAssertEqual(events[0].name, "node")
        XCTAssertTrue(events[0].data.contains("\"node\":\"supervisor\""))
    }

    func testJoinsMultilineDataAndIgnoresComments() {
        var parser = SSEParser()
        let events = parser.append(": keep-alive\nevent: node\ndata: {\"a\":1}\ndata: {\"b\":2}\n\n")
        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].data, "{\"a\":1}\n{\"b\":2}")
        XCTAssertNil(events[0].id)
    }

    func testDoesNotDispatchAFrameWithoutData() {
        var parser = SSEParser()
        XCTAssertEqual(parser.append("event: node\n\n"), [])
    }

    func testHoldsAnIncompleteFrame() {
        var parser = SSEParser()
        XCTAssertEqual(parser.append("id: 2\nevent: status\ndata: {"), [])
        let events = parser.append("\"status\":\"succeeded\"}\n\n")
        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].id, "2")
        XCTAssertEqual(events[0].name, "status")
    }

    func testCRLFAndASplitCarriageReturn() {
        var parser = SSEParser()
        XCTAssertEqual(parser.append("id: 3\r"), [])
        let events = parser.append("\ndata: hi\r\n\r\n")
        XCTAssertEqual(events.count, 1, "events=\(events)")
        guard events.count == 1 else { return }
        XCTAssertEqual(events[0].id, "3")
        XCTAssertEqual(events[0].data, "hi")
    }

    func testLastIDPersistsUntilReplacedAndRetryIsParsed() {
        var parser = SSEParser()
        let events = parser.append("id: 4\ndata: one\nretry: 1500\n\ndata: two\n\nid: 9\ndata: three\n\n")
        XCTAssertEqual(events.map(\.id), ["4", "4", "9"])
        XCTAssertEqual(events.map(\.data), ["one", "two", "three"])
        XCTAssertEqual(events[0].retryMilliseconds, 1500)
        XCTAssertNil(events[1].retryMilliseconds)
    }

    func testIgnoresAnIDContainingNullAndANonNumericRetry() {
        var parser = SSEParser()
        let events = parser.append("id: bad\u{0}id\nretry: 12ms\ndata: ok\n\n")
        XCTAssertEqual(events.count, 1)
        XCTAssertNil(events[0].id)
        XCTAssertNil(events[0].retryMilliseconds)
    }

    func testStripsOneLeadingSpaceFromTheFieldValue() {
        var parser = SSEParser()
        let events = parser.append("event: node\ndata:  spaced\n\n")
        XCTAssertEqual(events[0].data, " spaced")
    }
}
