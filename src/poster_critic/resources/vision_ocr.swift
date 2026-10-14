// A4-owned local Vision bridge. Only supplied image pixels are recognized.
import Foundation
import Vision

func emit(_ value: [String: Any]) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
}

do {
    guard CommandLine.arguments.count == 4 else {
        throw NSError(domain: "A4Vision", code: 1)
    }
    let mode = CommandLine.arguments[1]
    let imagePath = CommandLine.arguments[2]
    let languages = CommandLine.arguments[3].split(separator: ",").map(String.init)
    guard !languages.isEmpty else { throw NSError(domain: "A4Vision", code: 2) }
    let request = VNRecognizeTextRequest()
    request.revision = VNRecognizeTextRequestRevision3
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.automaticallyDetectsLanguage = false
    request.customWords = []
    let supported = try request.supportedRecognitionLanguages()
    guard languages.allSatisfy({ supported.contains($0) }) else {
        throw NSError(domain: "A4Vision", code: 3)
    }
    request.recognitionLanguages = languages
    let producer: [String: Any] = [
        "engine": "apple_vision",
        "request_revision": request.revision,
        "os_version": ProcessInfo.processInfo.operatingSystemVersionString,
        "languages": languages,
        "language_correction": false,
        "recognition_level": "accurate"
    ]
    if mode == "probe" {
        try emit(["schema_version": 1, "producer": producer,
                  "supported_languages": supported, "lines": []])
    } else if mode == "recognize" {
        let image = try Data(contentsOf: URL(fileURLWithPath: imagePath))
        let handler = VNImageRequestHandler(data: image, orientation: .up, options: [:])
        try handler.perform([request])
        let lines: [[String: Any]] = (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let box = observation.boundingBox
            return ["text": candidate.string, "confidence": candidate.confidence,
                    "bbox_bottom_left": [box.minX, box.minY, box.maxX, box.maxY]]
        }
        try emit(["schema_version": 1, "producer": producer, "lines": lines])
    } else {
        throw NSError(domain: "A4Vision", code: 4)
    }
} catch {
    // Do not echo image paths, observed strings or raw platform exceptions.
    let code = (error as NSError).code
    FileHandle.standardError.write(Data("a4_vision_execution_failed code=\(code)\n".utf8))
    exit(2)
}
