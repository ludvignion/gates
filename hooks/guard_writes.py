#!/usr/bin/env python3
"""PreToolUse hook: two write rules.

1. Closed tickets are immutable. A write to a ticket file whose frontmatter `status:` is one of
   `schemas.CLOSED_STATUSES` (done, superseded) is refused, whether or not a ticket is active.
   New work is a new ticket with `depends_on:`; a human reopens by changing the status by hand.
2. Scope. If `kanban/.active` names a ticket with a `writes:` list, refuse any write outside it.

Reads the tool call from stdin (JSON). Exit 0 = allow. Exit 2 = block (stderr goes to the
agent). No active ticket, or a ticket without `writes:` → rule 2 is off. Rule 1 is always on.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import _fm  # noqa: E402
import schemas  # noqa: E402

ALWAYS_ALLOWED = ("kanban/", "traces/", "docs/")


def closed_ticket(cwd: Path, target: Path) -> tuple[str, str] | None:
    """(id, status) if `target` is an existing ticket file in a closed status, else None."""
    try:
        rel = target.resolve().relative_to(cwd)
    except ValueError:
        return None
    if rel.parts[:1] != ("kanban",) or rel.suffix != ".md" or rel.name.endswith(".plan.md"):
        return None
    if not target.is_file():
        return None
    fm, _ = _fm.read(target)
    if "id" in fm and fm.get("status") in schemas.CLOSED_STATUSES:
        return fm["id"], fm["status"]
    return None


def main() -> int:
    try:
        call = json.load(sys.stdin)
    except Exception:
        return 0
    cwd = Path(call.get("cwd") or ".").resolve()
    target = (call.get("tool_input") or {}).get("file_path")
    if not target:
        return 0
    closed = closed_ticket(cwd, Path(target))
    if closed:
        tid, status = closed
        sys.stderr.write(
            f"BLOCKED: ticket {tid} is {status}; closed tickets are immutable. "
            f"Put new work in a new ticket with depends_on: [{tid}]. "
            "A human reopens by changing status: by hand.\n"
        )
        return 2
    active = cwd / "kanban" / ".active"
    if not active.exists():
        return 0
    ticket_id = active.read_text().strip()
    matches = list((cwd / "kanban").rglob(f"{ticket_id}.*.md"))
    if not matches:
        return 0
    fm, _ = _fm.read(matches[0])
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
