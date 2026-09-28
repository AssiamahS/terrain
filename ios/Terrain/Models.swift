import Foundation

enum Scope: String, CaseIterable, Identifiable, Codable {
    case county, state, country
    var id: String { rawValue }
    var title: String {
        switch self {
        case .county: return "US counties"
        case .state: return "US states"
        case .country: return "Americas"
        }
    }
    var geometryFile: String {
        switch self {
        case .county: return "counties.geojson"
        case .state: return "states.geojson"
        case .country: return "countries.geojson"
        }
    }
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
    let metrics: [Metric]
    let suggestions: [Suggestion]
    let unavailable: [Unavailable]
}

struct MetricsFile: Codable {
    let generated: String
    let county: [String: [String: Double]]
    let state: [String: [String: Double]]
    let country: [String: [String: Double]]

    func table(_ scope: Scope) -> [String: [String: Double]] {
        switch scope {
        case .county: return county
        case .state: return state
        case .country: return country
        }
    }
}

struct Place: Identifiable, Hashable {
    let id: String     // GEOID / ISO2
    let name: String
    let st: String
    var title: String { st.isEmpty ? name : "\(name)" }
}
