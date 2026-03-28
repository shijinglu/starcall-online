// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "VoiceAgent",
    platforms: [
        .iOS(.v17),
        .macOS(.v14)
    ],
    products: [
        .library(
            name: "VoiceAgentLib",
            targets: ["VoiceAgentLib"]
        )
    ],
    targets: [
        // Library target: all source code except the @main entry point.
        .target(
            name: "VoiceAgentLib",
            dependencies: [],
            path: "VoiceAgent",
            exclude: ["App"],
            linkerSettings: [
                .linkedFramework("AVFoundation")
            ]
        ),
        // Executable target: the SwiftUI @main entry point.
        .executableTarget(
            name: "VoiceAgent",
            dependencies: ["VoiceAgentLib"],
            path: "VoiceAgent/App",
            linkerSettings: [
                .linkedFramework("AVFoundation")
            ]
        ),
        // Test target: tests against the library.
        .testTarget(
            name: "VoiceAgentTests",
            dependencies: ["VoiceAgentLib"],
            path: "VoiceAgentTests"
        )
    ]
)
