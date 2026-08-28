#!/usr/bin/env python3
"""Re-run the grill on golden briefs headlessly and diff escalations against the stored trace.

Usage: make replay   (needs `claude` on PATH; each brief costs one grill run)
Golden set: traces/golden/<n>/{brief.md, grill.jsonl}. Copy a brief + its trace there after a
ticket closes with low rework.
"""
import json
import subprocess
import sys
from pathlib import Path


def escalations(jsonl: str) -> set[str]:
    return {json.loads(l)["node"] for l in jsonl.splitlines() if l.strip() and json.loads(l).get("resolved_by") == "human"}


def main(root: Path) -> None:
    golden = root / "traces" / "golden"
    if not golden.exists():
        print("no golden set")
        return
    for d in sorted(golden.iterdir()):
        brief = (d / "brief.md").read_text()
        old = escalations((d / "grill.jsonl").read_text())
        prompt = f"/grill --replay\n\n{brief}\n\nOutput only the JSONL trace records, one per line."
        res = subprocess.run(["claude", "-p", prompt, "--output-format", "text"], capture_output=True, text=True, cwd=root)
        new = escalations(res.stdout)
        print(f"{d.name}: escalations {len(old)} → {len(new)} | +{sorted(new - old)} -{sorted(old - new)}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
