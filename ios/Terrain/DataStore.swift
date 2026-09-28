import Foundation
import MapKit
import Observation

/// Downloads the dataset from GitHub Pages, caches it on disk, and hands the
/// map ready-made MKGeoJSON features. Data refreshes without an app rebuild.
@Observable
final class DataStore {
    static let base = URL(string: "https://assiamahs.github.io/terrain/data/")!

    var catalog: Catalog?
    var metrics: MetricsFile?
    var features: [Scope: [MKGeoJSONFeature]] = [:]
    var places: [Scope: [String: Place]] = [:]
    var status = "Loading data…"
    var loading = true
    var error: String?

    private let cacheDir: URL = {
        let d = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("terrain-data")
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }()

    func load(force: Bool = false) async {
        loading = true
        error = nil
        do {
            status = "Fetching catalog…"
            let catalogData = try await fetch("catalog.json", force: force)
            catalog = try JSONDecoder().decode(Catalog.self, from: catalogData)
            status = "Fetching metrics…"
            let metricsData = try await fetch("metrics.json", force: force)
            metrics = try JSONDecoder().decode(MetricsFile.self, from: metricsData)
            for scope in Scope.allCases {
                status = "Loading \(scope.title)…"
                let data = try await fetch(scope.geometryFile, force: force)
                let objs = try MKGeoJSONDecoder().decode(data)
                var feats: [MKGeoJSONFeature] = []
                var index: [String: Place] = [:]
                for case let f as MKGeoJSONFeature in objs {
                    guard let id = f.identifier else { continue }
                    feats.append(f)
                    var name = id, st = ""
                    if let p = f.properties, let dict = try? JSONSerialization.jsonObject(with: p) as? [String: Any] {
                        name = dict["name"] as? String ?? id
                        st = dict["st"] as? String ?? ""
                    }
                    index[id] = Place(id: id, name: name, st: st)
                }
                features[scope] = feats
                places[scope] = index
            }
            status = "Ready"
        } catch {
            self.error = error.localizedDescription
            status = "Failed: \(error.localizedDescription)"
        }
        loading = false
    }

    private func fetch(_ name: String, force: Bool) async throws -> Data {
        let local = cacheDir.appendingPathComponent(name)
        if !force, let attrs = try? FileManager.default.attributesOfItem(atPath: local.path),
           let modified = attrs[.modificationDate] as? Date, Date().timeIntervalSince(modified) < 6 * 3600,
           let data = try? Data(contentsOf: local) {
            return data
        }
        do {
            let (data, resp) = try await URLSession.shared.data(from: Self.base.appendingPathComponent(name))
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
                throw URLError(.badServerResponse)
            }
            try? data.write(to: local)
            return data
        } catch {
            if let data = try? Data(contentsOf: local) { return data }
            throw error
        }
    }

    func value(_ metric: String, for id: String, scope: Scope) -> Double? {
        metrics?.table(scope)[id]?[metric]
    }

    func metric(_ id: String) -> Metric? {
        catalog?.metrics.first { $0.id == id }
    }

    /// All ids with a value for the metric, sorted.
    func ranking(_ metric: String, scope: Scope, ascending: Bool = false) -> [(Place, Double)] {
        guard let table = metrics?.table(scope), let index = places[scope] else { return [] }
        var out: [(Place, Double)] = []
        for (id, row) in table {
            if let v = row[metric], let p = index[id] { out.append((p, v)) }
        }
        out.sort { ascending ? $0.1 < $1.1 : $0.1 > $1.1 }
        return out
    }

    /// Quantile breaks (7 classes) so a few outliers do not flatten the map.
    func breaks(_ metric: String, scope: Scope) -> [Double] {
        let vals = ranking(metric, scope: scope, ascending: true).map(\.1)
        guard vals.count > 8 else { return vals }
        return (1..<7).map { vals[min(vals.count - 1, vals.count * $0 / 7)] }
    }
}
