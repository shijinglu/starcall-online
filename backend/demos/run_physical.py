#!/usr/bin/env python3
"""General physical device demo runner.

Loads a conversation script from a JSON file and runs it against a real iPhone.
Mac speaks utterances through speakers -> iPhone mic picks up -> backend processes.

Usage:
  python run_physical.py case_2
  python run_physical.py scripts/case_3.json --no-prompt --volume 70
  python run_physical.py /path/to/custom_script.json --voice Daniel
  python run_physical.py --list
"""

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo_harness import SCRIPTS_DIR, load_script

# ---------- Configuration ----------
BACKEND_LOG = Path(__file__).resolve().parent.parent / "logs" / "app.log"
DEFAULT_VOICE = "Samantha"
DEFAULT_VOLUME = 30

DEVICE_ECID = "07A23C42-5796-5D3A-BC9A-CC2288AC325A"
BUNDLE_ID = "com.shijinglu.VoiceAgent"


# ---------- Timeline ----------
class Timeline:
    """Collects timestamped events from both local actions and backend logs."""

    def __init__(self):
        self.t0_mono: float = 0.0
        self.t0_wall: datetime | None = None
        self.events: list[tuple[float, str]] = []

    def start(self):
        self.t0_mono = time.monotonic()
        self.t0_wall = datetime.now()

    def elapsed(self) -> float:
        return time.monotonic() - self.t0_mono

    def add(self, description: str, at: float | None = None):
        t = (at or time.monotonic()) - self.t0_mono
        self.events.append((t, description))
        print(f"  [{t:6.1f}s] {description}")

    def add_from_log_ts(self, ts_str: str, description: str):
        try:
            log_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")
            delta = (log_dt - self.t0_wall).total_seconds()
            if delta < -1:
                return
            self.events.append((delta, description))
            print(f"  [{delta:6.1f}s] {description}")
        except (ValueError, TypeError):
            pass

    def print_report(self):
        self.events.sort(key=lambda e: e[0])
        print("\n")
        print("=" * 90)
        print("EVENT TIMELINE")
        print("=" * 90)
        for elapsed, desc in self.events:
            print(f"  {elapsed:7.1f}s  {desc}")
        if self.events:
            print(f"\n  Total duration: {self.events[-1][0]:.1f}s")
        print("=" * 90)


# ---------- Log parsing ----------
LOG_PATTERNS = [
    (
        re.compile(r"^(\S+ \S+).*Created session (s-\S+)"),
        lambda m: f"SESSION created: {m.group(2)}",
    ),
    (
        re.compile(r"^(\S+ \S+).*WS connected for session"),
        lambda m: "WEBSOCKET connected",
    ),
    (
        re.compile(r"^(\S+ \S+).*Gemini Live session started"),
        lambda m: "GEMINI session started",
    ),
    (
        re.compile(r"^(\S+ \S+).*DIAG-ECHO:.*Gemini heard user speech: '(.+)'"),
        lambda m: f"GEMINI heard: \"{m.group(2).strip()}\"",
    ),
    (
        re.compile(r"^(\S+ \S+).*enqueue MODERATOR pcm=(\d+) bytes \(([^)]+)\).*mod_q=0, agent_q=0"),
        lambda m: f"MODERATOR audio enqueued: {m.group(2)} bytes ({m.group(3)})"
        if int(m.group(2)) > 5000 else None,
    ),
    (
        re.compile(r"^(\S+ \S+).*Dispatching agent (\w+) with task: (.+)"),
        lambda m: f"AGENT dispatched: {m.group(2)} — \"{m.group(3)[:60]}\"",
    ),
    (
        re.compile(r"^(\S+ \S+).*_run_agent \[(\w+)\]: TTS took ([\d.]+)s.*expected_duration=([\d.]+)s"),
        lambda m: f"AGENT done: {m.group(2)} (TTS {m.group(3)}s, audio {m.group(4)}s)",
    ),
    (
        re.compile(r"^(\S+ \S+).*enqueue AGENT=(\w+) pcm=(\d+) bytes \(([^)]+)\)"),
        lambda m: f"AGENT audio enqueued: {m.group(2)} {m.group(3)} bytes ({m.group(4)})",
    ),
    (
        re.compile(r"^(\S+ \S+).*INTERRUPT: barge-in detected, prev_state=(\w+).*items_flushed=(\d+)"),
        lambda m: f"INTERRUPT: barge-in (was {m.group(2)}, flushed {m.group(3)} items)",
    ),
    (
        re.compile(r"^(\S+ \S+).*INTERRUPT: playback aborted for speaker=(\w+), played=([\d.]+)s of ([\d.]+)s"),
        lambda m: f"INTERRUPT: {m.group(2)} playback cut at {m.group(3)}s/{m.group(4)}s",
    ),
    (
        re.compile(r"^(\S+ \S+).*Turn complete, continuing to listen"),
        lambda m: "GEMINI turn complete (listening)",
    ),
    (
        re.compile(r"^(\S+ \S+).*Control action=stop"),
        lambda m: "SESSION stopped",
    ),
    (
        re.compile(r"^(\S+ \S+).*Terminated session"),
        lambda m: "SESSION terminated",
    ),
]


