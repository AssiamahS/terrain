import SwiftUI
import MapKit

struct ContentView: View {
    @Environment(DataStore.self) private var store
    @AppStorage("scope") private var scopeRaw = Scope.county.rawValue
    @AppStorage("metric") private var metricID = "age20s_pct"
    @State private var selected: String?
    @State private var focus: String?
    @State private var showPicker = false
    @State private var showRanking = false
    @State private var ascending = false

    private var scope: Scope { Scope(rawValue: scopeRaw) ?? .county }
    private var metric: Metric? { store.metric(metricID) }
    private var values: [String: Double] {
        guard let table = store.metrics?.table(scope) else { return [:] }
        return table.compactMapValues { $0[metricID] }
    }

    var body: some View {
        ZStack(alignment: .top) {
            MapView(features: store.features[scope] ?? [], scope: scope, metric: metric,
                    values: values, breaks: store.breaks(metricID, scope: scope),
                    selected: $selected, focus: $focus)
                .ignoresSafeArea()

            VStack(spacing: 10) {
                header
                Spacer()
                if let selected, let place = store.places[scope]?[selected] {
                    DetailCard(place: place, scope: scope, primary: metricID) { self.selected = nil }
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                } else if let metric {
                    Legend(metric: metric, breaks: store.breaks(metricID, scope: scope))
                }
            }
            .padding(.horizontal, 14)
            .padding(.bottom, 12)

            if store.loading {
                VStack(spacing: 8) {
                    ProgressView()
                    Text(store.status).font(.footnote).foregroundStyle(.secondary)
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
            MetricPicker(scope: scope, current: metricID) { m, s, asc in
                metricID = m
                scopeRaw = s.rawValue
                ascending = asc
                selected = nil
                showPicker = false
            }
            .presentationDetents([.large])
        }
        .sheet(isPresented: $showRanking) {
            RankingView(scope: scope, metricID: metricID, ascending: $ascending) { id in
                selected = id
                focus = id
                showRanking = false
            }
            .presentationDetents([.medium, .large])
        }
        .onChange(of: scopeRaw) { _, _ in
            if let m = metric, !m.scopes.contains(scope.rawValue) {
                metricID = store.catalog?.metrics.first { $0.scopes.contains(scope.rawValue) }?.id ?? metricID
            }
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

                Button { showRanking = true } label: {
                    Image(systemName: "list.number").font(.title3).padding(12)
                }
                .glassEffect(.regular, in: Circle())
                .buttonStyle(.plain)
            }
            Picker("Scope", selection: $scopeRaw) {
                ForEach(Scope.allCases) { s in Text(s.title).tag(s.rawValue) }
            }
            .pickerStyle(.segmented)
        }
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
    let scope: Scope
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
                ForEach(groups.keys.sorted(), id: \.self) { g in
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
    let scope: Scope
    let current: String
    let pick: (String, Scope, Bool) -> Void
    @State private var query = ""

    private var metrics: [Metric] {
        let all = store.catalog?.metrics ?? []
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return all.filter { q.isEmpty || $0.label.lowercased().contains(q) || $0.about.lowercased().contains(q) || $0.group.lowercased().contains(q) }
    }

    var body: some View {
        NavigationStack {
            List {
                if query.isEmpty, let sugg = store.catalog?.suggestions, !sugg.isEmpty {
                    Section("Suggestions") {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 8) {
                                ForEach(sugg) { s in
                                    Button(s.title) { pick(s.metric, Scope(rawValue: s.scope) ?? scope, s.ascending ?? false) }
                                        .buttonStyle(.bordered).tint(Color(MapView.ramp[5])).font(.footnote)
                                }
                            }
                        }
                        .listRowInsets(EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 12))
                    }
                }
                let groups = Dictionary(grouping: metrics, by: \.group)
                ForEach(["People", "Sex & relationships", "Money", "Nightlife & business", "Crime & civic"], id: \.self) { g in
                    if let items = groups[g] {
                        Section(g) {
                            ForEach(items) { m in
                                Button {
                                    let target = m.scopes.contains(scope.rawValue) ? scope : Scope(rawValue: m.scopes.first ?? "county") ?? .county
                                    pick(m.id, target, false)
                                } label: {
                                    HStack {
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(m.label).foregroundStyle(.primary)
                                            Text(m.about).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                                        }
                                        Spacer()
                                        if m.id == current { Image(systemName: "checkmark").foregroundStyle(Color(MapView.ramp[5])) }
                                        else if !m.scopes.contains(scope.rawValue) {
                                            Text(m.scopes.contains("country") ? "Americas" : "US").font(.caption2).foregroundStyle(.tertiary)
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
    let scope: Scope
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
