import MidplaneKit
import SwiftUI

struct AgentsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("The roster is paged with limit and cursor. The order is the gateway roster.")
                    .font(.subheadline)
                    .foregroundStyle(Theme.ink.opacity(0.7))
                if let failure = model.agents.failure {
                    ErrorBanner(failure: failure)
                }
                ForEach(model.agents.rows) { agent in
                    AgentRowView(agent: agent)
                }
                if model.agents.nextCursor != nil {
                    Button {
                        Task { await model.loadMoreAgents() }
                    } label: {
                        Text(model.agents.isLoading ? "Loading…" : "Load more")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.copper)
                    .disabled(model.agents.isLoading)
                } else if !model.agents.rows.isEmpty {
                    Text("End of the roster.")
                        .font(.caption)
                        .foregroundStyle(Theme.ink.opacity(0.55))
                }
            }
            .padding(20)
        }
        .dejavuScreen()
        .navigationTitle("Agents")
        .navigationBarTitleDisplayMode(.inline)
    }
}
