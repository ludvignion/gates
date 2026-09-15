"""One-off migration: every file ticket under kanban/tickets/ becomes a GitHub Issue on
`kanban/.issues`' repo (plan 4 AC-14 / this ticket AC-2). `init_project.py --issues` runs this
after the marker and labels exist; nothing here calls the marker or the labels itself.

A dependency migrates before its dependants (`depends_on` cited by ids still local to this
batch), so the issue it becomes already has a number when a later issue's `depends_on: [#<n>]`
is rendered. Every Log entry becomes one issue comment, in order, before the ticket's final
status label is set — `done` and `superseded` close the issue only once its history is written.
`## Slices` lines in every plan are rewritten to `#<number>` and the original ticket files are
removed from disk; `init_project.py` stages the deletions and the plan edits into the one commit
plan 4 AC-14 asks for.
"""
import re
from pathlib import Path

import _fm
import schemas
import tickets

_TITLE_RE = re.compile(r"^#\s+\S+\s+(?P<title>.+)$", re.MULTILINE)
_LOG_ENTRY_RE = re.compile(r"^(?P<when>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — (?P<head>.*)$", re.DOTALL)


def _title(body: str, fallback: str) -> str:
    m = _TITLE_RE.search(body)
    return m.group("title").strip() if m else fallback


def _order(rows: dict[str, list[str]]) -> list[str]:
    """Ticket ids topologically sorted, a dependency before its dependants. Every status is
    included (unlike `kanban_ops.plan_order`, which drops `done`/`superseded`): a migration
    creates an issue for every file ticket, closed ones too."""
    order: list[str] = []
    pending = dict(rows)
    while pending:
        ready = [tid for tid, deps in pending.items() if all(d in order or d not in pending for d in deps)]
        if not ready:
            raise ValueError(f"depends_on cycle among {', '.join(sorted(pending))}")
        for tid in sorted(ready, key=lambda t: [int(x) for x in t.split(".")]):
            order.append(tid)
            pending.pop(tid)
    return order


def _issue_body(parent: str, depends_on: list[str], writes: list[str], body: str) -> str:
    """The same body shape a grill-created issue has (frontmatter `parent`/`depends_on`/`writes`
    plus Outcome, Acceptance criteria, Out of scope) with the file ticket's `## Findings
    (append-only)` kept verbatim when it holds anything — nothing else drops a finding on the
    floor just because it predates the marker."""
    sect = dict(schemas.sections(body))
    findings = next((t for title, t in sect.items() if title.lower().startswith("findings")), "").strip()
    blocks = [
        f"---\nparent: {parent}\ndepends_on: [{', '.join(depends_on)}]\nwrites: [{', '.join(writes)}]\n---\n",
        f"## Outcome\n{schemas.section(body, 'Outcome').strip()}\n",
        f"## Acceptance criteria\n{schemas.section(body, 'Acceptance criteria').strip()}\n",
        f"## Out of scope\n{schemas.section(body, 'Out of scope').strip()}\n",
    ]
    if findings:
        blocks.append(f"## Findings (append-only)\n{findings}\n")
    return "\n".join(blocks)


def _split_entry(entry: "schemas.LogEntry") -> tuple[str, str | None, str, tuple[str, ...]]:
    """role, timestamp, head text and extra lines out of a parsed `LogEntry`, the pieces
    `tickets.log` takes — round-tripping through it reproduces the same comment text."""
    lines = entry.text.splitlines()[1:]  # drop the "### [role] ..." heading line
    m = _LOG_ENTRY_RE.match(entry.head)
    when, head = (m.group("when"), m.group("head")) if m else (None, entry.head)
    return entry.role, when, head, tuple(lines)


def _rewrite_plans(root: Path, mapping: dict[str, int]) -> list[Path]:
    """Every plan's `## Slices` line for a migrated id now cites `#<number>` (plan 4 AC-14)."""
    changed = []
    for plan_path in sorted((root / "kanban").rglob("*.plan.md")):
        text = plan_path.read_text(encoding="utf-8")
        slices = schemas.section(text, "Slices")
        if not slices:
            continue
        new_slices = slices
        for old, new in mapping.items():
            new_slices = re.sub(rf"`{re.escape(old)}`", f"`#{new}`", new_slices)
        if new_slices != slices:
            start = text.index(slices)
            plan_path.write_text(text[:start] + new_slices + text[start + len(slices):], encoding="utf-8")
            changed.append(plan_path)
    return changed


def migrate(root: Path, repo: str) -> tuple[list[tuple[str, int]], list[Path]]:
    """Every file ticket under `kanban/` becomes one issue on `repo`; returns
    `([(old_id, new_number), ...], changed_plan_paths)` in migration order. Raises ValueError on
    a `depends_on` cycle before any `gh` call, and whatever `tickets.py` raises on a `gh` failure
    partway (leaving that issue's own history incomplete, as any other `gh` failure does)."""
    rows = {fm["id"]: (path, fm, body) for path, fm, body in _fm.tickets(root / "kanban")}
    if not rows:
        return [], []
    order = _order({tid: [str(d) for d in (fm.get("depends_on") or [])] for tid, (_, fm, _) in rows.items()})
    new_number_of: dict[str, int] = {}
    mapping: list[tuple[str, int]] = []
    for tid in order:
        path, fm, body = rows[tid]
        depends_new = [f"#{new_number_of[d]}" if d in new_number_of else str(d) for d in (fm.get("depends_on") or [])]
        issue_body = _issue_body(str(fm.get("parent", "")), depends_new, list(fm.get("writes") or []), body)
        number = tickets.create_issue(repo, _title(body, tid), issue_body)
        for entry in schemas.Log.parse(body).entries:
            role, when, head, lines = _split_entry(entry)
            tickets.log(repo, number, role, head, lines, when=when)
        status = fm.get("status", "ready")
        if status != "ready":
            tickets.set_status(repo, number, status)
        new_number_of[tid] = number
        mapping.append((tid, number))
    changed_plans = _rewrite_plans(root, dict(mapping))
    for tid in order:
        rows[tid][0].unlink()
    return mapping, changed_plans
