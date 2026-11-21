import SwiftUI

@main
struct DejavuApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
        }
    }
}

enum AppRoute: Hashable {
    case create
    case run(String)
    case episode(String)
    case agents
}
