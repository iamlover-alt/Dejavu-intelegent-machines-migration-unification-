import MidplaneKit
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @State private var path = NavigationPath()
    @State private var showSettings = false

    var body: some View {
        NavigationStack(path: $path) {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    header
                    if let failure = model.connection.failure {
                        ErrorBanner(failure: failure)
                    }
                    newRun
                    recent
                    agentsPreview
                }
                .padding(20)
            }
            .dejavuScreen()
            .navigationTitle("Dejavú")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "slider.horizontal.3")
                    }
                    .accessibilityLabel("Debug settings")
                }
            }
            .navigationDestination(for: AppRoute.self) { route in
                switch route {
                case .create:
                    CreateRunView { runID in
                        model.remember(runID: runID)
                        path.append(AppRoute.run(runID))
                    }
                case .run(let id):
                    RunDetailView(runID: id)
                case .episode(let id):
                    EpisodeDetailView(episodeID: id)
                case .agents:
                    AgentsView()
                }
            }
            .sheet(isPresented: $showSettings) {
                DebugSettingsView()
                    .environmentObject(model)
            }
            .task {
                await model.refresh()
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Reviews run on the mid-plane. This app starts them and reads the episode back.")
                .font(.subheadline)
                .foregroundStyle(Theme.ink.opacity(0.72))
            HStack {
                ConnectionPill(state: model.connection)
                Text(model.mode == .mock ? "Mock" : "Live")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.ink.opacity(0.6))
            }
        }
    }

    private var newRun: some View {
        Button {
            path.append(AppRoute.create)
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("New code review")
                        .font(.title3.weight(.semibold))
                    Text("Send a diff. The gateway chooses the model.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.ink.opacity(0.7))
                }
                Spacer()
                Image(systemName: "arrow.right")
            }
            .padding(16)
            .background(Theme.copper)
            .foregroundStyle(Theme.card)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    private var recent: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("This session")
                .font(.headline)
            if model.recentRunIDs.isEmpty {
                Text("Runs you create show up here. The API has no list-runs route, so this list is not reloaded later.")
                    .font(.subheadline)
                    .foregroundStyle(Theme.ink.opacity(0.65))
            } else {
                ForEach(model.recentRunIDs, id: \.self) { id in
                    Button {
                        path.append(AppRoute.run(id))
                    } label: {
                        HStack {
                            Text(id)
                                .font(.caption.monospaced())
                                .lineLimit(1)
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption)
                        }
                        .padding(12)
                        .background(Theme.card)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var agentsPreview: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Agents")
                    .font(.headline)
                Spacer()
                Button("See all") {
                    path.append(AppRoute.agents)
                }
                .font(.subheadline.weight(.semibold))
            }
            if let failure = model.agents.failure, model.agents.rows.isEmpty {
                ErrorBanner(failure: failure)
            } else if model.agents.rows.isEmpty {
                Text(model.agents.isLoading ? "Loading the roster…" : "No agents yet.")
                    .font(.subheadline)
                    .foregroundStyle(Theme.ink.opacity(0.65))
            } else {
                ForEach(Array(model.agents.rows.prefix(3))) { agent in
                    AgentRowView(agent: agent)
                }
            }
        }
    }
}

struct AgentRowView: View {
    var agent: AgentsState.Row

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(agent.title)
                    .font(.body.weight(.semibold))
                Text(agent.name)
                    .font(.caption.monospaced())
                    .foregroundStyle(Theme.ink.opacity(0.6))
            }
            Spacer()
            Text(agent.status)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.pine)
        }
        .padding(12)
        .background(Theme.card)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}
