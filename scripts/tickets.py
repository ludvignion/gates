#!/usr/bin/env python3
"""The one `gh` seam (brief 4, decision 7). `kanban/.issues` names a repo (`owner/repo`);
`make sync` (`tickets.py sync`) mirrors its `ticket`-labelled Issues into
`kanban/tickets/<number>.<slug>.md`, a read-only, derived, never-committed cache — the same shape
`scripts/schemas.py` and `scripts/_fm.py` already parse. A project without the marker never calls
`gh`: every existing reader stays on file tickets, unchanged (plan 4 AC-0 / this ticket AC-1).

Status lives in the issue's label, never in its body (plan 4 AC-2): the sync writes `status:`
into the mirror from the label and reports a body that carries its own `status:` line instead of
trusting it.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

MARKER = "kanban/.issues"
TICKET_LABEL = "ticket"
STATUS_LABELS = ("ready", "in_progress", "in_review", "done", "superseded")
_STATUS_LINE_RE = re.compile(r"^status:\s*\S", re.MULTILINE)


def marker(root: Path) -> str | None:
    """`kanban/.issues`'s content (`owner/repo`), or None when the project has not opted in."""
    p = root / MARKER
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def _gh(args: list[str]) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def ensure_labels(repo: str) -> None:
    """The `ticket` marker label and the five status labels, created idempotently."""
    for name in (TICKET_LABEL, *STATUS_LABELS):
        _gh(["label", "create", name, "-R", repo, "--force"])


def _label_names(labels: list) -> list[str]:
    return [l if isinstance(l, str) else l.get("name") for l in labels]


def _status_label(labels: list) -> str | None:
    names = _label_names(labels)
    return next((s for s in STATUS_LABELS if s in names), None)


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].rstrip("-") or "ticket"


def body_status_problem(body: str) -> bool:
    """True when the issue body's own frontmatter carries a `status:` line (plan 4 AC-2 / this
    ticket AC-3): status is the label, never body text a writer or a human can edit."""
    head = body.split("---", 2)[1] if body.startswith("---") else body
    return bool(_STATUS_LINE_RE.search(head))


def render_mirror(issue: dict) -> str:
    """The mirror text for one issue: frontmatter with `status:` from the label, the body
    sections verbatim, and every comment concatenated under `## Log` in creation order."""
    import _fm  # local: keeps this importable from a bare python3, as Packet.parse does

    number = issue["number"]
    status = _status_label(issue.get("labels") or []) or "ready"
    fm, rest = _fm.parse(issue.get("body") or "")
    depends = ", ".join(str(d) for d in fm.get("depends_on") or [])
    writes = ", ".join(str(w) for w in fm.get("writes") or [])
    head = (
        f"---\nid: {number}\nparent: {fm.get('parent', '')}\nstatus: {status}\n"
        f"depends_on: [{depends}]\nwrites: [{writes}]\n---\n"
    )
    title = f"# {number} {issue.get('title', '')}\n"
    log_entries = "".join(f"{(c.get('body') or '').rstrip()}\n\n" for c in issue.get("comments") or [])
    return f"{head}\n{title}\n{rest.strip(chr(10))}\n\n## Log (append-only)\n{log_entries}"


def sync(root: Path) -> list[Path]:
    """Mirror every `ticket`-labelled issue of `kanban/.issues`'s repo into
    `kanban/tickets/<number>.<slug>.md`. No-op, no `gh` call, when the marker is absent."""
    repo = marker(root)
    if not repo:
        return []
    ensure_labels(repo)
    numbers = [
        i["number"]
        for i in json.loads(_gh(["issue", "list", "-R", repo, "--label", TICKET_LABEL, "--state", "all", "--json", "number"]))
    ]
    tickets_dir = root / "kanban" / "tickets"
    tickets_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for n in numbers:
        issue = json.loads(_gh(["issue", "view", str(n), "-R", repo, "--json", "number,title,body,labels,comments"]))
        if body_status_problem(issue.get("body") or ""):
            print(f"[sync] #{n}: body carries a status: line; status is the label, ignoring it", file=sys.stderr)
        path = tickets_dir / f"{n}.{slug(issue.get('title', ''))}.md"
        path.write_text(render_mirror(issue), encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "sync":
        print("usage: tickets.py sync [--cwd .]", file=sys.stderr)
        return 2
    cwd = argv[argv.index("--cwd") + 1] if "--cwd" in argv else "."
    for p in sync(Path(cwd).resolve()):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
