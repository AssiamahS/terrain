import SwiftUI

@main
struct TerrainApp: App {
    @State private var store = DataStore()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(store)
                .preferredColorScheme(.dark)
                .task { await store.load() }
        }
    }
}
