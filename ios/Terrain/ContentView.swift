import SwiftUI
import MapKit

struct ContentView: View {
    @Environment(DataStore.self) private var store
    @AppStorage("scope") private var scopeID = "county"
    @AppStorage("metric") private var metricID = "age20s_pct"
    @AppStorage("mode3d") private var mode3D = false
    @State private var selected: String?
    @State private var focus: String?
    @State private var showPicker = false
    @State private var showRanking = false
    @State private var ascending = false
    @State private var snapshot: MKMapSnapshotter.Snapshot?
    @State private var bars: [TerrainView.Bar] = []
    @State private var building3D = false

    private var scope: Scope { store.scope(scopeID) }
    private var metric: Metric? { store.metric(metricID) }
    private var values: [String: Double] { store.values(metricID, scope: scopeID) }
    private var breaks: [Double] { store.breaks(metricID, scope: scopeID) }

    var body: some View {
        ZStack(alignment: .top) {
            Group {
                if mode3D, let snapshot {
                    TerrainView(snapshot: snapshot, bars: bars, selected: $selected)
                } else {
                    MapView(features: store.features[scopeID] ?? [], scope: scope, metric: metric,
                            values: values, breaks: breaks, selected: $selected, focus: $focus)
                }
            }
            .ignoresSafeArea()

            VStack(spacing: 10) {
                header
                Spacer()
                if let selected, let place = store.places[scopeID]?[selected] {
                    DetailCard(place: place, scope: scopeID, primary: metricID) { self.selected = nil }
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                } else if let metric {
                    Legend(metric: metric, breaks: breaks)
                }
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 12)

            if store.loading || building3D {
                VStack(spacing: 8) {
                    ProgressView()
                    Text(building3D ? "Building terrain…" : store.status).font(.footnote).foregroundStyle(.secondary)
                }
                .padding(20).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
                .frame(maxHeight: .infinity)
            } else if let err = store.error {
                VStack(spacing: 10) {
                    Text("Could not load data").font(.headline)
                    Text(err).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    Button("Retry") { Task { await store.load(force: true) } }.buttonStyle(.borderedProminent)
                }
                .padding(20).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
                .frame(maxHeight: .infinity)
            }
        }
        .animation(.snappy, value: selected)
        .sheet(isPresented: $showPicker) {
            MetricPicker(scope: scopeID, current: metricID) { m, s, asc in
                metricID = m
                scopeID = s
                ascending = asc
                selected = nil
                showPicker = false
            }
            .presentationDetents([.large])
        }
        .sheet(isPresented: $showRanking) {
            RankingView(scope: scopeID, metricID: metricID, ascending: $ascending) { id in
                selected = id
                focus = id
                showRanking = false
            }
            .presentationDetents([.medium, .large])
        }
        .task(id: scopeID) {
            await store.ensureGeometry(scope)
            if let m = metric, !m.scopes.contains(scopeID) {
                metricID = store.catalog?.metrics.first { $0.scopes.contains(scopeID) }?.id ?? metricID
            }
            if mode3D { await build3D() }
        }
        .task(id: "\(mode3D)|\(metricID)|\(store.loading)") {
            if mode3D, !store.loading { await build3D() }
        }
    }

