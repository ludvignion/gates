#!/usr/bin/env python3
"""Headless state machine for one ticket: build → ci → verdict, retry capped, idempotent.

Usage: python loop.py <ticket id> [--max-retries 4] [--docker]
States: build → ci → verdict → (ship | reject→child | retry)
Each state is a `claude -p` call or a shell command; transitions only on objective signals
(exit codes, verdict.json decision). The machine, not the model, owns the loop.
Run in a container when unattended (see Dockerfile).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def claude(prompt: str, cwd: Path) -> int:
    return subprocess.run(["claude", "-p", prompt, "--permission-mode", "acceptEdits"], cwd=cwd).returncode


def ci(cwd: Path) -> bool:
    return subprocess.run(["make", "ci"], cwd=cwd).returncode == 0


def verdict(cwd: Path, tid: str) -> str:
    claude(f"/verdict {tid}", cwd)
    p = cwd / "traces" / "verdict" / f"{tid}.json"
    return json.loads(p.read_text())["decision"] if p.exists() else "reject"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticket")
    ap.add_argument("--max-retries", type=int, default=4)
    ap.add_argument("--cwd", default=".")
    a = ap.parse_args()
    cwd = Path(a.cwd).resolve()
    for attempt in range(1, a.max_retries + 1):
        print(f"[loop] {a.ticket} attempt {attempt}")
        claude(f"/build {a.ticket}", cwd)
        if not ci(cwd):
            print("[loop] ci red")
            continue
        d = verdict(cwd, a.ticket)
        print(f"[loop] verdict: {d}")
        if d == "ship":
            return 0
    print("[loop] retry cap reached — human needed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
