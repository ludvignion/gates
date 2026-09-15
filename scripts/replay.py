#!/usr/bin/env python3
"""Re-run the grill on golden briefs headlessly and diff escalations against the stored trace.

Usage: make replay   (needs `claude` on PATH; each brief costs one grill run)
Golden set: traces/golden/<n>/{brief.md, grill.jsonl}. Copy a brief + its trace there after a
ticket closes with low rework.

Each run goes through vendor.run, so the JSON envelope's cost and token counts are printed with
the escalation diff: a skill edit that moves context cost is visible in the same line as the one
that moves behaviour.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import vendor  # noqa: E402


CMD = 'claude -p --output-format json'


def cost_line(call: "vendor.Call") -> str:
    """`<in>K in / <out>K out · $<cost> · <n>s`, with `-` for whatever the envelope did not carry."""
    tokens, cost = call.tokens or {}, call.cost_usd
    read = (tokens.get("cache_creation_input_tokens") or 0) + (tokens.get("cache_read_input_tokens") or 0) + (tokens.get("input_tokens") or 0)
    out = tokens.get("output_tokens") or 0
    money = f"${cost:.2f}" if cost is not None else "$-"
    return f"{read // 1000}K in / {out // 1000}K out · {money} · {call.wall:.0f}s"


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
        call = vendor.run(CMD, root, stdin_text=prompt)
        report = vendor.report(call.stdout) or {}
        new = escalations(report.get("result") or "")
        print(f"{d.name}: escalations {len(old)} → {len(new)} | +{sorted(new - old)} -{sorted(old - new)} | {cost_line(call)}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
