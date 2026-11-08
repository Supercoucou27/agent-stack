#!/usr/bin/env python3
"""One-shot cleanup for users whose saved profiles were bloated by
the pre-Section-6 round-trip.

Run this once to delete any saved profile whose ``raw_args`` field
contains more than 50 entries. The next time you launch llamaUI, a
fresh empty profile will be created on first save.

Usage:
    python3 qt_app/clean_user_profiles.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROFILES_PATH = Path.home() / ".local/share/llamaUI/profiles.json"


def main() -> int:
    if not PROFILES_PATH.exists():
        print(f"no profiles at {PROFILES_PATH}; nothing to clean up")
        return 0
    raw = PROFILES_PATH.read_text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"{PROFILES_PATH} is not valid JSON; leaving it alone")
        return 1
    profiles = data.get("data") or []
    if not isinstance(profiles, list):
        print(f"{PROFILES_PATH} has unexpected shape; leaving it alone")
        return 1

    bloated = [p for p in profiles if len(p.get("raw_args") or []) > 50]
    if not bloated:
        print(f"no bloated profiles (largest has "
              f"{max((len(p.get('raw_args') or []) for p in profiles), default=0)} raw_args)")
        return 0

    print(f"deleting {len(bloated)} bloated profile(s):")
    for p in bloated:
        print(f"  - {p.get('id')[:8]}.. model={p.get('model_id')!r} "
              f"raw_args={len(p.get('raw_args') or [])}")
    keep = [p for p in profiles if p not in bloated]
    data["data"] = keep
    PROFILES_PATH.write_text(json.dumps(data, indent=2))
    print(f"wrote {len(keep)} remaining profile(s) back to {PROFILES_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
