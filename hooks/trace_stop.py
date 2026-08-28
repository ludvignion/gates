#!/usr/bin/env python3
"""Stop hook: append one JSONL record per agent turn to traces/sessions.jsonl.

Cheap, local, always on. Opik export is a separate concern (see README).
"""
import json
import sys
import time
from pathlib import Path


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    cwd = Path(ev.get("cwd") or ".")
    traces = cwd / "traces"
    if not traces.exists():
        return 0
    active = cwd / "kanban" / ".active"
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": ev.get("session_id"),
        "ticket": active.read_text().strip() if active.exists() else None,
        "transcript_path": ev.get("transcript_path"),
    }
    with (traces / "sessions.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
