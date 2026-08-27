#!/usr/bin/env python3
"""PreToolUse hook: block file writes outside the active ticket's `writes:` paths.

Reads the tool call from stdin (JSON). Looks for `kanban/.active` in the project
(contents: a ticket id, e.g. `1.2`). If present, parses that ticket's frontmatter
`writes:` list and refuses any write whose path is not under one of them.

Exit 0 = allow. Exit 2 = block (message on stderr is shown to the agent).
No active ticket, or a ticket without `writes:` → allow. The gate is opt-in per ticket.
"""
import json
import sys
from pathlib import Path

ALWAYS_ALLOWED = ("kanban/", "traces/", "docs/")


def frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    body = text.split("---", 2)[1]
    out: dict = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            out[k.strip()] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            out[k.strip()] = v
    return out


def main() -> int:
    try:
        call = json.load(sys.stdin)
    except Exception:
        return 0
    cwd = Path(call.get("cwd") or ".").resolve()
    target = (call.get("tool_input") or {}).get("file_path")
    if not target:
        return 0
    active = cwd / "kanban" / ".active"
    if not active.exists():
        return 0
    ticket_id = active.read_text().strip()
    matches = list((cwd / "kanban").glob(f"{ticket_id}.*.md"))
    if not matches:
        return 0
    fm = frontmatter(matches[0].read_text())
    writes = fm.get("writes")
    if not writes:
        return 0
    rel = Path(target).resolve()
    try:
        rel_str = str(rel.relative_to(cwd))
    except ValueError:
        rel_str = str(rel)
    if rel_str.startswith(ALWAYS_ALLOWED):
        return 0
    if any(rel_str.startswith(w.rstrip("/")) for w in writes):
        return 0
    sys.stderr.write(
        f"BLOCKED: {rel_str} is outside ticket {ticket_id} writes: {writes}. "
        "Log a finding in the ticket instead of editing outside scope.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
