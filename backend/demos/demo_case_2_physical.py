#!/usr/bin/env python3
"""Case 2 Physical Device Demo — backward-compatible wrapper.

Delegates to run_physical.py with the case_2 script.
For new usage, prefer: python run_physical.py case_2 [options]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo_harness import load_script
from run_physical import run_demo, DEFAULT_VOICE, DEFAULT_VOLUME

import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Case 2 Physical Device Demo (wrapper — prefer run_physical.py)"
    )
    parser.add_argument("--skip-device-setup", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--volume", type=int, default=DEFAULT_VOLUME)
    parser.add_argument("--voice", type=str, default=DEFAULT_VOICE)
    parser.add_argument("--no-prompt", action="store_true")
    args = parser.parse_args()

    run_demo(
        script=load_script("case_2"),
        voice=args.voice,
        volume=args.volume,
        skip_device_setup=args.skip_device_setup,
        dry_run=args.dry_run,
        no_prompt=args.no_prompt,
    )
