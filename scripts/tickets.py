#!/usr/bin/env python3
"""The one `gh` seam (brief 4, decision 7). `kanban/.issues` names a repo (`owner/repo`);
`make sync` (`tickets.py sync`) mirrors its `ticket`-labelled Issues into
`kanban/tickets/<number>.<slug>.md`, a read-only, derived, never-committed cache — the same shape
`scripts/schemas.py` and `scripts/_fm.py` already parse. A project without the marker never calls
`gh`: every existing reader stays on file tickets, unchanged (plan 4 AC-0 / this ticket AC-1).

Status lives in the issue's label, never in its body (plan 4 AC-2): the sync writes `status:`
into the mirror from the label and reports a body that carries its own `status:` line instead of
trusting it.

Every write — `log` (a comment), `set_status` (a label swap), `create_child` (a new issue) — goes
through here (plan 4 decision 7); `kanban_ops.py` and `board.py` call these instead of `gh`
directly. Each refuses on a closed issue before any `gh` call runs (plan 4 AC-6 / this ticket
AC-3): a closed issue is immutable, and a human reopens it from the Issues tab.

`list_ticket_numbers` and `closed_issue_history` are the reads `lint_kanban.py`'s GitHub-mode
rules use (plan 4 AC-12; ticket 4.4 AC-1/AC-2): GitHub keeps no closed-issue immutability of its
own, so the lint checks the edit history and reopen events instead of trusting the label.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

MARKER = "kanban/.issues"
TICKET_LABEL = "ticket"
STATUS_LABELS = ("ready", "in_progress", "in_review", "done", "superseded")
CLOSED_STATUSES = ("done", "superseded")


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")
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


def get_issue(repo: str, number: int) -> dict:
    return json.loads(_gh(["issue", "view", str(number), "-R", repo, "--json", "number,title,body,labels,comments,state"]))


def list_ticket_numbers(repo: str, state: str = "all") -> list[int]:
    """Every `ticket`-labelled issue's number, `state` as `gh issue list` takes it (`open`,
    `closed` or `all`). The one place `sync` and `lint_kanban`'s GitHub-mode rules list issues,
    so a `gh` failure looks the same (RuntimeError) everywhere it's called."""
    return [i["number"] for i in json.loads(_gh(["issue", "list", "-R", repo, "--label", TICKET_LABEL, "--state", state, "--json", "number"]))]


def _timeline(repo: str, number: int) -> list[dict]:
    """Every timeline event for an issue, paginated (this ticket's retry F1): the REST endpoint
    caps a page at 100, so a `reopened` event past page one would go undetected on a single
    unpaginated read."""
    events: list[dict] = []
    page = 1
    while True:
        chunk = json.loads(_gh(["api", f"repos/{repo}/issues/{number}/timeline",
                                 "-f", "per_page=100", "-f", f"page={page}"]))
        events.extend(chunk)
        if len(chunk) < 100:
            return events
        page += 1


def closed_issue_history(repo: str, number: int) -> dict:
    """Whether a closed issue's body was edited after it closed, and whether it carries a
    `reopened` timeline event (plan 4 AC-12): `userContentEdits` via GraphQL for the first,
    the REST timeline for the second — GitHub keeps no closed-issue immutability of its own
    (plan 4 decision 8). Raises RuntimeError, same as every other call here, when `gh` fails."""
    owner, name = repo.split("/", 1)
    query = ("query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name)"
             "{issue(number:$number){closedAt userContentEdits(last:20){nodes{editedAt}}}}}")
    data = json.loads(_gh(["api", "graphql", "-f", f"query={query}",
                            "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={number}"]))
    issue = data["data"]["repository"]["issue"]
    closed_at = issue.get("closedAt")
    edits = [n["editedAt"] for n in (issue.get("userContentEdits") or {}).get("nodes") or []]
    edited_after_close = bool(closed_at) and any(e > closed_at for e in edits)
    reopened = any(t.get("event") == "reopened" for t in _timeline(repo, number))
    return {"edited_after_close": edited_after_close, "reopened": reopened}


def refuse_if_closed(repo: str, number: int, issue: dict | None = None) -> dict:
    """The read that gates every write (plan 4 AC-6 / this ticket AC-3): a closed issue's status,
    body and comments are immutable here; a human reopens it from the Issues tab. Raises
    RuntimeError before any write call when the issue is closed."""
    issue = issue if issue is not None else get_issue(repo, number)
    if issue.get("state") == "CLOSED":
        raise RuntimeError(f"#{number} is closed; a human reopens it from the Issues tab")
    return issue


def log(repo: str, number: int, role: str, head: str, lines: tuple[str, ...] = (), when: str | None = None) -> str:
    """One issue comment `### [role] <timestamp> — <head>` plus lines (plan 4 AC-4). Returns the
    comment text; the caller re-syncs the mirror."""
    refuse_if_closed(repo, number)
    entry = f"### [{role}] {when or now()} — {head}" + "".join(f"\n{l}" for l in lines)
    _gh(["issue", "comment", str(number), "-R", repo, "--body", entry])
    return entry


