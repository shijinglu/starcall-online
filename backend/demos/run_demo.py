#!/usr/bin/env python3
"""General WebSocket demo runner.

Loads a conversation script from a JSON file and runs it via the WebSocket
harness (TTS -> binary audio frames -> backend -> collect responses).

Usage:
  python run_demo.py case_1
  python run_demo.py scripts/case_3.json
  python run_demo.py /path/to/custom_script.json
  python run_demo.py --list
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo_harness import SCRIPTS_DIR, load_script, run_demo


def list_scripts():
    print("Available scripts:")
    for p in sorted(SCRIPTS_DIR.glob("*.json")):
        script = load_script(p)
        print(f"  {p.stem:20s}  {script['name']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="General WebSocket demo runner. Loads conversation from a script file.",
        epilog="Examples:\n"
               "  python run_demo.py case_1\n"
               "  python run_demo.py scripts/case_3.json\n"
               "  python run_demo.py --list\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "script", nargs="?",
        help="Script name (e.g. 'case_2'), filename, or path to JSON file",
    )
    parser.add_argument("--list", action="store_true", help="List available scripts")
    parser.add_argument("--final-wait", type=int, help="Override final wait seconds")
    args = parser.parse_args()

    if args.list:
        list_scripts()
        sys.exit(0)

    if not args.script:
        parser.error("script is required (use --list to see available scripts)")

    script_data = load_script(args.script)
    conversation = [t["text"] for t in script_data["conversation"]]
    delays = [t["wait"] for t in script_data["conversation"]]
    final_wait = args.final_wait if args.final_wait is not None else script_data["final_wait"]

    asyncio.run(run_demo(
        case_name=script_data["name"],
        conversation=conversation,
        delays=delays,
        final_wait=final_wait,
    ))
