#!/usr/bin/env python3
"""The board acts. Run: board.py serve [--port 8765] [--cwd .]   (localhost only, no auth)

Serves traces/board.html with an actions panel, and the gate pages (traces/verdict/<id>.html).
Every action writes the Log / files and commits exactly as a human would, through the code the
runner and the lint already use (kanban_ops for Log lines and status, render_verdict for the
recommended action and the next child id, verdict_eval.record_decision for the dataset item,
runner.py for runs). Refuses to start on a dirty tree.

Gate 1 (plans):  approve · override backend/scrutiny (router miss logged, like runner --override).
Gate 2 (tickets in_review, verdict present):
  ship    — status done, Log entry, commit on the ticket branch, dataset item expected=ship,
            merge --no-ff into main, push (when a remote exists), delete the branch, remove the
            .worktrees/<id>/ worktree.
  reject  — Log entry with the reason, status in_progress, commit, dataset item expected=reject.
  child   — a ticket <id>.<n> written from the finding: Outcome and AC from the block's text
            and citation, depends_on the parent, the parent's writes; Log entries on both.
  home    — the warn's `home` set and a `— finding: … home: <id>` Log entry, as lint expects.
  waive   — `[human] — waive F# by <who>: <reason>` Log entry (lint's waiver), finding.waived_by set.
Every Log entry the board writes carries the `[human]` role (lint reads waivers and approvals
by role) with `who` in the head.
  rerun   — runner.py for the ticket.
Run:  a ticket, or --plan <n>: tickets in depends_on order, one runner each, pausing at every
      Gate 2 until the ticket is shipped from the board. Progress (the runner's phase lines) is
      shown live from traces/runs/<id>.log.
"""
import argparse
import html
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import kanban_ops  # noqa: E402
import render_board  # noqa: E402
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
    if subprocess.run(["git", "rev-parse", "--verify", "-q", branch], cwd=root, capture_output=True).returncode != 0:
        return root, (lambda: None)  # no branch: the ticket lives on the current branch (session backend)
    orig = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
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


def _finding(v: schemas.Verdict, fid: str) -> dict:
    f = next((f for f in v.findings if f.get("id") == fid), None)
    if f is None:
        raise BoardError(f"no finding {fid} in the verdict for {v.ticket}")
    return f


