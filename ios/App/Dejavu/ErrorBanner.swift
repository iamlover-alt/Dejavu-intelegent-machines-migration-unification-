import SwiftUI
import MidplaneKit

struct ErrorBanner: View {
    var failure: GatewayFailure

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(failure.message)
                .font(.subheadline)
                .foregroundStyle(Theme.clay)
            if let requestID = failure.requestID {
                Text("Request ID \(requestID)")
                    .font(.caption.monospaced())
                    .foregroundStyle(Theme.ink.opacity(0.7))
                    .textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Theme.clay.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

struct ConnectionPill: View {
    var state: ConnectionState

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
            Text(title)
                .font(.caption.weight(.semibold))
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(color.opacity(0.12))
        .clipShape(Capsule())
        .accessibilityLabel(title)
    }

    private var title: String {
        switch state.phase {
        case .unknown:
            return "Not checked"
        case .checking:
            return "Checking gateway"
        case .reachable:
            return "Gateway ok"
        case .failed:
            return "Gateway unreachable"
        }
    }

    private var color: Color {
        switch state.phase {
        case .reachable:
            return Theme.pine
        case .failed:
            return Theme.clay
        case .checking:
            return Theme.copper
        case .unknown:
            return Theme.ink.opacity(0.45)
        }
    }
}

struct StatusBadge: View {
    var status: String?

    var body: some View {
        Text(status ?? "unknown")
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Theme.status(status).opacity(0.14))
            .foregroundStyle(Theme.status(status))
            .clipShape(Capsule())
    }
}