    private var header: some View {
        VStack(spacing: 8) {
            HStack(spacing: 8) {
                Button { showPicker = true } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "slider.horizontal.3")
                        VStack(alignment: .leading, spacing: 1) {
                            Text(metric?.label ?? "Pick a filter").font(.headline).lineLimit(1)
                            Text(scope.title).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .glassEffect(in: RoundedRectangle(cornerRadius: 18))
                .buttonStyle(.plain)

                Button { mode3D.toggle(); selected = nil } label: {
                    Image(systemName: mode3D ? "map" : "cube").font(.title3).padding(12)
                }
                .glassEffect(.regular, in: Circle())
                .buttonStyle(.plain)

                Button { showRanking = true } label: {
                    Image(systemName: "list.number").font(.title3).padding(12)
                }
                .glassEffect(.regular, in: Circle())
                .buttonStyle(.plain)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(store.scopes) { s in
                        Button(s.title) { scopeID = s.id; selected = nil }
                            .font(.footnote.weight(s.id == scopeID ? .bold : .regular))
                            .padding(.horizontal, 12).padding(.vertical, 7)
                            .glassEffect(s.id == scopeID ? .regular.tint(Color(MapView.ramp[5]).opacity(0.7)) : .regular, in: Capsule())
                            .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    /// Snapshot the scope's region and turn the metric into bars.
    private func build3D() async {
        guard let cents = store.centroids[scopeID], !cents.isEmpty else { return }
        building3D = true
        defer { building3D = false }
        let size = CGSize(width: 1400, height: 1400)
        let center = CLLocationCoordinate2D(latitude: scope.lat, longitude: scope.lon)
        let span = MKCoordinateSpan(latitudeDelta: scope.latDelta, longitudeDelta: scope.lonDelta)
        guard let snap = try? await MapSnapshot.take(center: center, span: span, size: size) else { return }
        let vals = values
        let b = breaks
        let sorted = vals.values.sorted()
        // height on a rank scale so a few huge counties do not flatten everyone else
        var rank: [Double: Double] = [:]
        for (i, v) in sorted.enumerated() { rank[v] = Double(i) / Double(max(1, sorted.count - 1)) }
        var out: [TerrainView.Bar] = []
        for (id, c) in cents {
            guard let v = vals[id] else { continue }
            let p = snap.point(for: c)
            guard p.x >= 0, p.y >= 0, p.x <= size.width, p.y <= size.height else { continue }
            var cls = 0
            for br in b where v > br { cls += 1 }
            out.append(TerrainView.Bar(id: id, x: p.x, y: p.y, height: 0.05 + 0.95 * (rank[v] ?? 0), color: MapView.ramp[min(cls, MapView.ramp.count - 1)]))
        }
        snapshot = snap
        bars = out
    }
}

struct Legend: View {
    let metric: Metric
    let breaks: [Double]
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(metric.about).font(.footnote).foregroundStyle(.secondary).lineLimit(2)
            HStack(spacing: 2) {
                ForEach(0..<MapView.ramp.count, id: \.self) { i in
                    Rectangle().fill(Color(MapView.ramp[i])).frame(height: 10)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 3))
            HStack {
                Text(breaks.first.map(metric.format) ?? "low")
                Spacer()
                Text(breaks.last.map(metric.format) ?? "high")
            }
            .font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
            Text(metric.source).font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(14)
        .glassEffect(in: RoundedRectangle(cornerRadius: 18))
    }
}

struct DetailCard: View {
    @Environment(DataStore.self) private var store
    let place: Place
    let scope: String
    let primary: String
    let dismiss: () -> Void

    private var rows: [(Metric, Double)] {
        guard let cat = store.catalog, let table = store.metrics?.table(scope)[place.id] else { return [] }
        return cat.metrics.compactMap { m in table[m.id].map { (m, $0) } }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading) {
                    Text(place.name).font(.title3.bold()).lineLimit(1)
                    if let m = store.metric(primary), let v = store.value(primary, for: place.id, scope: scope) {
                        Text("\(m.label): \(m.format(v))").font(.subheadline).foregroundStyle(Color(MapView.ramp[5]))
                    }
                }
                Spacer()
                Button(action: dismiss) { Image(systemName: "xmark.circle.fill").font(.title2).foregroundStyle(.secondary) }
            }
            ScrollView {
                let groups = Dictionary(grouping: rows, by: { $0.0.group })
                ForEach(Catalog.groupOrder.filter { groups[$0] != nil }, id: \.self) { g in
                    Text(g.uppercased()).font(.caption2.bold()).foregroundStyle(.secondary).padding(.top, 6)
                    ForEach(groups[g] ?? [], id: \.0.id) { m, v in
                        HStack {
                            Text(m.label).font(.subheadline)
                            Spacer()
                            Text(m.format(v)).font(.subheadline.monospacedDigit().bold())
                        }
                    }
                }
            }
            .frame(maxHeight: 260)
        }
        .padding(16)
        .glassEffect(in: RoundedRectangle(cornerRadius: 20))
    }
}

