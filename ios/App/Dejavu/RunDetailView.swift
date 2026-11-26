import SwiftUI
import MidplaneKit

struct RunDetailView: View {
    @EnvironmentObject private var model: AppModel
    var runID: String

    @State private var state = RunDetailState.idle

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text(runID)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                    Spacer()
                    StatusBadge(status: state.status)
                }
                if let taskType = state.taskType {
                    LabeledValue(label: "Task", value: taskType)
                }
                if let clientRef = state.clientRef {
                    LabeledValue(label: "Client ref", value: clientRef)
                }
                if let runError = state.runError {
                    LabeledValue(label: "Run error", value: runError)
                }
                if let failure = state.failure {
                    ErrorBanner(failure: failure)
                    Button("Retry stream") {
                        Task { await load(resuming: true) }
                    }
                    .buttonStyle(.bordered)
                }
                if let result = state.resultJSON {
                    section("Result", text: result)
                }
                timeline
                if let episodeID = state.episodeID {
                    NavigationLink(value: AppRoute.episode(episodeID)) {
                        Text("Open episode")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.copper)
                } else if state.phase == .finished {
                    Text("No episode id on this run.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.ink.opacity(0.65))
                }
            }
            .padding(20)
        }
        .dejavuScreen()
        .navigationTitle("Run")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: runID) {
            await load()
        }
    }

    private var timeline: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Events")
                .font(.headline)
            if state.events.isEmpty {
                Text(state.phase == .loading ? "Loading…" : "Waiting for events.")
                    .font(.subheadline)
                    .foregroundStyle(Theme.ink.opacity(0.65))
            } else {
                ForEach(Array(state.events.enumerated()), id: \.offset) { _, event in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(event.name ?? "event")
                                .font(.subheadline.weight(.semibold))
                            if let id = event.id {
                                Text("#\(id)")
                                    .font(.caption.monospaced())
                                    .foregroundStyle(Theme.ink.opacity(0.5))
                            }
                        }
                        Text(JSONDisplayBridge.pretty(event.data))
                            .font(.caption.monospaced())
                            .foregroundStyle(Theme.ink.opacity(0.8))
                            .textSelection(.enabled)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .background(Theme.card)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
            }
        }
    }

    private func section(_ title: String, text: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.headline)
            Text(text)
                .font(.caption.monospaced())
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(Theme.card)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
    }

    private func load(resuming: Bool = false) async {
        let lastEventID = resuming ? state.events.compactMap(\.id).last : nil
        if !resuming {
            state = .idle
        }
        let current = model.client()
        for await action in RunDetailLoader.load(runID: runID, client: current, lastEventID: lastEventID) {
            state = RunDetailReducer.reduce(state, action)
        }
    }
}

enum JSONDisplayBridge {
    static func pretty(_ raw: String) -> String {
        guard let data = raw.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data),
            JSONSerialization.isValidJSONObject(object),
            let pretty = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]),
            let text = String(data: pretty, encoding: .utf8)
        else {
            return raw
        }
        return text
    }
}

struct LabeledValue: View {
    var label: String
    var value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.ink.opacity(0.55))
                .frame(width: 88, alignment: .leading)
            Text(value)
                .font(.subheadline)
                .textSelection(.enabled)
        }
    }
}
