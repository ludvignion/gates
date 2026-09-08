#!/usr/bin/env python3
"""The Gate 1 and Gate 2 actions, each a function; kanban_ops.py's command line is the door. No server.

Every action writes the Log / files and commits exactly as a human would, through the code the
runner and the lint already use (kanban_ops for Log lines and status, render_verdict for the
recommended action and the next child id, verdict_eval.record_decision for the dataset item).

Gate 1 (plans):  approve · override backend/scrutiny (router miss logged, like runner --override).
Gate 2 (tickets in_review, verdict present):
  ship    — status done, Log entry, commit on the ticket branch, dataset item expected=ship,
            merge --no-ff into the base branch (main, else master), push (when a remote exists),
            delete the branch, remove the .worktrees/<id>/ worktree. Runs only from the ticket
            branch or the base branch; ends on the base branch checked out.
  reject  — Log entry with the reason, status in_progress, commit, dataset item expected=reject.
            Stays on the ticket branch when the checkout already is there (the runner leaves it
            so after a verdict); from the base branch it checks the ticket branch out and returns.
  child   — a ticket <id>.<n> written from the finding: Outcome and AC from the block's text
            and citation, depends_on the parent, the parent's writes; Log entries on both.
  home    — the warn's `home` set and a `— finding: … home: <id>` Log entry, as lint expects.
  waive   — `[human] — waive F# by <who>: <reason>` Log entry (lint's waiver), finding.waived_by set.
Every Log entry written here carries the `[human]` role (lint reads waivers and approvals by
role) with `who` in the head. `plan_order` is kanban_ops.plan_order (the runner's walk order).
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import kanban_ops  # noqa: E402
import render_verdict  # noqa: E402
import schemas  # noqa: E402
import verdict_eval  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
TICKET_TEMPLATE = SCRIPTS.parent / "templates" / "ticket.md"


class BoardError(Exception):
    """A refused action, with the reason a human reads."""


# --- where a ticket's files live ---------------------------------------------------------------
def branch_tree(root: Path, tid: str):
    """(path, restore) for editing the ticket's branch: the .worktrees/<id>/ worktree when the
    runner kept one, else ticket/<id> checked out in place (restored afterwards)."""
    wt = root / ".worktrees" / tid
    if wt.is_dir():
        return wt, (lambda: None)
    branch = f"ticket/{tid}"
    if not _has_branch(root, branch):
        return root, (lambda: None)  # no branch: the ticket lives on the current branch (session backend)
    orig = current_branch(root)
    if orig == branch:
        return root, (lambda: None)
    if not _clean(root):
        raise BoardError("the tree is dirty; commit or stash before acting")
    _git(root, "checkout", "-q", branch)
    return root, (lambda: _git(root, "checkout", "-q", orig))


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise BoardError(f"git {' '.join(args)}: {r.stderr.strip()[-300:]}")
    return r.stdout


def _has_branch(root: Path, branch: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "-q", branch], cwd=root, capture_output=True).returncode == 0


def current_branch(root: Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def base_branch(root: Path) -> str:
    """The branch a ship merges into: main, else master (E16: the ship ends here, checked out)."""
    for b in ("main", "master"):
        if _has_branch(root, b):
            return b
    raise BoardError("no main or master branch to merge into")


def _clean(root: Path) -> bool:
    return subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True, text=True).stdout.strip() == ""


def _ticket(tree: Path, tid: str) -> Path:
    p = kanban_ops.find_ticket(tree, tid)
    if p is None:
        raise BoardError(f"no ticket {tid} under kanban/")
    return p


def _verdict(tree: Path, tid: str) -> tuple[Path, schemas.Verdict]:
    vp = tree / "traces" / "verdict" / f"{tid}.json"
    if not vp.exists():
        raise BoardError(f"no verdict for {tid}: run it first")
    return vp, schemas.Verdict.load(vp)


def page(tree: Path, tid: str) -> str:
    """Re-render the Gate 2 page from disk (html only: the summary cache and the stamp stay) and
    return its path for the commit. E20: a render after the commit left the tracked page and
    summary dirty, and the ship's checkout to base then refused."""
    render_verdict.view(tree, tid)
    return f"traces/verdict/{tid}.html"


VERDICT_FILES = "traces/verdict/{tid}."