struct MetricPicker: View {
    @Environment(DataStore.self) private var store
    let scope: String
    let current: String
    let pick: (String, String, Bool) -> Void
    @State private var query = ""

    private var metrics: [Metric] {
        let all = store.catalog?.metrics ?? []
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return all.filter { q.isEmpty || $0.label.lowercased().contains(q) || $0.about.lowercased().contains(q) || $0.group.lowercased().contains(q) }
    }

    private func scopeTitle(_ id: String) -> String { store.scope(id).title }

    var body: some View {
        NavigationStack {
            List {
                if query.isEmpty, let sugg = store.catalog?.suggestions, !sugg.isEmpty {
                    Section("Suggestions") {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 8) {
                                ForEach(sugg) { s in
                                    Button(s.title) { pick(s.metric, s.scope, s.ascending ?? false) }
                                        .buttonStyle(.bordered).tint(Color(MapView.ramp[5])).font(.footnote)
                                }
                            }
                        }
                        .listRowInsets(EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 12))
                    }
                }
                let groups = Dictionary(grouping: metrics, by: \.group)
                ForEach(Catalog.groupOrder, id: \.self) { g in
                    if let items = groups[g] {
                        Section(g) {
                            ForEach(items) { m in
                                Button {
                                    let target = m.scopes.contains(scope) ? scope : (m.scopes.first ?? scope)
                                    pick(m.id, target, false)
                                } label: {
                                    HStack {
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(m.label).foregroundStyle(.primary)
                                            Text(m.about).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                                        }
                                        Spacer()
                                        if m.id == current { Image(systemName: "checkmark").foregroundStyle(Color(MapView.ramp[5])) }
                                        else if !m.scopes.contains(scope) {
                                            Text(m.scopes.map(scopeTitle).joined(separator: ", ")).font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                if query.isEmpty, let un = store.catalog?.unavailable, !un.isEmpty {
                    Section("Asked for, no public data yet") {
                        ForEach(un) { u in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(u.topic).font(.subheadline)
                                Text(u.why).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                if let gen = store.catalog?.generated {
                    Section { Text("Data built \(gen.prefix(10))").font(.caption).foregroundStyle(.tertiary) }
                }
            }
            .searchable(text: $query, prompt: "Search filters")
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

struct RankingView: View {
    @Environment(DataStore.self) private var store
    let scope: String
    let metricID: String
    @Binding var ascending: Bool
    let open: (String) -> Void

    var body: some View {
        NavigationStack {
            let m = store.metric(metricID)
            List {
                ForEach(Array(store.ranking(metricID, scope: scope, ascending: ascending).prefix(100).enumerated()), id: \.offset) { i, row in
                    Button { open(row.0.id) } label: {
                        HStack {
                            Text("\(i + 1)").font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 28, alignment: .trailing)
                            VStack(alignment: .leading) {
                                Text(row.0.name).foregroundStyle(.primary)
                                if !row.0.st.isEmpty { Text(row.0.st).font(.caption).foregroundStyle(.secondary) }
                            }
                            Spacer()
                            Text(m?.format(row.1) ?? "\(row.1)").font(.subheadline.monospacedDigit().bold())
                        }
                    }
                }
            }
            .navigationTitle(m?.label ?? "Ranking")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(ascending ? "Lowest first" : "Highest first") { ascending.toggle() }.font(.footnote)
                }
            }
        }
    }
}
