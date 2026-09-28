import Foundation
import MapKit
import Observation

/// Downloads the dataset from GitHub Pages, caches it on disk, and hands the
/// map ready-made MKGeoJSON features. Data refreshes without an app rebuild.
/// Geometry for a scope loads the first time that scope is shown.
@Observable
final class DataStore {
    static let base = URL(string: "https://assiamahs.github.io/terrain/data/")!

    var catalog: Catalog?
    var metrics: MetricsFile?
    var features: [String: [MKGeoJSONFeature]] = [:]
    var places: [String: [String: Place]] = [:]
    var centroids: [String: [String: CLLocationCoordinate2D]] = [:]
    var status = "Loading data…"
    var loading = true
    var error: String?

    private let cacheDir: URL = {
        let d = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("terrain-data")
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }()

    var scopes: [Scope] { catalog?.allScopes ?? [Scope.fallback] }
    func scope(_ id: String) -> Scope { scopes.first { $0.id == id } ?? scopes.first ?? Scope.fallback }

    func load(force: Bool = false) async {
        loading = true
        error = nil
        do {
            status = "Fetching catalog…"
            catalog = try JSONDecoder().decode(Catalog.self, from: try await fetch("catalog.json", force: force))
            status = "Fetching metrics…"
            metrics = try JSONDecoder().decode(MetricsFile.self, from: try await fetch("metrics.json", force: force))
            if force { features = [:]; places = [:]; centroids = [:] }
            try await loadGeometry(for: scopes.first ?? Scope.fallback)
            status = "Ready"
        } catch {
            self.error = error.localizedDescription
            status = "Failed: \(error.localizedDescription)"
        }
        loading = false
    }

    func ensureGeometry(_ scope: Scope) async {
        guard features[scope.id] == nil else { return }
        loading = true
        status = "Loading \(scope.title)…"
        do { try await loadGeometry(for: scope) } catch { self.error = error.localizedDescription }
        loading = false
    }

    private func loadGeometry(for scope: Scope) async throws {
        let data = try await fetch(scope.geometry, force: false)
        let objs = try MKGeoJSONDecoder().decode(data)
        var feats: [MKGeoJSONFeature] = []
        var index: [String: Place] = [:]
        var cents: [String: CLLocationCoordinate2D] = [:]
        for case let f as MKGeoJSONFeature in objs {
            guard let id = f.identifier else { continue }
            feats.append(f)
            var name = id, st = ""
            if let p = f.properties, let dict = try? JSONSerialization.jsonObject(with: p) as? [String: Any] {
                name = dict["name"] as? String ?? id
                st = dict["st"] as? String ?? ""
            }
            index[id] = Place(id: id, name: name, st: st)
            // centroid of the largest polygon, for the 3D bars
            var best: MKPolygon?
            for g in f.geometry {
                if let p = g as? MKPolygon, p.pointCount > (best?.pointCount ?? 0) { best = p }
                if let m = g as? MKMultiPolygon {
                    for p in m.polygons where p.pointCount > (best?.pointCount ?? 0) { best = p }
                }
            }
            if let best {
                let pts = best.points()
                var x = 0.0, y = 0.0
                for i in 0..<best.pointCount { x += pts[i].x; y += pts[i].y }
                cents[id] = MKMapPoint(x: x / Double(best.pointCount), y: y / Double(best.pointCount)).coordinate
            }
        }
        features[scope.id] = feats
        places[scope.id] = index
        centroids[scope.id] = cents
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
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { throw URLError(.badServerResponse) }
            try? data.write(to: local)
            return data
        } catch {
            if let data = try? Data(contentsOf: local) { return data }
            throw error
        }
    }

    func value(_ metric: String, for id: String, scope: String) -> Double? {
        metrics?.table(scope)[id]?[metric]
    }

    func metric(_ id: String) -> Metric? { catalog?.metrics.first { $0.id == id } }

    func values(_ metric: String, scope: String) -> [String: Double] {
        metrics?.table(scope).compactMapValues { $0[metric] } ?? [:]
    }

    func ranking(_ metric: String, scope: String, ascending: Bool = false) -> [(Place, Double)] {
        guard let index = places[scope] else { return [] }
        var out: [(Place, Double)] = []
        for (id, row) in metrics?.table(scope) ?? [:] {
            if let v = row[metric], let p = index[id] { out.append((p, v)) }
        }
        out.sort { ascending ? $0.1 < $1.1 : $0.1 > $1.1 }
        return out
    }

    /// Quantile breaks (7 classes) so a few outliers do not flatten the map.
    func breaks(_ metric: String, scope: String) -> [Double] {
        let vals = (metrics?.table(scope) ?? [:]).compactMap { $0.value[metric] }.sorted()
        guard vals.count > 8 else { return vals }
        return (1..<7).map { vals[min(vals.count - 1, vals.count * $0 / 7)] }
    }
}