def set_status(repo: str, number: int, status: str) -> None:
    """Exactly the new status label remains; `done` and `superseded` also close the issue (plan 4
    AC-5)."""
    if status not in STATUS_LABELS:
        raise ValueError(f"status must be one of {STATUS_LABELS}, not {status!r}")
    issue = refuse_if_closed(repo, number)
    current = _label_names(issue.get("labels") or [])
    for s in STATUS_LABELS:
        if s in current and s != status:
            _gh(["issue", "edit", str(number), "-R", repo, "--remove-label", s])
    if status not in current:
        _gh(["issue", "edit", str(number), "-R", repo, "--add-label", status])
    if status in CLOSED_STATUSES:
        _gh(["issue", "close", str(number), "-R", repo])


def create_issue(repo: str, title: str, body: str, labels: tuple[str, ...] = (TICKET_LABEL, "ready")) -> int:
    """A new issue; returns its number, parsed from `gh issue create`'s printed URL."""
    args = ["issue", "create", "-R", repo, "--title", title, "--body", body]
    for l in labels:
        args += ["--label", l]
    out = _gh(args)
    m = re.search(r"/issues/(\d+)", out)
    if not m:
        raise RuntimeError(f"gh issue create: no issue number in output: {out.strip()!r}")
    return int(m.group(1))


def create_child(repo: str, parent_number: int, parent_fm: dict, finding: dict) -> int:
    """A new issue for a rejected verdict finding: `ticket` and `ready` labels, `parent` and
    `writes` copied from the parent ticket's body, `depends_on: [#<parent_number>]` (plan 4
    AC-8). Content comes from the finding's fields only, as board.child's file-mode shape does.
    Refuses, before creating anything, when the parent is closed (AC-3)."""
    refuse_if_closed(repo, parent_number)
    plan = parent_fm.get("parent", "")
    writes = json.dumps(list(parent_fm.get("writes") or []))
    text = finding.get("text", "")
    fid = finding.get("id", "")
    kind = "critical" if finding.get("severity") == "block" else "behavioral"
    cite = finding.get("ac") or finding.get("charter") or "-"
    body = (
        f"---\nparent: {plan}\ndepends_on: [#{parent_number}]\nwrites: {writes}\n---\n\n"
        f"## Outcome\nResolve {fid} from #{parent_number}: {text}\n\n"
        f"## Acceptance criteria\n- AC-1 ({kind}): {text} — from #{parent_number} {fid} ({cite}); "
        f"repro: `{finding.get('repro') or 'n/a'}`\n\n"
        f"## Out of scope\n- everything else in #{parent_number}\n"
    )
    return create_issue(repo, text[:60] or f"child of #{parent_number}", body)


def render_mirror(issue: dict) -> str:
    """The mirror text for one issue: frontmatter with `status:` from the label, the body
    sections verbatim, and every comment concatenated under `## Log` in creation order.
    Raises ValueError, never guesses, when the issue carries no status label (charter 1)."""
    import _fm  # local: keeps this importable from a bare python3, as Packet.parse does

    number = issue["number"]
    status = _status_label(issue.get("labels") or [])
    if status is None:
        raise ValueError(f"#{number}: no status label; refusing to guess")
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
    `kanban/tickets/<number>.<slug>.md`. No-op, no `gh` call, when the marker is absent.

    Every `gh` call and every render runs before any file is written, so a `gh` failure or an
    issue lacking a status label partway through changes nothing on disk (charter 1): the
    caller reports it and the tree stays as it was — never a mirror of some issues but not
    others."""
    repo = marker(root)
    if not repo:
        return []
    ensure_labels(repo)
    numbers = list_ticket_numbers(repo)
    tickets_dir = root / "kanban" / "tickets"
    staged = []
    for n in numbers:
        issue = json.loads(_gh(["issue", "view", str(n), "-R", repo, "--json", "number,title,body,labels,comments"]))
        text = render_mirror(issue)
        if body_status_problem(issue.get("body") or ""):
            print(f"[sync] #{n}: body carries a status: line; status is the label, ignoring it", file=sys.stderr)
        staged.append((tickets_dir / f"{n}.{slug(issue.get('title', ''))}.md", text))
    tickets_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for path, text in staged:
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "sync":
        print("usage: tickets.py sync [--cwd .]", file=sys.stderr)
        return 2
    cwd = argv[argv.index("--cwd") + 1] if "--cwd" in argv else "."
    try:
        paths = sync(Path(cwd).resolve())
    except (RuntimeError, ValueError) as e:
        print(f"[sync] {e}", file=sys.stderr)
        return 1
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