def commit_page(root: Path, tid: str) -> str | None:
    """Commit whatever is dirty under traces/verdict/<tid>.* (a page re-rendered by an older
    action) so a ship can leave the branch; anything else dirty stays the human's."""
    dirty = [l[3:] for l in subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True, text=True).stdout.splitlines()]
    mine = [f for f in dirty if f.startswith(VERDICT_FILES.format(tid=tid))]
    return kanban_ops.commit(root, mine, f"docs({tid}): gate 2 page", force=True) if mine else None


def _finding(v: schemas.Verdict, fid: str) -> dict:
    f = next((f for f in v.findings if f.get("id") == fid), None)
    if f is None:
        raise BoardError(f"no finding {fid} in the verdict for {v.ticket}")
    return f


# --- Gate 1 -----------------------------------------------------------------------------------
def approve(root: Path, n: str, who: str) -> str:
    """Gate 1: status approved, the `approved:` stamp, a [human] Log entry, one commit."""
    plan = kanban_ops.find_plan(root, n)
    if plan is None:
        raise BoardError(f"no plan {n} under kanban/")
    text = plan.read_text(encoding="utf-8")
    missing = schemas.RoutingStamp.parse(text).missing()
    if missing:
        raise BoardError(f"plan {n} lacks its routing stamp ({', '.join(missing)}); the grill stamps it, approval waits")
    if _fm.parse(text)[0].get("approved"):
        raise BoardError(f"plan {n} is already approved")
    stamp = f"{who} {time.strftime('%Y-%m-%d')}"
    text = re.sub(r"^status:\s*\S+", "status: approved", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^approved:[^\n]*$", f"approved: {stamp}   # approved from the board", text, count=1, flags=re.MULTILINE)
    plan.write_text(text, encoding="utf-8")
    kanban_ops.append_log(plan, "human", f"approved by {who} (gate 1, from the board)")
    sha = kanban_ops.commit(root, [str(plan.relative_to(root))], f"docs(plan {n}): approved by {who}")
    return f"plan {n} approved by {stamp} ({sha})"


def override(root: Path, n: str, field: str, value: str, who: str = "human") -> str:
    """A router miss by hand: the stamp restamped, the miss logged, one commit."""
    old, new = kanban_ops.override_plan(root, n, field, value, who=who)
    plan = kanban_ops.find_plan(root, n)
    sha = kanban_ops.commit(root, [str(plan.relative_to(root)), "traces/grill-misses.jsonl"], f"docs(plan {n}): router miss — {field} {old} → {new}")
    return f"plan {n} {field} {old} → {new} ({sha})"


# --- Gate 2 -----------------------------------------------------------------------------------
def ship(root: Path, tid: str, who: str = "human") -> str:
    """Gate 2 ship: from ticket/<id> or the base branch only; merges --no-ff into base, pushes,
    deletes the branch, and ends on base checked out (E16)."""
    branch = f"ticket/{tid}"
    base = base_branch(root)
    here = current_branch(root)
    if here not in (branch, base):
        raise BoardError(f"ship {tid} from the ticket branch or {base}, not {here}")
    tree, restore = branch_tree(root, tid)
    try:
        path = _ticket(tree, tid)
        vp, v = _verdict(tree, tid)
        log = schemas.Log.parse(_fm.read(path)[1])
        open_blocks = [f for f in v.open_blocks() if f.get("id") not in log.waived_ids() and not f.get("spawn_child")]
        if open_blocks:
            raise BoardError("open block(s) without a waiver or a child: " + ", ".join(f["id"] for f in open_blocks) + " — waive, create the child, or rework")
        kanban_ops.set_status(path, "done")
        kanban_ops.append_log(path, "human", f"ship by {who} (gate 2, from the board)",
                              tuple(f"- {f['severity']} {f['id']} {f.get('ac') or f.get('charter') or '-'}: {f['text']}" for f in v.findings if v.is_open(f)))
        kanban_ops.commit(tree, [str(path.relative_to(tree))], f"docs({tid}): ship — gate 2 by {who}")
        record = verdict_eval.record_decision(tree, tid, "ship")
    finally:
        restore()
    lines = [record]
    if _has_branch(root, branch):
        if tree != root:  # a worktree holds the branch: remove it first, the merge needs the branch free
            _git(root, "worktree", "remove", "--force", str(tree))
        if current_branch(root) != base:  # the runner left the checkout on the ticket branch
            if commit_page(root, tid):
                lines.append("gate 2 page committed")
            if not _clean(root):
                raise BoardError(f"the tree is dirty; commit or stash before shipping from {branch}")
            _git(root, "checkout", "-q", base)
        _git(root, *kanban_ops.GIT_IDENTITY, "merge", "--no-ff", "-q", branch, "-m", f"merge({tid}): {_title(root, tid)}")
        lines.append(f"merged {branch} --no-ff into {base}")
        if _git(root, "remote").strip():
            r = subprocess.run(["git", "push", "-q"], cwd=root, capture_output=True, text=True)
            lines.append("pushed" if r.returncode == 0 else f"push failed: {r.stderr.strip()[-200:]}")
        else:
            lines.append("no remote: not pushed")
        _git(root, "branch", "-d", branch)
        lines.append(f"deleted {branch}")
    return f"{tid} shipped; " + "; ".join(lines)


def _title(root: Path, tid: str) -> str:
    p = kanban_ops.find_ticket(root, tid)
    if p is None:
        return tid
    return next((l[2:].strip() for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("# ")), tid)


def reject(root: Path, tid: str, reason: str, who: str = "human") -> str:
    """Gate 2 reject: status in_progress and the reason on the ticket branch. The checkout stays
    where it was: on the ticket branch when already there, back on base when it started there."""
    if not reason.strip():
        raise BoardError("a reject needs a reason")
    tree, restore = branch_tree(root, tid)
    try:
        path = _ticket(tree, tid)
        _verdict(tree, tid)
        kanban_ops.set_status(path, "in_progress")
        kanban_ops.append_log(path, "human", f"reject by {who} (gate 2, from the board): {reason.strip()}")
        sha = kanban_ops.commit(tree, [str(path.relative_to(tree))], f"docs({tid}): reject — gate 2 by {who}")
        record = verdict_eval.record_decision(tree, tid, "reject")
    finally:
        restore()
    return f"{tid} rejected ({sha}); {record}"


def child(root: Path, tid: str, fid: str, who: str = "human") -> str:
    """A child ticket from a finding. Its content comes from verdict.json fields only."""
    tree, restore = branch_tree(root, tid)
    try:
        parent = _ticket(tree, tid)
        vp, v = _verdict(tree, tid)
        f = _finding(v, fid)
        fm, _ = _fm.read(parent)
        tickets = render_verdict.tickets_of(tree)
        cid = render_verdict.next_child(tid, tickets)
        slug = "-".join(w for w in "".join(c if c.isalnum() else " " for c in f.get("text", "")).lower().split()[:4]) or "child"
        cite = f.get("ac") or f.get("charter") or "-"
        kind = "critical" if f.get("severity") == "block" else "behavioral"
        body = TICKET_TEMPLATE.read_text(encoding="utf-8")
        body = (body.replace("id: <n>.<m>", f"id: {cid}").replace("parent: <n>", f"parent: {fm.get('parent', tid.split('.')[0])}")
                .replace("status: ready            # ready | in_progress | in_review | done", "status: ready")
                .replace("depends_on: []", f"depends_on: [{tid}]")
                .replace("writes: []               # paths the build may touch; enforced by hook if set", f"writes: {json.dumps(list(fm.get('writes') or []))}")
                .replace("# <n>.<m> <title>", f"# {cid} {f.get('text', '')[:60]}")
                .replace("<one sentence>", f"Resolve {fid} from {tid}: {f.get('text', '')}")
                .replace("- AC-1 (behavioral): Given / When / Then\n- AC-2 (critical): ...",
                         f"- AC-1 ({kind}): {f.get('text', '')} — from {tid} {fid} ({cite}); repro: `{f.get('repro') or 'n/a'}`")
                .replace("## Out of scope\n- ...", f"## Out of scope\n- everything else in {tid}")
                .replace("### [grill] <YYYY-MM-DD HH:MM> — created", f"### [human] {kanban_ops.now()} — created by {who} from {tid} {fid} (gate 2, from the board)"))
        cpath = parent.parent / f"{cid}.{slug}.md"
        if cpath.exists():
            raise BoardError(f"{cpath.name} already exists")
        cpath.write_text(body, encoding="utf-8")
        v = v.with_findings(tuple({**x, "spawn_child": True, "home": cid} if x.get("id") == fid else x for x in v.findings))
        v.dump(vp)
        kanban_ops.append_log(parent, "human", f"child {cid} from {fid} by {who}", (f"- {f.get('text', '')} → home: {cid}",))
        sha = kanban_ops.commit(tree, [str(cpath.relative_to(tree)), str(parent.relative_to(tree)), str(vp.relative_to(tree)), page(tree, tid)],
                                f"docs({tid}): child {cid} from {fid}", force=True)
    finally:
        restore()
    return f"child {cid} created from {tid} {fid} ({sha}): {cpath.name}"


def home(root: Path, tid: str, fid: str, target: str, who: str = "human") -> str:
    """A warn homed to another ticket: finding.home set, the Log entry lint reads, one commit."""
    tree, restore = branch_tree(root, tid)
    try:
        path = _ticket(tree, tid)
        vp, v = _verdict(tree, tid)
        f = _finding(v, fid)
        if kanban_ops.find_ticket(tree, target) is None:
            raise BoardError(f"no ticket {target} to home {fid} to")
        v = v.with_findings(tuple({**x, "home": target} if x.get("id") == fid else x for x in v.findings)).dump(vp) or schemas.Verdict.load(vp)
        kanban_ops.append_log(path, "human", f"finding: {f.get('text', '')}", (f"- {fid} home: {target} (by {who})",))
        sha = kanban_ops.commit(tree, [str(path.relative_to(tree)), str(vp.relative_to(tree)), page(tree, tid)], f"docs({tid}): {fid} homed to {target}", force=True)
    finally:
        restore()
    return f"{tid} {fid} homed to {target} ({sha})"


def waive(root: Path, tid: str, fid: str, reason: str, who: str = "human") -> str:
    """A finding waived with a reason: the Log waiver lint reads, finding.waived_by, one commit."""
    if not reason.strip():
        raise BoardError("a waiver needs a reason")
    tree, restore = branch_tree(root, tid)
    try:
        path = _ticket(tree, tid)
        vp, v = _verdict(tree, tid)
        _finding(v, fid)
        when = kanban_ops.now()
        kanban_ops.append_log(path, "human", f"waive {fid} by {who}: {reason.strip()}", when=when)
        v = v.with_findings(tuple({**x, "waived_by": f"{who} {when}"} if x.get("id") == fid else x for x in v.findings))
        v.dump(vp)
        sha = kanban_ops.commit(tree, [str(path.relative_to(tree)), str(vp.relative_to(tree)), page(tree, tid)], f"docs({tid}): {fid} waived by {who}", force=True)
    finally:
        restore()
    return f"{tid} {fid} waived ({sha})"


# --- lookups the runner and the command line share --------------------------------------------
plan_order = kanban_ops.plan_order  # contract D: one walk order, defined once in kanban_ops


def ticket_status(root: Path, tid: str) -> str:
    """The ticket's frontmatter status on the current checkout; "missing" when there is no file."""
    p = kanban_ops.find_ticket(root, tid)
    return str(_fm.read(p)[0].get("status", "")) if p else "missing"


# --- the door -----------------------------------------------------------------------------------
ACTIONS = {"approve": approve, "override": override, "ship": ship, "reject": reject, "child": child, "home": home, "waive": waive}


def act(root: Path, form: dict[str, str]) -> str:
    """Dispatch one form (kanban_ops.py's command line builds it). Every branch is a function
    above; nothing else writes."""
    a = form.get("action", "")
    who = form.get("who") or "human"
    if a == "approve":
        return approve(root, form["plan"], who)
    if a == "override":
        return override(root, form["plan"], form["field"], form["value"], who)
    if a == "ship":
        return ship(root, form["ticket"], who)
    if a == "reject":
        return reject(root, form["ticket"], form.get("reason", ""), who)
    if a == "child":
        return child(root, form["ticket"], form["finding"], who)
    if a == "home":
        return home(root, form["ticket"], form["finding"], form["target"], who)
    if a == "waive":
        return waive(root, form["ticket"], form["finding"], form.get("reason", ""), who)
    raise BoardError(f"unknown action {a!r}")
