import Foundation

/// A map layer: US counties, US states, Canada, Mexico, Brazil, the Americas.
/// Defined by the catalog so new countries appear without an app update.
struct Scope: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let geometry: String
    let lat: Double
    let lon: Double
    let latDelta: Double
    let lonDelta: Double

    static let fallback = Scope(id: "county", title: "US counties", geometry: "counties.geojson", lat: 38.5, lon: -96.5, latDelta: 32, lonDelta: 40)
}

struct Metric: Codable, Identifiable, Hashable {
    let id: String
    let label: String
    let group: String
    let unit: String      // pct | ratio | money | count | rate | years
    let about: String
    let source: String
    let scopes: [String]

    func format(_ v: Double) -> String {
        switch unit {
        case "pct": return String(format: v < 10 ? "%.1f%%" : "%.0f%%", v)
        case "ratio": return String(format: "%.2f : 1", v)
        case "money":
            if v >= 1000 { return "$" + Int(v).formatted(.number.grouping(.automatic)) }
            return String(format: "$%.0f", v)
        case "years": return String(format: "%.1f yrs", v)
        case "rate": return v < 10 ? String(format: "%.2f", v) : String(format: "%.0f", v)
        default:
            if v >= 100_000 { return Int(v).formatted(.number.notation(.compactName)) }
            if v >= 1000 { return Int(v).formatted(.number.grouping(.automatic)) }
            return v.truncatingRemainder(dividingBy: 1) == 0 ? String(format: "%.0f", v) : String(format: "%.1f", v)
        }
    }
}

struct Suggestion: Codable, Identifiable, Hashable {
    let title: String
    let metric: String
    let scope: String
    var ascending: Bool? = nil
    var id: String { title }
}

struct Unavailable: Codable, Identifiable, Hashable {
    let topic: String
    let why: String
    var id: String { topic }
}

struct Catalog: Codable {
    let generated: String
    var scopes: [Scope]? = nil
    let metrics: [Metric]
    let suggestions: [Suggestion]
    let unavailable: [Unavailable]

    var allScopes: [Scope] { scopes ?? [Scope.fallback] }
    static let groupOrder = ["People", "Sex & relationships", "Looks", "Money", "Nightlife & business", "Crime & civic"]
}

/// metrics.json: { generated, <scope id>: { <place id>: { <metric id>: value } } }
struct MetricsFile: Decodable {
    let generated: String
    let tables: [String: [String: [String: Double]]]

    struct DynamicKey: CodingKey {
        var stringValue: String
        var intValue: Int? { nil }
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { nil }
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: DynamicKey.self)
        var gen = ""
        var t: [String: [String: [String: Double]]] = [:]
        for key in c.allKeys {
            if key.stringValue == "generated" {
                gen = try c.decode(String.self, forKey: key)
            } else if let table = try? c.decode([String: [String: Double]].self, forKey: key) {
                t[key.stringValue] = table
            }
        }
        generated = gen
        tables = t
    }

    func table(_ scope: String) -> [String: [String: Double]] { tables[scope] ?? [:] }
}

struct Place: Identifiable, Hashable {
    let id: String
    let name: String
    let st: String
}
