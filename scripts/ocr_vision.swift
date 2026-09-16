import Foundation
import AppKit
import Vision
import CoreImage

struct OCRLine: Codable {
    let text: String
    let x: Double
    let y: Double
    let w: Double
    let h: Double
    let conf: Double
}

struct OCRResult: Codable {
    let path: String
    let text: String
    let rotation: Int
    let confidence: Double
    let engine: String
    let lines: [OCRLine]
    let score: Double
    let margin: Double
}

let recipeKeywords: Set<String> = [
    "cup", "cups", "tbsp", "tsp", "teaspoon", "tablespoon", "ounce", "ounces",
    "bake", "oven", "salt", "pepper", "chopped", "minced", "sliced", "diced",
    "flour", "butter", "sugar", "egg", "eggs", "cream", "mix", "add", "stir",
    "onion", "garlic", "cheese", "milk", "water", "oil", "preheat", "simmer",
    "cook", "beat", "fold", "pour", "serve", "minutes", "hour", "casserole",
    "saute", "blend", "cover", "boil", "whisk", "dough", "batter"
]

func die(_ msg: String) -> Never {
    fputs(msg + "\n", stderr)
    exit(1)
}

func loadCGImage(url: URL) -> CGImage? {
    let opts: [CFString: Any] = [
        kCGImageSourceShouldCache: true,
        kCGImageSourceShouldAllowFloat: false,
    ]
    guard let src = CGImageSourceCreateWithURL(url as CFURL, opts as CFDictionary) else {
        return nil
    }
    return CGImageSourceCreateImageAtIndex(src, 0, opts as CFDictionary)
}

let customWords: [String] = [
    "Baked", "Eggplant", "onion", "onions", "tomato", "tomatoes", "chopped", "sliced",
    "diced", "minced", "grated", "crushed", "basil", "oregano", "cheese", "parmesan",
    "garlic", "butter", "flour", "sugar", "salt", "pepper", "milk", "cream", "egg",
    "eggs", "water", "oil", "vinegar", "mustard", "paprika", "cayenne", "thyme",
    "rosemary", "parsley", "celery", "carrot", "carrots", "potato", "potatoes",
    "chicken", "beef", "pork", "ham", "tuna", "salmon", "soup", "stew", "casserole",
    "saute", "simmer", "bake", "preheat", "tablespoon", "teaspoon", "cup", "ounce",
    "pound", "serves", "servings", "zucchini", "broccoli", "spinach", "mushroom",
    "mushrooms", "wine", "brandy", "port", "Worcestershire", "bouillon", "margarine",
    "Layer", "Beat", "Stir", "Add", "Mix", "Cook", "Cover", "Blend", "Heat",
    "Peanut", "Vegetable", "Surprise", "Portuguese", "Frango",
]

func preprocess(_ image: CGImage) -> CGImage {
    let scale: CGFloat = image.width < 1400 ? 2.0 : 1.4
    if abs(scale - 1) < 0.05 { return image }
    let ci = CIImage(cgImage: image).transformed(by: CGAffineTransform(scaleX: scale, y: scale))
    let ctx = CIContext(options: [.useSoftwareRenderer: false])
    let _ = image.colorSpace
    return ctx.createCGImage(ci, from: ci.extent.integral) ?? image
}

func rotateImage(_ image: CGImage, degrees: Int) -> CGImage {
    let d = ((degrees % 360) + 360) % 360
    if d == 0 { return image }
    let radians = CGFloat(d) * .pi / 180.0
    // Clockwise in view coordinates is clockwise on the image when using CIImage
    // with a negative angle (CI y-up). 90 CW => -90 degrees.
    let angle: CGFloat = -radians
    let ci = CIImage(cgImage: image)
    let rotated = ci.transformed(by: CGAffineTransform(rotationAngle: angle))
    let extent = rotated.extent.integral
    let translated = rotated.transformed(by: CGAffineTransform(translationX: -extent.origin.x, y: -extent.origin.y))
    let ctx = CIContext(options: [.useSoftwareRenderer: false])
    let cs = CGColorSpaceCreateDeviceRGB()
    return ctx.createCGImage(translated, from: translated.extent.integral, format: .RGBA8, colorSpace: cs) ?? image
}

