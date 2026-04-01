---
name: dev-mobile-mcp-setup
description: Check and fix prerequisites for mobile-mcp to work with a physical iOS device (go-ios tunnel, iproxy port forwarding, WDA, screenshots)
user_invocable: true
---

# Mobile-MCP Physical Device Setup

This skill verifies that mobile-mcp can communicate with a connected physical iOS device, and fixes any broken prerequisites.

## Step 1: Quick check — can mobile-mcp already see the device?

Call `mcp__mobile-mcp__mobile_list_available_devices`. If a **physical iOS device** appears in the list, report success and stop — everything is working.

If no physical device is found, proceed to diagnose and fix each component below.

## Step 2: Diagnose all prerequisites

Check each component and build a status table. Run these checks in parallel:

### 2a. go-ios tunnel
```bash
pgrep -f "ios tunnel"
```
- If a PID is returned: **Running**
- If no PID: **Not running** — needs to be started

### 2b. Port forwarding (8100)
```bash
pgrep -f "iproxy.*8100"
```
- If a PID is returned: **Running**
- If no PID: **Not running** — needs to be started

### 2c. WDA on device
```bash
curl -s --max-time 3 http://localhost:8100/status
```
- If returns JSON with `sessionId`: **Running**
- If connection refused or timeout: **Not running** — needs to be started

### 2d. mobile-mcp screenshots
Only check this after 2a-2c are all green. Call `mcp__mobile-mcp__mobile_take_screenshot` with the device identifier. If it succeeds, mark **Working**.

## Step 3: Print status table

Display results in this format:

```
┌────────────────────────┬──────────────────────────────────────────┐
│       Component        │                  Status                  │
├────────────────────────┼──────────────────────────────────────────┤
│ go-ios tunnel          │ <status>                                 │
│ Port forwarding (8100) │ <status>                                 │
│ WDA on device          │ <status>                                 │
│ mobile-mcp screenshots │ <status>                                 │
│ VoiceAgent app         │ <status>                                 │
└────────────────────────┴──────────────────────────────────────────┘
```

## Step 4: Fix broken components (in order)

Fix components top-to-bottom — later components depend on earlier ones.

### Fix: go-ios tunnel

The tunnel must run with sudo. Ask the user to run it themselves:

> Please run this in a separate terminal (it must stay running):
> ```
> sudo ios tunnel start
> ```

After the user confirms it's running, re-check with `pgrep -f "ios tunnel"`.

### Fix: Port forwarding (8100)

Start iproxy to forward port 8100 from localhost to the device's WDA port:

```bash
# Get the device UDID
UDID=$(ios list 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['deviceList'][0])")

# Start iproxy in background (maps localhost:8100 -> device:8100)
nohup iproxy 8100 8100 --udid "$UDID" > /tmp/iproxy.log 2>&1 &
```

Verify it started: `pgrep -f "iproxy.*8100"`

### Fix: WDA on device

WDA (WebDriverAgent) needs to be built and launched on the physical device. The WDA repo is at `local/WDA`.

Use XcodeBuildMCP to build and run WDA on the device. The scheme is `WebDriverAgentRunner` and the project is at `local/WDA/WebDriverAgent.xcodeproj`.

If XcodeBuildMCP device tools are not available, tell the user to build and run WDA manually:

> Open `local/WDA/WebDriverAgent.xcodeproj` in Xcode, select the `WebDriverAgentRunner` scheme, choose your physical device as the target, and hit **Test** (Cmd+U).

After WDA is running, verify:
```bash
curl -s --max-time 3 http://localhost:8100/status
```

### Fix: VoiceAgent app

Once all infrastructure is green, launch VoiceAgent on the device using mobile-mcp:

```
mcp__mobile-mcp__mobile_launch_app(device: "<device_id>", app_id: "<bundle_id>")
```

Get the bundle ID from XcodeBuildMCP or the project's Info.plist.

## Step 5: Final verification

After all fixes, run the full check sequence again (Step 1) and print the updated status table. Confirm that mobile-mcp can take a screenshot of the device.
