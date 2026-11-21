import SwiftUI
import MidplaneKit

struct EpisodeDetailView: View {
    @EnvironmentObject private var model: AppModel
    var episodeID: String

    @State private var state = EpisodeDetailState.idle

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if state.phase == .loading {
                    ProgressView("Loading episode")
                }
                if let failure = state.failure {
                    ErrorBanner(failure: failure)
                }
                if state.phase == .loaded {
                    metrics
                    block("Outputs", state.outputs)
                    block("Cost", state.cost)
                    block("Trace", state.trace)
                    DisclosureGroup("Inputs, plan, tools, models") {
                        VStack(alignment: .leading, spacing: 12) {
                            block("Inputs", state.inputs)
                            block("Plan", state.plan)
                            block("Tool calls", state.toolCalls)
                            block("Model choices", state.modelChoices)
                        }
                        .padding(.top, 8)
                    }
                    .font(.subheadline.weight(.semibold))
                }
            }
            .padding(20)
        }
        .dejavuScreen()
        .navigationTitle("Episode")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: episodeID) {
            await load()
        }
    }

    private var metrics: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(state.episodeID ?? episodeID)
                .font(.caption.monospaced())
                .textSelection(.enabled)
            LabeledValue(label: "Source", value: state.source ?? "—")
            LabeledValue(label: "Outcome", value: state.outcome ?? "—")
            LabeledValue(label: "Quality", value: state.qualityScore ?? "—")
            LabeledValue(label: "Latency", value: latency)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private var latency: String {
        if let latencyMS = state.latencyMS {
            return "\(latencyMS) ms"
        }
        return "—"
    }

    private func block(_ title: String, _ text: String?) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.headline)
            Text(text?.isEmpty == false ? text! : "—")
                .font(.caption.monospaced())
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(Theme.card)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
    }

    private func load() async {
        let actions = await EpisodeDetailLoader.load(id: episodeID, client: model.client())
        state = EpisodeDetailReducer.fold(actions)
    }
}
