import SwiftUI

enum Theme {
    static let ink = Color(red: 0.11, green: 0.10, blue: 0.08)
    static let paper = Color(red: 0.96, green: 0.94, blue: 0.89)
    static let card = Color(red: 0.99, green: 0.97, blue: 0.94)
    static let copper = Color(red: 0.68, green: 0.33, blue: 0.14)
    static let pine = Color(red: 0.15, green: 0.40, blue: 0.30)
    static let clay = Color(red: 0.55, green: 0.22, blue: 0.16)
    static let rule = Color(red: 0.11, green: 0.10, blue: 0.08).opacity(0.12)

    static func status(_ value: String?) -> Color {
        switch value {
        case "succeeded":
            return pine
        case "failed":
            return clay
        case "running", "queued":
            return copper
        default:
            return ink.opacity(0.55)
        }
    }
}

struct ScreenBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .background(Theme.paper.ignoresSafeArea())
    }
}

extension View {
    func dejavuScreen() -> some View {
        modifier(ScreenBackground())
    }
}
