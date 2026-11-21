import SwiftUI
import MidplaneKit

struct CreateRunView: View {
    @EnvironmentObject private var model: AppModel
    var onCreated: (String) -> Void

    @State private var state = CreateRunState.empty
    @State private var isWorking = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("v0 accepts one task type, code_review. You send the diff. The server sets source to app and chooses the model and tools.")
                    .font(.subheadline)
                    .foregroundStyle(Theme.ink.opacity(0.72))

                labeled("Diff") {
                    TextEditor(text: $state.diff)
                        .font(.caption.monospaced())
                        .frame(minHeight: 180)
                        .padding(8)
                        .background(Theme.card)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
                Button("Use a sample diff") {
                    state.diff = Self.sampleDiff
                }
                .font(.subheadline.weight(.semibold))

                labeled("Repo") {
                    TextField("local", text: $state.repo)
                        .textFieldStyle(.roundedBorder)
                }
                labeled("Instructions") {
                    TextField("Review this diff.", text: $state.instructions, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                }
                labeled("Client ref (optional)") {
                    TextField("draft-1", text: $state.clientRef)
                        .textFieldStyle(.roundedBorder)
                }

                if let failure = state.failure {
                    ErrorBanner(failure: failure)
                }

                Button {
                    Task { await submit() }
                } label: {
                    Text(isWorking ? "Creating…" : "Create run")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.ink)
                .disabled(isWorking)
            }
            .padding(20)
        }
        .dejavuScreen()
        .navigationTitle("New review")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func labeled(_ title: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.ink.opacity(0.6))
            content()
        }
    }

    private func submit() async {
        isWorking = true
        defer { isWorking = false }
        let actions = await CreateRunLoader.submit(state, client: model.client())
        state = CreateRunReducer.fold(actions, from: state)
        if case .created(let runID, _) = state.phase {
            onCreated(runID)
        }
    }

    private static let sampleDiff = """
    diff --git a/hello.py b/hello.py
    --- a/hello.py
    +++ b/hello.py
    @@ -1 +1,2 @@
    -print("hi")
    +print("hi")
    +password = "hunter2"
    """
}
