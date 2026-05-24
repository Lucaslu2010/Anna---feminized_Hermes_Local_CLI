// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SwiftAnna",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "AnnaSwift", targets: ["AnnaSwift"])
    ],
    targets: [
        .executableTarget(
            name: "AnnaSwift",
            path: "Sources/AnnaSwift",
            resources: [
                .copy("Resources/icon.icns")
            ]
        )
    ]
)
