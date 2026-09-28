import SwiftUI
import MapKit

/// Choropleth over MapKit. Every feature is an MKPolygon/MKMultiPolygon whose
/// fill comes from the selected metric's quantile class.
struct MapView: UIViewRepresentable {
    let features: [MKGeoJSONFeature]
    let scope: Scope
    let metric: Metric?
    let values: [String: Double]
    let breaks: [Double]
    @Binding var selected: String?
    @Binding var focus: String?

    static let ramp: [UIColor] = [
        UIColor(red: 0.16, green: 0.09, blue: 0.32, alpha: 1),
        UIColor(red: 0.33, green: 0.11, blue: 0.47, alpha: 1),
        UIColor(red: 0.53, green: 0.13, blue: 0.51, alpha: 1),
        UIColor(red: 0.74, green: 0.20, blue: 0.43, alpha: 1),
        UIColor(red: 0.90, green: 0.35, blue: 0.29, alpha: 1),
        UIColor(red: 0.98, green: 0.58, blue: 0.16, alpha: 1),
        UIColor(red: 1.00, green: 0.85, blue: 0.35, alpha: 1),
    ]

    func makeUIView(context: Context) -> MKMapView {
        let map = MKMapView()
        map.delegate = context.coordinator
        map.overrideUserInterfaceStyle = .dark
        let config = MKStandardMapConfiguration(elevationStyle: .flat, emphasisStyle: .muted)
        config.pointOfInterestFilter = .excludingAll
        config.showsTraffic = false
        map.preferredConfiguration = config
        map.isPitchEnabled = false
        map.isRotateEnabled = false
        map.showsCompass = false
        map.setRegion(MKCoordinateRegion(center: CLLocationCoordinate2D(latitude: 38.5, longitude: -96.5),
                                         span: MKCoordinateSpan(latitudeDelta: 32, longitudeDelta: 40)), animated: false)
        let tap = UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tapped(_:)))
        map.addGestureRecognizer(tap)
        return map
    }

    func updateUIView(_ map: MKMapView, context: Context) {
        let c = context.coordinator
        let key = "\(scope.id)|\(metric?.id ?? "")|\(features.count)"
        if c.overlayKey != key {
            c.overlayKey = key
            map.removeOverlays(map.overlays)
            c.overlayOwner = [:]
            var overlays: [MKOverlay] = []
            for f in features {
                guard let id = f.identifier else { continue }
                for g in f.geometry {
                    if let poly = g as? MKPolygon { overlays.append(poly); c.overlayOwner[ObjectIdentifier(poly)] = id }
                    if let multi = g as? MKMultiPolygon { overlays.append(multi); c.overlayOwner[ObjectIdentifier(multi)] = id }
                }
            }
            map.addOverlays(overlays, level: .aboveRoads)
            if scope.id != c.lastScope {
                c.lastScope = scope.id
                map.setRegion(MKCoordinateRegion(center: CLLocationCoordinate2D(latitude: scope.lat, longitude: scope.lon),
                                                 span: MKCoordinateSpan(latitudeDelta: scope.latDelta, longitudeDelta: scope.lonDelta)), animated: true)
            }
        }
        c.parent = self
        if c.selectedKey != selected {
            c.selectedKey = selected
            for o in map.overlays {
                if let r = map.renderer(for: o) as? MKOverlayPathRenderer {
                    c.style(r, for: o)
                    r.setNeedsDisplay()
                }
            }
        }
        if let focus, focus != c.lastFocus {
            c.lastFocus = focus
            if let f = features.first(where: { $0.identifier == focus }), let g = f.geometry.first as? MKOverlay {
                var rect = g.boundingMapRect
                for extra in f.geometry.dropFirst() { if let o = extra as? MKOverlay { rect = rect.union(o.boundingMapRect) } }
                map.setVisibleMapRect(rect, edgePadding: UIEdgeInsets(top: 160, left: 40, bottom: 320, right: 40), animated: true)
            }
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, MKMapViewDelegate {
        var parent: MapView
        var overlayKey = ""
        var lastScope: String?
        var lastFocus: String?
        var selectedKey: String?
        var overlayOwner: [ObjectIdentifier: String] = [:]

        init(_ parent: MapView) { self.parent = parent }

        func color(for id: String) -> UIColor {
            guard let v = parent.values[id] else { return UIColor(white: 0.18, alpha: 1) }
            var cls = 0
            for b in parent.breaks where v > b { cls += 1 }
            return MapView.ramp[min(cls, MapView.ramp.count - 1)]
        }

        func style(_ r: MKOverlayPathRenderer, for overlay: MKOverlay) {
            let id = overlayOwner[ObjectIdentifier(overlay as AnyObject)] ?? ""
            let sel = id == parent.selected
            r.fillColor = color(for: id).withAlphaComponent(sel ? 1 : 0.82)
            r.strokeColor = sel ? .white : UIColor(white: 0, alpha: 0.5)
            r.lineWidth = sel ? 2.5 : 0.4
        }

        func mapView(_ mapView: MKMapView, rendererFor overlay: MKOverlay) -> MKOverlayRenderer {
            let r: MKOverlayPathRenderer
            if let p = overlay as? MKPolygon { r = MKPolygonRenderer(polygon: p) }
            else if let m = overlay as? MKMultiPolygon { r = MKMultiPolygonRenderer(multiPolygon: m) }
            else { return MKOverlayRenderer(overlay: overlay) }
            style(r, for: overlay)
            return r
        }

        @objc func tapped(_ g: UITapGestureRecognizer) {
            guard let map = g.view as? MKMapView else { return }
            let pt = g.location(in: map)
            let coord = map.convert(pt, toCoordinateFrom: map)
            let mp = MKMapPoint(coord)
            for o in map.overlays.reversed() {
                guard o.boundingMapRect.contains(mp), let r = map.renderer(for: o) as? MKOverlayPathRenderer, let path = r.path else { continue }
                let p = r.point(for: mp)
                if path.contains(p) {
                    let id = overlayOwner[ObjectIdentifier(o as AnyObject)]
                    DispatchQueue.main.async { self.parent.selected = id }
                    return
                }
            }
            DispatchQueue.main.async { self.parent.selected = nil }
        }
    }
}
