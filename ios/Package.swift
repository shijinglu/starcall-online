// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "StarCall",
    platforms: [
        .iOS(.v17),
        .macOS(.v14)
    ],
    products: [
        .library(
            name: "StarCallLib",
            targets: ["StarCallLib"]
        )
    ],
    targets: [
        // Library target: all source code except the @main entry point.
        .target(
            name: "StarCallLib",
            dependencies: [],
            path: "StarCall",
            exclude: ["App"],
            linkerSettings: [
                .linkedFramework("AVFoundation")
            ]
        ),
        // Executable target: the SwiftUI @main entry point.
        .executableTarget(
            name: "StarCall",
            dependencies: ["StarCallLib"],
            path: "StarCall/App",
            linkerSettings: [
                .linkedFramework("AVFoundation")
            ]
        ),
        // Test target: tests against the library.
        .testTarget(
            name: "StarCallTests",
            dependencies: ["StarCallLib"],
            path: "StarCallTests"
        )
    ]
)