def scan_log_lines(text: str, timeline: Timeline):
    for line in text.splitlines():
        for pattern, formatter in LOG_PATTERNS:
            m = pattern.match(line)
            if m:
                desc = formatter(m)
                if desc:
                    timeline.add_from_log_ts(m.group(1), desc)
                break


# ---------- Log file helpers ----------
def get_log_size() -> int:
    try:
        return BACKEND_LOG.stat().st_size
    except FileNotFoundError:
        return 0


def read_new_logs(since_offset: int) -> tuple[str, int]:
    try:
        size = BACKEND_LOG.stat().st_size
        if size <= since_offset:
            return "", since_offset
        with open(BACKEND_LOG, "r") as f:
            f.seek(since_offset)
            text = f.read()
        return text, since_offset + len(text.encode())
    except FileNotFoundError:
        return "", since_offset


# ---------- Helpers ----------
def set_mac_volume(volume: int):
    subprocess.run(
        ["osascript", "-e", f"set volume output volume {volume}"],
        check=True, capture_output=True,
    )


def say(text: str, voice: str = DEFAULT_VOICE):
    subprocess.run(["say", "-v", voice, text], check=True)


def poll_logs(log_offset: int, duration: float, timeline: Timeline) -> int:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        new_text, log_offset = read_new_logs(log_offset)
        if new_text:
            scan_log_lines(new_text, timeline)
        time.sleep(0.3)
    return log_offset


