import XCTest

/// UI test that verifies the app background (#0A0A10) fills the safe area
/// regions — no pure black gap between the system status bar and the app's
/// content, and no pure black gap above the bottom home indicator.
///
/// Note: The device's physical bezel/rounded corners and the system status bar
/// text area are rendered by the system and may be black. This test checks
/// the zones the app controls: the transition between status bar and content,
/// and the home indicator region.
final class BackgroundColorUITests: XCTestCase {

    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launch()
    }

    func testBackgroundFillsFullScreen() throws {
        let starClHeader = app.staticTexts["Star"]
        XCTAssertTrue(starClHeader.waitForExistence(timeout: 5),
                      "App should display the StarCl header")

        // Use the app screenshot (excludes system status bar rendering).
        let screenshot = app.screenshot()
        guard let cgImage = screenshot.image.cgImage else {
            XCTFail("Could not get CGImage from screenshot")
            return
        }

        let width = cgImage.width
        let height = cgImage.height

        guard let dataProvider = cgImage.dataProvider,
              let data = dataProvider.data,
              let ptr = CFDataGetBytePtr(data) else {
            XCTFail("Could not access pixel data")
            return
        }

        let bytesPerPixel = cgImage.bitsPerPixel / 8
        let bytesPerRow = cgImage.bytesPerRow

        func rgb(x: Int, y: Int) -> (r: UInt8, g: UInt8, b: UInt8) {
            let offset = y * bytesPerRow + x * bytesPerPixel
            return (ptr[offset], ptr[offset + 1], ptr[offset + 2])
        }

        func isPureBlack(_ pixel: (r: UInt8, g: UInt8, b: UInt8)) -> Bool {
            pixel.r == 0 && pixel.g == 0 && pixel.b == 0
        }

        // Locate the StarCl header element's position on screen to find the
        // safe area boundary. The region between the header and the top of
        // the app's rendering area should be the app's background color.
        let headerFrame = starClHeader.frame
        // Convert header top to pixel coordinates (approximate scale).
        let scale = CGFloat(width) / UIScreen.main.bounds.width
        let headerTopPixel = Int(headerFrame.minY * scale)

        // Check the region 10-20px above the header (within app's rendered area).
        // This should be the app's background, not black.
        let checkStartY = max(0, headerTopPixel - 20)
        let checkEndY = max(0, headerTopPixel - 5)
        let sampleX = width / 4  // left quarter, away from any center UI

        var blackPixelsAboveHeader = 0
        var totalChecked = 0
        for y in checkStartY..<checkEndY {
            let pixel = rgb(x: sampleX, y: y)
            if isPureBlack(pixel) {
                blackPixelsAboveHeader += 1
            }
            totalChecked += 1
        }

        // Allow some tolerance (anti-aliasing), but most pixels should not be black.
        if totalChecked > 0 {
            let blackRatio = Double(blackPixelsAboveHeader) / Double(totalChecked)
            XCTAssertLessThan(blackRatio, 0.5,
                "Region above header (y=\(checkStartY)-\(checkEndY)) is mostly black " +
                "(\(blackPixelsAboveHeader)/\(totalChecked)) — " +
                "app background should extend into top safe area")
        }

        // Check bottom region: 50-100px from the bottom of the app's rendered area.
        // This is the home indicator safe area zone.
        let bottomCheckStart = height - 100
        let bottomCheckEnd = height - 50
        var blackPixelsAtBottom = 0
        var bottomChecked = 0

        for x in [width / 4, width / 2, 3 * width / 4] {
            for y in bottomCheckStart..<bottomCheckEnd {
                let pixel = rgb(x: x, y: y)
                if isPureBlack(pixel) {
                    blackPixelsAtBottom += 1
                }
                bottomChecked += 1
            }
        }

        if bottomChecked > 0 {
            let blackRatio = Double(blackPixelsAtBottom) / Double(bottomChecked)
            XCTAssertLessThan(blackRatio, 0.1,
                "Bottom safe area region (y=\(bottomCheckStart)-\(bottomCheckEnd)) is mostly black " +
                "(\(blackPixelsAtBottom)/\(bottomChecked)) — " +
                "app background should extend into bottom safe area")
        }
    }
}
