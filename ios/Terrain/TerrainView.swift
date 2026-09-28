import SwiftUI
import SceneKit
import MapKit

/// The Human Terrain look: a snapshot of the map as the ground, one extruded bar
/// per place, height and color from the metric. Orbit with one finger, pinch to zoom.
struct TerrainView: UIViewRepresentable {
    let snapshot: MKMapSnapshotter.Snapshot
    let bars: [Bar]            // built by ContentView from centroids + values
    @Binding var selected: String?

    struct Bar {
        let id: String
        let x: CGFloat         // pixel position in the snapshot image
        let y: CGFloat
        let height: Double     // 0…1
        let color: UIColor
    }

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = .black
        view.antialiasingMode = .multisampling2X
        view.allowsCameraControl = true
        view.defaultCameraController.interactionMode = .orbitTurntable
        view.defaultCameraController.maximumVerticalAngle = 85
        view.defaultCameraController.minimumVerticalAngle = 10
        view.autoenablesDefaultLighting = false
        view.scene = context.coordinator.build(snapshot: snapshot, bars: bars)
        let tap = UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tapped(_:)))
        view.addGestureRecognizer(tap)
        return view
    }

    func updateUIView(_ view: SCNView, context: Context) {
        let c = context.coordinator
        c.parent = self
        if c.builtKey != c.key(snapshot: snapshot, bars: bars) {
            view.scene = c.build(snapshot: snapshot, bars: bars)
        }
        c.highlight(selected)
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject {
        var parent: TerrainView
        var builtKey = ""
        var nodes: [String: SCNNode] = [:]
        var highlighted: String?

        init(_ parent: TerrainView) { self.parent = parent }

        func key(snapshot: MKMapSnapshotter.Snapshot, bars: [Bar]) -> String {
            "\(ObjectIdentifier(snapshot).hashValue)|\(bars.count)|\(bars.first?.id ?? "")|\(bars.first?.height ?? 0)"
        }

        func build(snapshot: MKMapSnapshotter.Snapshot, bars: [Bar]) -> SCNScene {
            builtKey = key(snapshot: snapshot, bars: bars)
            nodes = [:]
            let scene = SCNScene()
            let img = snapshot.image
            let w = img.size.width, h = img.size.height
            let scale = 100.0 / max(w, h)           // scene units: longest side = 100
            let plane = SCNPlane(width: w * scale, height: h * scale)
            plane.firstMaterial?.diffuse.contents = img
            plane.firstMaterial?.lightingModel = .constant
            let ground = SCNNode(geometry: plane)
            ground.eulerAngles.x = -.pi / 2
            scene.rootNode.addChildNode(ground)

            let maxH = 22.0
            let barW = max(0.35, 100.0 / sqrt(Double(max(bars.count, 1))) * 0.55)
            for b in bars where b.height > 0.01 {
                let hgt = b.height * maxH
                let box = SCNBox(width: barW, height: hgt, length: barW, chamferRadius: 0)
                let m = SCNMaterial()
                m.diffuse.contents = b.color
                m.lightingModel = .lambert
                box.materials = [m]
                let n = SCNNode(geometry: box)
                n.position = SCNVector3((b.x - w / 2) * scale, hgt / 2, (b.y - h / 2) * scale)
                n.name = b.id
                scene.rootNode.addChildNode(n)
                nodes[b.id] = n
            }
            let light = SCNNode()
            light.light = SCNLight(); light.light?.type = .directional; light.light?.intensity = 900
            light.eulerAngles = SCNVector3(-1.0, 0.6, 0)
            scene.rootNode.addChildNode(light)
            let ambient = SCNNode()
            ambient.light = SCNLight(); ambient.light?.type = .ambient; ambient.light?.intensity = 500
            scene.rootNode.addChildNode(ambient)
            let cam = SCNNode()
            cam.camera = SCNCamera(); cam.camera?.zFar = 1000; cam.camera?.fieldOfView = 50
            cam.position = SCNVector3(0, 70, 95)
            cam.look(at: SCNVector3(0, 0, 0))
            scene.rootNode.addChildNode(cam)
            return scene
        }

        func highlight(_ id: String?) {
            if let old = highlighted, let n = nodes[old] { n.geometry?.firstMaterial?.emission.contents = UIColor.black }
            highlighted = id
            if let id, let n = nodes[id] { n.geometry?.firstMaterial?.emission.contents = UIColor(white: 0.6, alpha: 1) }
        }

        @objc func tapped(_ g: UITapGestureRecognizer) {
            guard let view = g.view as? SCNView else { return }
            let hits = view.hitTest(g.location(in: view), options: [.searchMode: SCNHitTestSearchMode.closest.rawValue])
            let id = hits.first?.node.name
            DispatchQueue.main.async { self.parent.selected = id }
        }
    }
}

/// Renders a dark map image of a region for the 3D ground plane.
enum MapSnapshot {
    static func take(center: CLLocationCoordinate2D, span: MKCoordinateSpan, size: CGSize) async throws -> MKMapSnapshotter.Snapshot {
        let opts = MKMapSnapshotter.Options()
        opts.region = MKCoordinateRegion(center: center, span: span)
        opts.size = size
        opts.traitCollection = UITraitCollection(userInterfaceStyle: .dark)
        let cfg = MKStandardMapConfiguration(elevationStyle: .flat, emphasisStyle: .muted)
        cfg.pointOfInterestFilter = .excludingAll
        opts.preferredConfiguration = cfg
        return try await MKMapSnapshotter(options: opts).start()
    }
}