# --- Gate 1 -----------------------------------------------------------------------------------
def approve(root: Path, n: str, who: str) -> str:
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
    import re

    text = re.sub(r"^status:\s*\S+", "status: approved", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^approved:[^\n]*$", f"approved: {stamp}   # approved from the board", text, count=1, flags=re.MULTILINE)
    plan.write_text(text, encoding="utf-8")
    kanban_ops.append_log(plan, "human", f"approved by {who} (gate 1, from the board)")
    sha = kanban_ops.commit(root, [str(plan.relative_to(root))], f"docs(plan {n}): approved by {who}")
    return f"plan {n} approved by {stamp} ({sha})"


def override(root: Path, n: str, field: str, value: str, who: str = "human") -> str:
    old, new = kanban_ops.override_plan(root, n, field, value, who=who)
    plan = kanban_ops.find_plan(root, n)
    sha = kanban_ops.commit(root, [str(plan.relative_to(root)), "traces/grill-misses.jsonl"], f"docs(plan {n}): router miss — {field} {old} → {new}")
    return f"plan {n} {field} {old} → {new} ({sha})"


# --- Gate 2 -----------------------------------------------------------------------------------
def ship(root: Path, tid: str, who: str = "human") -> str:
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
    branch = f"ticket/{tid}"
    has_branch = subprocess.run(["git", "rev-parse", "--verify", "-q", branch], cwd=root, capture_output=True).returncode == 0
    lines = [record]
    if has_branch:
        if tree != root:  # a worktree holds the branch: remove it first, the merge needs the branch free
            _git(root, "worktree", "remove", "--force", str(tree))
        _git(root, *kanban_ops.GIT_IDENTITY, "merge", "--no-ff", "-q", branch, "-m", f"merge({tid}): {_title(root, tid)}")
        lines.append(f"merged {branch} --no-ff into {_git(root, 'rev-parse', '--abbrev-ref', 'HEAD').strip()}")
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
        sha = kanban_ops.commit(tree, [str(cpath.relative_to(tree)), str(parent.relative_to(tree)), str(vp.relative_to(tree))],
                                f"docs({tid}): child {cid} from {fid}")
        render_verdict.main(tree, tid, v.meta.vendor if v.meta else render_verdict.DEFAULT_VENDOR, summary_model="none")
    finally:
        restore()
    return f"child {cid} created from {tid} {fid} ({sha}): {cpath.name}"


def home(root: Path, tid: str, fid: str, target: str, who: str = "human") -> str:
    tree, restore = branch_tree(root, tid)
    try:
        path = _ticket(tree, tid)
        vp, v = _verdict(tree, tid)
        f = _finding(v, fid)
        if kanban_ops.find_ticket(tree, target) is None:
            raise BoardError(f"no ticket {target} to home {fid} to")
        v = v.with_findings(tuple({**x, "home": target} if x.get("id") == fid else x for x in v.findings)).dump(vp) or schemas.Verdict.load(vp)
        kanban_ops.append_log(path, "human", f"finding: {f.get('text', '')}", (f"- {fid} home: {target} (by {who})",))
        sha = kanban_ops.commit(tree, [str(path.relative_to(tree)), str(vp.relative_to(tree))], f"docs({tid}): {fid} homed to {target}")
        render_verdict.main(tree, tid, v.meta.vendor if v.meta else render_verdict.DEFAULT_VENDOR, summary_model="none")
    finally:
        restore()
    return f"{tid} {fid} homed to {target} ({sha})"


def waive(root: Path, tid: str, fid: str, reason: str, who: str = "human") -> str:
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
        sha = kanban_ops.commit(tree, [str(path.relative_to(tree)), str(vp.relative_to(tree))], f"docs({tid}): {fid} waived by {who}")
        render_verdict.main(tree, tid, v.meta.vendor if v.meta else render_verdict.DEFAULT_VENDOR, summary_model="none")
    finally:
        restore()
    return f"{tid} {fid} waived ({sha})"


# --- runs -------------------------------------------------------------------------------------
def run_log(root: Path, tid: str) -> Path:
    return root / "traces" / "runs" / f"{tid}.log"


def start_run(root: Path, tid: str, extra: tuple[str, ...] = ()) -> subprocess.Popen:
    """runner.py for one ticket, phase lines into traces/runs/<id>.log."""
    log = run_log(root, tid)
    log.parent.mkdir(parents=True, exist_ok=True)
    f = log.open("w", encoding="utf-8")
    return subprocess.Popen([sys.executable, str(SCRIPTS / "runner.py"), tid, "--cwd", str(root), *extra], cwd=root, stdout=f, stderr=subprocess.STDOUT, text=True)


def plan_order(root: Path, n: str) -> list[str]:
    """Tickets of plan n in depends_on order (a dependency before its dependants), done ones out."""
    rows = [(fm["id"], fm.get("status", "ready"), list(fm.get("depends_on") or [])) for _, fm, _ in _fm.tickets(root / "kanban") if str(fm.get("parent", "")) == str(n)]
    order: list[str] = []
    pending = {tid: deps for tid, status, deps in rows if status not in schemas.CLOSED_STATUSES}
    while pending:
        ready = [tid for tid, deps in pending.items() if all(d in order or d not in pending for d in deps)]
        if not ready:
            raise BoardError(f"plan {n}: depends_on cycle among {', '.join(sorted(pending))}")
        for tid in sorted(ready, key=lambda t: [int(x) for x in t.split(".")]):
            order.append(tid)
            pending.pop(tid)
    return order


def ticket_status(root: Path, tid: str) -> str:
    p = kanban_ops.find_ticket(root, tid)
    return str(_fm.read(p)[0].get("status", "")) if p else "missing"


class Walker(threading.Thread):
    """--plan <n>: one runner per ticket in order; after a ship exit it waits until the board's
    ship flips the ticket to done, then moves on. Stops on any other exit."""

    def __init__(self, root: Path, n: str, poll: float = 2.0):
        super().__init__(daemon=True)
        self.root, self.n, self.poll = root, n, poll
        self.state = "queued"
        self.current: str | None = None

    def run(self) -> None:
        for tid in plan_order(self.root, self.n):
            self.current, self.state = tid, "running"
            rc = start_run(self.root, tid).wait()
            if rc != 0:
                self.state = f"stopped at {tid}: runner exit {rc}"
                return
            self.state = f"gate 2: {tid} awaits ship"
            while ticket_status(self.root, tid) != "done":
                time.sleep(self.poll)
        self.state = "done"


# --- the page ---------------------------------------------------------------------------------
ACTIONS = {"approve": approve, "override": override, "ship": ship, "reject": reject, "child": child, "home": home, "waive": waive}


def act(root: Path, form: dict[str, str]) -> str:
    """Dispatch one form post. Every branch is a function above; nothing else writes."""
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


def panel(root: Path, notice: str = "", runs: dict | None = None) -> str:
    """The actions panel above the board: one form per gate the tree is waiting on."""
    e = html.escape
    parts = [f"<p class=notice>{e(notice)}</p>" if notice else ""]
    plans = sorted(((p.name[: -len('.plan.md')], _fm.read(p)[0], p.read_text()) for p in (root / 'kanban').rglob('*.plan.md')), key=lambda x: x[0])
    for n, fm, text in plans:
        st = schemas.RoutingStamp.parse(text)
        row = f"<b>Plan {e(n)}</b> {e(fm.get('status', '?'))}"
        if not fm.get("approved"):
            row += f' <form method=post action=/action><input type=hidden name=action value=approve><input type=hidden name=plan value="{e(n)}"><input name=who placeholder=who required><button>approve</button></form>'
        row += (f' <form method=post action=/action><input type=hidden name=action value=override><input type=hidden name=plan value="{e(n)}">'
                f'<select name=field><option>scrutiny</option><option>backend</option></select><input name=value placeholder=value required>'
                f'<button>override</button> <span class=meta>now {e(st.scrutiny)} / {e(st.backend)}</span></form>')
        row += f' <form method=post action=/run><input type=hidden name=plan value="{e(n)}"><button>run plan</button></form>'
        parts.append(f"<div class=gate>{row}</div>")
    for p, fm, body in _fm.tickets(root / "kanban"):
        tid = fm["id"]
        status = fm.get("status", "ready")
        vp = root / "traces" / "verdict" / f"{tid}.json"
        row = f"<b>{e(tid)}</b> {e(status)}"
        if vp.exists():
            v = schemas.Verdict.load(vp)
            acts = render_verdict.recommendations(v, render_verdict.tickets_of(root))
            row += f' · <a href="/verdict/{e(tid)}">gate 2 page</a> · verdict {e(v.decision)}'
            if acts:
                row += " · recommended: " + "; ".join(f"{e(fid)} {e(a)}" for fid, a in acts)
            if status == "in_review":
                row += (f' <form method=post action=/action><input type=hidden name=action value=ship><input type=hidden name=ticket value="{e(tid)}"><button>ship</button></form>'
                        f' <form method=post action=/action><input type=hidden name=action value=reject><input type=hidden name=ticket value="{e(tid)}"><input name=reason placeholder=reason required><button>reject</button></form>')
                opts = "".join(f'<option>{e(f["id"])}</option>' for f in v.findings if v.is_open(f))
                if opts:
                    row += (f' <form method=post action=/action><input type=hidden name=action value=child><input type=hidden name=ticket value="{e(tid)}"><select name=finding>{opts}</select><button>child from finding</button></form>'
                            f' <form method=post action=/action><input type=hidden name=action value=home><input type=hidden name=ticket value="{e(tid)}"><select name=finding>{opts}</select><input name=target placeholder="ticket id" required><button>home</button></form>'
                            f' <form method=post action=/action><input type=hidden name=action value=waive><input type=hidden name=ticket value="{e(tid)}"><select name=finding>{opts}</select><input name=reason placeholder=reason required><button>waive</button></form>')
        if status != "done":
            row += f' <form method=post action=/run><input type=hidden name=ticket value="{e(tid)}"><button>{"rerun" if vp.exists() else "run"}</button></form>'
        parts.append(f"<div class=gate>{row}</div>")
    for tid, proc in (runs or {}).items():
        state = "running" if proc.poll() is None else f"exit {proc.returncode}"
        parts.append(f'<div class=run><b>run {e(tid)}</b> {e(state)} <pre id="log-{e(tid)}" class=log data-ticket="{e(tid)}"></pre></div>')
    return "".join(parts)


PAGE_CSS = ("<style>.panel{background:#eef;padding:.6rem 1rem;border-radius:8px;margin:0 0 1rem}.gate{margin:.3rem 0}.gate form{display:inline}"
            ".notice{color:#0a5;font-weight:600}.meta{color:#666;font-size:.85em}.log{background:#111;color:#ddd;padding:.5rem;max-height:20rem;overflow:auto;font-size:.8em}</style>")
PAGE_JS = ("<script>function tick(){document.querySelectorAll('pre.log').forEach(function(p){fetch('/progress?ticket='+p.dataset.ticket).then(function(r){return r.text()})"
           ".then(function(t){p.textContent=t;p.scrollTop=p.scrollHeight})})}setInterval(tick,2000);tick();</script>")


class Board:
    def __init__(self, root: Path):
        self.root = root
        self.runs: dict[str, subprocess.Popen] = {}
        self.walkers: dict[str, Walker] = {}
        self.lock = threading.Lock()

    def page(self, notice: str = "") -> str:
        with self.lock:
            render_board.main(self.root)
            board_html = (self.root / "traces" / "board.html").read_text(encoding="utf-8")
            body = panel(self.root, notice, self.runs)
        walkers = "".join(f"<div class=run><b>plan {html.escape(n)}</b> {html.escape(w.state)}</div>" for n, w in self.walkers.items())
        return board_html.replace("<h1>Board</h1>", f"<h1>Board</h1>{PAGE_CSS}<div class=panel>{walkers}{body}</div>{PAGE_JS}", 1)

    def action(self, form: dict[str, str]) -> str:
        with self.lock:
            try:
                return act(self.root, form)
            except BoardError as e:
                return f"refused: {e}"

    def run(self, form: dict[str, str]) -> str:
        with self.lock:
            if "plan" in form:
                n = form["plan"]
                if n in self.walkers and self.walkers[n].is_alive():
                    return f"plan {n} already walking"
                w = Walker(self.root, n)
                self.walkers[n] = w
                w.start()
                return f"walking plan {n}: {', '.join(plan_order(self.root, n)) or 'nothing to do'}"
            tid = form["ticket"]
            if tid in self.runs and self.runs[tid].poll() is None:
                return f"{tid} already running"
            self.runs[tid] = start_run(self.root, tid)
            return f"runner started for {tid}"

    def gate2(self, tid: str) -> tuple[str, str, int]:
        """The Gate 2 page, rendered on demand when only the JSON is there (no model call)."""
        p = self.root / "traces" / "verdict" / f"{tid}.html"
        if not p.exists():
            with self.lock:
                render_verdict.view(self.root, tid)  # html only; the JSON stays as committed
        if not p.exists():
            return f"no gate 2 page for {html.escape(tid)}", "text/html; charset=utf-8", 404
        return p.read_text(encoding="utf-8"), "text/html; charset=utf-8", 200

    def progress(self, tid: str) -> str:
        log = run_log(self.root, tid)
        return log.read_text(encoding="utf-8", errors="replace")[-20000:] if log.exists() else ""


def handler(board: Board):
    class H(BaseHTTPRequestHandler):
        def _send(self, body: str, ctype: str = "text/html; charset=utf-8", code: int = 200) -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path in ("/", "/board.html"):
                self._send(board.page(q.get("notice", "")))
            elif u.path.startswith("/verdict/"):
                tid = u.path[len("/verdict/"):]
                self._send(*board.gate2(tid))
            elif u.path == "/progress":
                self._send(board.progress(q.get("ticket", "")), "text/plain; charset=utf-8")
            else:
                self._send("not found", code=404)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            notice = board.action(form) if self.path == "/action" else board.run(form) if self.path == "/run" else "not found"
            self.send_response(303)
            self.send_header("Location", "/?notice=" + quote_plus(notice))
            self.end_headers()

        def log_message(self, fmt, *args) -> None:  # quiet
            pass

    return H


def serve(root: Path, port: int) -> ThreadingHTTPServer:
    if not (root / "kanban").is_dir():
        raise BoardError(f"{root} has no kanban/")
    if not _clean(root):
        raise BoardError("the tree is dirty; commit or stash before serving the board")
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(Board(root)))
    return server


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["serve"])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--cwd", default=".")
    a = ap.parse_args(argv[1:])
    try:
        server = serve(Path(a.cwd).resolve(), a.port)
    except BoardError as e:
        print(f"[board] {e}", file=sys.stderr)
        return 1
    print(f"[board] http://127.0.0.1:{a.port}/  (ctrl-c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