func recognize(cgImage: CGImage) -> (text: String, conf: Double, lines: [OCRLine]) {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["en-US"]
    request.customWords = customWords
    request.minimumTextHeight = 0.008
    let handler = VNImageRequestHandler(cgImage: cgImage, orientation: .up, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return ("", 0, [])
    }
    let observations = request.results ?? []
    var lines: [OCRLine] = []
    var confs: [Double] = []
    for obs in observations {
        guard let top = obs.topCandidates(1).first else { continue }
        let bb = obs.boundingBox
        let conf = Double(top.confidence)
        confs.append(conf)
        let yTop = 1.0 - Double(bb.origin.y) - Double(bb.size.height)
        lines.append(OCRLine(
            text: top.string,
            x: Double(bb.origin.x),
            y: yTop,
            w: Double(bb.size.width),
            h: Double(bb.size.height),
            conf: conf
        ))
    }
    lines.sort { a, b in
        if abs(a.y - b.y) > 0.02 { return a.y < b.y }
        return a.x < b.x
    }
    let text = lines.map { $0.text }.joined(separator: "\n")
    let mean = confs.isEmpty ? 0.0 : confs.reduce(0, +) / Double(confs.count)
    return (text, mean, lines)
}

func score(text: String, conf: Double, lines: [OCRLine], rot: Int, portrait: Bool) -> Double {
    let lower = text.lowercased()
    let tokens = lower.split { !$0.isLetter }.map(String.init)
    guard !tokens.isEmpty else { return (portrait && (rot == 90 || rot == 270)) ? 0.2 : 0 }
    let hits = tokens.reduce(into: 0) { acc, t in
        if recipeKeywords.contains(t) { acc += 1 }
    }
    let lengthScore = min(Double(text.count) / 180.0, 1.5)
    let keywordScore = min(Double(hits) / 8.0, 1.5)
    let aspect: Double
    if lines.isEmpty {
        aspect = 0
    } else {
        aspect = lines.map { $0.w / max($0.h, 0.001) }.reduce(0, +) / Double(lines.count)
    }
    var s = conf * 2.0 + lengthScore + keywordScore + min(aspect, 10) * 0.45
    // Portrait FastFoto scans hold landscape handwriting — 90 or 270 makes them readable.
    if portrait && (rot == 90 || rot == 270) {
        s += 2.8
    }
    if rot == 90 { s += 0.25 }
    return s
}

func ocrPath(_ path: String, uprightCheck: Bool = false) -> OCRResult {
    let url = URL(fileURLWithPath: path)
    guard let original = loadCGImage(url: url) else {
        return OCRResult(path: path, text: "", rotation: 0, confidence: 0, engine: "vision", lines: [], score: 0, margin: 0)
    }
    let portrait = original.height > original.width
    // Portrait scans of landscape handwriting must become landscape (90 or 270).
    // Upright check on already-rotated copies: only 0 vs 180 (or 90 vs 270 if still portrait).
    let order: [Int]
    if uprightCheck {
        order = portrait ? [90, 270] : [0, 180]
    } else {
        order = portrait ? [90, 270] : [0, 180, 90, 270]
    }
    var scored: [(rot: Int, text: String, conf: Double, lines: [OCRLine], score: Double)] = []
    for rot in order {
        let img = preprocess(rotateImage(original, degrees: rot))
        let rec = recognize(cgImage: img)
        let s = score(text: rec.text, conf: rec.conf, lines: rec.lines, rot: rot, portrait: portrait)
        scored.append((rot, rec.text, rec.conf, rec.lines, s))
    }
    scored.sort { $0.score > $1.score }
    let b = scored[0]
    let second = scored.count > 1 ? scored[1].score : 0.0
    return OCRResult(
        path: path,
        text: b.text,
        rotation: b.rot,
        confidence: b.conf,
        engine: "vision",
        lines: b.lines,
        score: b.score,
        margin: b.score - second
    )
}

func emit(_ result: OCRResult) {
    let enc = JSONEncoder()
    enc.outputFormatting = [.withoutEscapingSlashes]
    do {
        let data = try enc.encode(result)
        if let str = String(data: data, encoding: .utf8) {
            print(str)
            fflush(stdout)
        }
    } catch {
        die("json encode failed: \(error)")
    }
}

// AppKit/Vision can require an NSApplication instance.
_ = NSApplication.shared

var args = Array(CommandLine.arguments.dropFirst())
var uprightCheck = false
var stdinPaths = false
var files: [String] = []
for a in args {
    if a == "--upright-check" {
        uprightCheck = true
    } else if a == "--stdin-paths" {
        stdinPaths = true
    } else if a.hasPrefix("-") {
        die("usage: ocr_vision [--upright-check] [--stdin-paths] <image> [image...]")
    } else {
        files.append(a)
    }
}
if !stdinPaths && files.isEmpty {
    fputs("usage: ocr_vision [--upright-check] [--stdin-paths] <image> [image...]\n", stderr)
    exit(2)
}

if stdinPaths {
    while let line = readLine() {
        let p = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if p.isEmpty { continue }
        emit(ocrPath(p, uprightCheck: uprightCheck))
    }
} else {
    for p in files {
        emit(ocrPath(p, uprightCheck: uprightCheck))
    }
}
