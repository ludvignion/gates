#!/usr/bin/env python3
"""traces/grill-misses.jsonl → traces/blind-spots.md (read by the grill skill). Run: make digest

Record shape: {"ticket": "1.2", "finding": "...", "category": "ordering", "should_grill_have_caught": true, "missed_question": "..."}
Override and grill-approval records ({"plan": ..., "source": "override"|"approval", ...}) share the file and carry no ticket.
"""
import json
from collections import Counter, defaultdict
from pathlib import Path


def main(root: Path) -> None:
    src = root / "traces" / "grill-misses.jsonl"
    if not src.exists():
        print("no misses yet")
        return
    recs = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    misses = [r for r in recs if r.get("should_grill_have_caught")]
    by_cat = Counter(r["category"] for r in misses)
    examples = defaultdict(list)
    for r in misses:
        if r.get("missed_question"):
            examples[r["category"]].append(r["missed_question"])
    lines = ["# Blind spots (generated — do not edit)", "",
             f"{len(misses)} misses over {len({r.get('ticket') for r in recs if r.get('ticket')})} tickets. Check each category against every brief.", ""]
    for cat, n in by_cat.most_common():
        lines.append(f"## {cat} ({n})")
        for q in examples[cat][:3]:
            lines.append(f"- {q}")
        lines.append("")
    (root / "traces" / "blind-spots.md").write_text("\n".join(lines))
    print("traces/blind-spots.md")


if __name__ == "__main__":
    main(Path("."))
