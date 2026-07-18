// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "WhisperedCaptureHelper",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "whispered-capture-helper", targets: ["WhisperedCaptureHelper"])],
    targets: [.executableTarget(name: "WhisperedCaptureHelper")]
)