# ---------- Main ----------
def run_demo(
    script: dict,
    voice: str = DEFAULT_VOICE,
    volume: int = DEFAULT_VOLUME,
    skip_device_setup: bool = False,
    dry_run: bool = False,
    no_prompt: bool = False,
):
    conversation = script["conversation"]
    case_name = script["name"]
    final_wait = script.get("final_wait", 15)

    tl = Timeline()
    tl.start()

    print("=" * 60)
    print(f"Physical Device Demo: {case_name}")
    print("=" * 60)
    if script.get("description"):
        print(f"  {script['description']}")
    tl.add("Demo started")

    # Phase 1: Prepare
    set_mac_volume(volume)
    tl.add(f"Mac volume set to {volume}%")

    if not BACKEND_LOG.exists():
        print(f"  WARNING: Backend log not found at {BACKEND_LOG}")

    log_offset = get_log_size()

    if not skip_device_setup:
        result = subprocess.run(
            ["xcrun", "devicectl", "list", "devices"],
            capture_output=True, text=True,
        )
        if DEVICE_ECID in result.stdout or "iPhone" in result.stdout:
            tl.add("iPhone detected via USB")
        else:
            tl.add("WARNING: iPhone not detected")

    # Phase 2: Launch & start
    if skip_device_setup:
        tl.add("Skipping device setup (--skip-device-setup)")
        if no_prompt:
            time.sleep(5)
        else:
            input("  Press Enter when app is running and tapped START...")
    else:
        tl.add("Launching VoiceAgent app on iPhone...")
        launch_result = subprocess.run(
            ["xcrun", "devicectl", "device", "process", "launch",
             "--device", DEVICE_ECID, BUNDLE_ID],
            capture_output=True, text=True,
        )
        if launch_result.returncode == 0:
            tl.add("App launched on iPhone")
        else:
            tl.add(f"App launch failed: {launch_result.stderr.strip()[:80]}")

        print("\n  >>> Tap 'TAP TO START' on the iPhone screen now <<<")
        if no_prompt:
            tl.add("Waiting 10s for START tap...")
            time.sleep(10)
        else:
            input("  Press Enter after tapping START on iPhone...")
        tl.add("START tap window elapsed")

    # Wait for session init
    tl.add("Waiting for session init...")
    log_offset = poll_logs(log_offset, 3, tl)

    if dry_run:
        print("\n[DRY RUN] Utterances:")
        for i, turn in enumerate(conversation):
            print(f"  {i+1}. \"{turn['text']}\" (wait {turn['wait']}s)")
        return

    # Phase 3: Run conversation
    tl.add(f"Starting conversation ({len(conversation)} turns)")

    for i, turn in enumerate(conversation):
        utterance = turn["text"]
        wait_time = turn["wait"]
        turn_label = f"Turn {i+1}/{len(conversation)}"
        print(f"\n{'─' * 60}")
        print(f"  [{turn_label}] \"{utterance}\"")
        print(f"{'─' * 60}")

        say_start = time.monotonic()
        tl.add(f"SAY [{turn_label}]: \"{utterance}\"", at=say_start)
        say(utterance, voice=voice)
        say_end = time.monotonic()
        tl.add(f"SAY done ({(say_end - say_start)*1000:.0f}ms)", at=say_end)

        log_offset = poll_logs(log_offset, wait_time, tl)

    # Phase 4: Drain remaining events
    tl.add(f"Conversation done, waiting {final_wait}s for trailing responses...")
    log_offset = poll_logs(log_offset, final_wait, tl)

    final_text, log_offset = read_new_logs(log_offset)
    if final_text:
        scan_log_lines(final_text, tl)

    tl.add("Demo complete")

    # Save log snapshot
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = script["name"].replace(" ", "_").replace(":", "")[:40]
    log_snapshot = output_dir / f"physical_{safe_name}_{timestamp}.log"
    all_logs, _ = read_new_logs(max(0, get_log_size() - 200_000))
    if all_logs:
        with open(log_snapshot, "w") as f:
            f.write(all_logs)
        print(f"\n  Log snapshot: {log_snapshot}")

    # Save timeline
    timeline_path = output_dir / f"physical_{safe_name}_{timestamp}_timeline.txt"
    tl.events.sort(key=lambda e: e[0])
    with open(timeline_path, "w") as f:
        for elapsed, desc in tl.events:
            f.write(f"{elapsed:7.1f}s  {desc}\n")
    print(f"  Timeline:     {timeline_path}")

    tl.print_report()


def list_scripts():
    print("Available scripts:")
    for p in sorted(SCRIPTS_DIR.glob("*.json")):
        script = load_script(p)
        print(f"  {p.stem:20s}  {script['name']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="General physical device demo runner. Loads conversation from a script file.",
        epilog="Examples:\n"
               "  python run_physical.py case_2\n"
               "  python run_physical.py scripts/case_3.json --no-prompt\n"
               "  python run_physical.py --list\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "script", nargs="?",
        help="Script name (e.g. 'case_2'), filename, or path to JSON file",
    )
    parser.add_argument("--list", action="store_true", help="List available scripts")
    parser.add_argument("--skip-device-setup", action="store_true", help="Skip device detection and app launch")
    parser.add_argument("--dry-run", action="store_true", help="Print script without speaking")
    parser.add_argument("--volume", type=int, default=DEFAULT_VOLUME, help=f"Mac output volume 0-100 (default: {DEFAULT_VOLUME})")
    parser.add_argument("--voice", type=str, default=DEFAULT_VOICE, help=f"macOS TTS voice (default: {DEFAULT_VOICE})")
    parser.add_argument("--no-prompt", action="store_true", help="Don't wait for interactive input")
    args = parser.parse_args()

    if args.list:
        list_scripts()
        sys.exit(0)

    if not args.script:
        parser.error("script is required (use --list to see available scripts)")

    script_data = load_script(args.script)
    run_demo(
        script=script_data,
        voice=args.voice,
        volume=args.volume,
        skip_device_setup=args.skip_device_setup,
        dry_run=args.dry_run,
        no_prompt=args.no_prompt,
    )
