// Finds faces in still images with Apple Vision and rates how good each
// is as a portrait (VNDetectFaceCaptureQualityRequest: sharpness, open
// eyes, pose, lighting). Used by covers/face_vision.py to pick speaker
// photos for the cover.
//
// Usage: whispered-face-helper IMAGE...
// Prints one JSON object per image, one per line:
//   {"path": "...", "faces": [{"x":0.39,"y":0.25,"w":0.21,"h":0.38,"quality":0.61}]}
// Boxes are fractions of the image, origin top-left. An unreadable image
// gets "error" instead of "faces". Everything stays on this Mac.

import AppKit
import Foundation
import Vision

func describe(_ path: String) -> [String: Any] {
    guard let image = NSImage(contentsOfFile: path),
          let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        return ["path": path, "error": "unreadable image"]
    }
    let request = VNDetectFaceCaptureQualityRequest()
    do {
        try VNImageRequestHandler(cgImage: cgImage).perform([request])
    } catch {
        return ["path": path, "error": error.localizedDescription]
    }
    let faces: [[String: Any]] = (request.results ?? []).map { face in
        let box = face.boundingBox  // normalised, origin bottom-left
        return [
            "x": Double(box.minX),
            "y": Double(1 - box.maxY),
            "w": Double(box.width),
            "h": Double(box.height),
            "quality": Double(face.faceCaptureQuality ?? 0),
        ]
    }
    return ["path": path, "faces": faces]
}

for path in CommandLine.arguments.dropFirst() {
    let line = describe(path)
    if let data = try? JSONSerialization.data(withJSONObject: line),
       let text = String(data: data, encoding: .utf8) {
        print(text)
        fflush(stdout)
    }
}
