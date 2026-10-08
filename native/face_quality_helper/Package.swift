// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "WhisperedFaceHelper",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "whispered-face-helper", targets: ["WhisperedFaceHelper"])],
    targets: [.executableTarget(name: "WhisperedFaceHelper")]
)
