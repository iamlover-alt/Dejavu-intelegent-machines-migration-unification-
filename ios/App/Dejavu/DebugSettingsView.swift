import SwiftUI
import MidplaneKit

struct DebugSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var draftKey = ""
    @State private var savedNote: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Client") {
                    Picker("Mode", selection: $model.mode) {
                        Text("Mock").tag(ClientMode.mock)
                        Text("Live").tag(ClientMode.live)
                    }
                    .pickerStyle(.segmented)
                    TextField("Base URL", text: $model.baseURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    Text("Default is http://localhost:8000, the port from docker compose.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Bearer key") {
                    SecureField("ios-dev API key", text: $draftKey)
                    Text(model.hasAPIKey ? "A key is stored in the Keychain." : "No key stored.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Save key") {
                        model.saveAPIKey(draftKey)
                        draftKey = ""
                        savedNote = model.hasAPIKey ? "Saved. The key is not written into the repo." : "Cleared."
                    }
                    Button("Remove key", role: .destructive) {
                        model.clearAPIKey()
                        draftKey = ""
                        savedNote = "Removed."
                    }
                    if let savedNote {
                        Text(savedNote)
                            .font(.caption)
                    }
                    Text("docker compose up --build prints the dev key as “dev api key:”. Mint another with python -m midplane.gateway.keys mint --client ios-dev. GET /healthz does not use the key. Every /v1 call does.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Debug")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        Task {
                            await model.applySettings()
                            dismiss()
                        }
                    }
                }
            }
        }
    }
}
