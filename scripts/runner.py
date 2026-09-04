#!/usr/bin/env python3
"""Headless state machine for one ticket, run from the main checkout.
Usage: python runner.py <ticket id> [--max-retries 2] [--cwd .] [--build-model sonnet] [--verdict-model opus]
                        [--arm blind|packet|repo] [--verdict-cmd "<template>"] [--summary-model haiku]
                        [--parallel] [--override backend=<x>|scrutiny=<y>]
States: branch → ci-pre → build (skipped when in_review with tests/feat/close-out commits) →
status → ci → verdict → close-out → (ship | block→retry | child→human). Phase lines:
branch|worktree · ci-pre · build attempt n · tests commit · feat commit · build close-out · ci ·
verdict · close-out.
Each state is a `claude -p` call or a shell command; transitions only on objective signals
(exit codes, build status line in the ticket Log, verdict.json decision and findings).
The machine, not the model, owns the loop. Every phase prints `[runner hh:mm:ss +m:ss] <phase>`
with the builder's output streamed under it, and re-renders traces/board.html in the main
checkout.

Where it builds: branch ticket/<id> checked out in place (the tree must be clean; the original
branch is restored at the end), or with --parallel a worktree under .worktrees/<id>/ (kept in
.git/info/exclude; the board's ship removes it).

Build seat: /build streamed, with a shell allowlist and denied prompts. The session ends at the
close-out (status line committed): any later tool call is logged on the ticket as
`orbit after close-out` and the session is terminated; permission denials are fatal only when
the close-out was not reached. Traced to Opik per attempt: input = ticket + plan ACs, output =
commits + CI, metadata = attempt, cost, seconds per phase.

Verdict seat: the runner writes the packet (verdict_prep.py --arm), runs --verdict-cmd over it
(default: the packet on stdin as a tool-less claude prompt; the reply is the verdict), then
closes out (render_verdict.py: validate for the arm, stamp meta, summarise, render), commits
traces/verdict/<id>.{input.md,json,summary.json,html} on the ticket branch, records the Gate 2
decision as the Opik dataset item for the packet, and reads the decision. The vendor in the
stamp is the template's executable name. A non-zero exit, an empty result, or JSON that fails
the Verdict schema is an error: logged, traced with the CLI envelope and the stderr tail,
treated as a reject to retry. "opik: tracing to <url>" or "opik: untraced (<reason>)" is the
first line printed and lands in meta.opik.

--override backend=<x>|scrutiny=<y> restamps the ticket's plan before the run, writes the router
miss to the plan Log and traces/grill-misses.jsonl, and commits.

Exit codes: 0 ship (review the Gate 2 page, merge from the board) · 1 red baseline or retry cap ·
2 human gate (blocks all spawn child tickets, or same blocks as previous verdict) ·
3 build reported NEEDS_CONTEXT (grill miss logged) or BLOCKED (see ticket Log) ·
4 permission denied before the close-out (never retried; see BUILD_ALLOWED_TOOLS and the
project's .claude/settings.json allowlist).
Run in a container when unattended (see Dockerfile).
"""
import argparse
import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import kanban_ops  # noqa: E402
import render_board  # noqa: E402
import render_verdict  # noqa: E402
import schemas  # noqa: E402
import vendor  # noqa: E402

STATUS_RE = re.compile(r"^### \[build\] .*— status: (NEEDS_CONTEXT|BLOCKED|DONE)\b", re.M)
SCRIPTS = Path(__file__).resolve().parent
DEFAULT_VERDICT_MODEL = "opus"
DEFAULT_VERDICT_CMD = 'claude -p --model {model} --output-format json --tools "" < {packet}'
VERDICT_CMD_HELP = (
    "shell template for the one verdict call, run in the worktree. Placeholders: "
    "{packet} = packet path (traces/verdict/<id>.input.md), {output} = where the verdict JSON must land "
    "(traces/verdict/<id>.json), {model} = --verdict-model, {ticket} = ticket id. Paths are relative to "
    "the worktree and contain no spaces. The default feeds the packet on stdin as the prompt with no tools; "
    "never as an argument, a packet starts with ---. The model's reply is the verdict JSON, which the runner "
    "takes from the `result` field of claude's --output-format json and writes to {output}. A command that "
    "writes {output} itself is left alone. A non-zero exit, an empty result, or JSON that fails the Verdict "
    "schema is an error, treated as a reject to retry. The stamp's vendor is the template's first word. When "
    "stdout is one JSON object with total_cost_usd and usage, they are stamped as meta.cost_usd and "
    "meta.tokens; other vendors leave them null. "
    f"Default: {DEFAULT_VERDICT_CMD}"
)
OPIK_ENV = ("OPIK_URL_OVERRIDE",)


def sh(args: list[str], cwd: Path) -> int:
    return subprocess.run(args, cwd=cwd).returncode


def workspace(repo: Path, tid: str, parallel: bool) -> tuple[Path, bool, "callable"]:
    """Where the ticket is built. Default: branch ticket/<id> checked out in place in the main
    checkout, restored afterwards. --parallel: a worktree under .worktrees/<id>/ (excluded via
    .git/info/exclude), kept until the board's ship removes it. Returns (path, fresh, restore).
    Refuses a dirty tree."""
    if not clean_tree(repo):
        raise SystemExit("[runner] the tree is dirty; commit or stash before running a ticket")
    branch = f"ticket/{tid}"
    branch_exists = sh(["git", "rev-parse", "--verify", "-q", branch], repo) == 0
    if parallel:
        path = repo / ".worktrees" / tid
        exclude = repo / ".git" / "info" / "exclude"
        if exclude.parent.is_dir() and ".worktrees/" not in (exclude.read_text() if exclude.exists() else ""):
            with exclude.open("a") as f:
                f.write(".worktrees/\n")
        if path.exists():
            return path, False, lambda: None
        cmd = (["git", "worktree", "add", str(path), branch] if branch_exists
               else ["git", "worktree", "add", "-b", branch, str(path), "HEAD"])
        if sh(cmd, repo) != 0:
            raise SystemExit(f"[runner] worktree add failed for {tid}")
        return path, True, lambda: None
    orig = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    if sh(["git", "checkout", "-q", branch] if branch_exists else ["git", "checkout", "-q", "-b", branch], repo) != 0:
        raise SystemExit(f"[runner] checkout of {branch} failed")

    def restore() -> None:
        if orig and orig != branch:
            r = subprocess.run(["git", "checkout", "-q", orig], cwd=repo, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[runner] could not switch back to {orig}; staying on {branch}: {r.stderr.strip()[-200:]}")

    return repo, not branch_exists, restore


def ticket_base(cwd: Path, branch_from: str = "main") -> str:
    """The commit the ticket branch started from: the merge base with main (or master)."""
    for b in (branch_from, "master"):
        r = subprocess.run(["git", "merge-base", b, "HEAD"], cwd=cwd, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return "HEAD~1"


def ci(cwd: Path) -> bool:
    return sh(["make", "ci"], cwd) == 0


def build_status(cwd: Path, tid: str) -> str:
    """Last build status line in the ticket Log; DONE if the builder wrote none."""
    ticket = kanban_ops.find_ticket(cwd, tid)
    if ticket is None:
        return "DONE"
    found = STATUS_RE.findall(ticket.read_text())
    return found[-1] if found else "DONE"


def log_grill_miss(cwd: Path, tid: str) -> None:
    """NEEDS_CONTEXT means an AC was ambiguous and the domain pack didn't resolve it: a grill miss."""
    p = cwd / "traces" / "grill-misses.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps({"ticket": tid, "source": "build", "status": "NEEDS_CONTEXT"}) + "\n")


# --- verdict seat ---------------------------------------------------------------------------
def vendor_of(template: str) -> str:
    """The stamp's vendor: the template's executable name (`claude`, `codex`, ...)."""
    return Path(shlex.split(template)[0]).stem


def verdict_cmd(template: str, *, packet: str, output: str, model: str, ticket: str) -> str:
    return template.format(packet=packet, output=output, model=model, ticket=ticket)


vendor_report = vendor.report  # stdout as one JSON object (claude --output-format json), else None
vendor_usage = vendor.usage  # (cost_usd, tokens) from that report; (None, None) otherwise


def verdict_from_result(stdout: str) -> dict | None:
    """The verdict JSON object inside the report's `result` text (the model's reply), fenced or
    bare; None when there is no report, no result, or no object with a `decision` in it."""
    return vendor.object_in_result(stdout, "decision")


ENVELOPE_FIELDS = ("stop_reason", "num_turns", "permission_denials", "is_error")  # claude --output-format json
STDERR_TAIL = 20  # lines


@dataclass(frozen=True)
class VendorCall:
    """One run of the verdict command: what it printed, how it exited, what it reported, and the
    one reason it failed if it did: `exit <code>`, `empty result` (no verdict written and none in
    the reply), or `invalid verdict: ...` (JSON that fails the Verdict schema). `envelope` is the
    error as traced: the reason, the CLI envelope fields (ENVELOPE_FIELDS), and the stderr tail."""

    stdout: str
    returncode: int
    cost_usd: float | None
    tokens: dict | None
    wall: float
    started: datetime
    error: str | None
    envelope: dict | None = None


def error_envelope(reason: str, stdout: str, stderr: str) -> dict:
    report = vendor_report(stdout) or {}
    return {"reason": reason, **{k: report.get(k) for k in ENVELOPE_FIELDS},
            "stderr_tail": "\n".join(stderr.splitlines()[-STDERR_TAIL:])}


def call_error(returncode: int, output: Path) -> str | None:
    if returncode != 0:
        return f"exit {returncode}"
    if not output.exists():
        return "empty result"
    try:
        problems = schemas.Verdict.load(output).problems()
    except (json.JSONDecodeError, ValueError) as e:
        return f"invalid verdict: {e}"
    return f"invalid verdict: {'; '.join(problems)}" if problems else None


# --- build seat -----------------------------------------------------------------------------
# The build session runs headless: nobody answers a permission prompt. acceptEdits covers file
# writes; Bash needs an explicit allowlist (claude --allowedTools, "Bash(git *)" syntax), and
# --permission-prompts none turns any remaining prompt into a recorded denial instead of a hang.
# The project's .claude/settings.json allowlist (project-template) covers the same commands for
# human sessions; the checkout carries that file, so both apply there.
# The session streams (stream-json): the runner prints the builder's text and tool calls under
# the phase lines, marks the tests/feat commits as phases, and once the close-out is reached
# (status line committed) treats any further tool call as orbit: logged on the ticket,
# session terminated, runner proceeds.
BUILD_ALLOWED_TOOLS = (
    "Bash(make *)", "Bash(make)", "Bash(uv *)", "Bash(git *)", "Bash(pytest *)",
    "Bash(python3 -m pytest *)", "Bash(python -m pytest *)",
)
BUILD_SYSTEM_PROMPT = (
    "The session ends at the build close-out: after the status line is written and committed, "
    "make no further tool call; print the report and stop."
)


def build_cmd(tid: str, model: str) -> list[str]:
    return ["claude", "-p", f"/build {tid}", "--model", model, "--permission-mode", "acceptEdits",
            "--permission-prompts", "none", "--allowedTools", ",".join(BUILD_ALLOWED_TOOLS),
            "--append-system-prompt", BUILD_SYSTEM_PROMPT, "--output-format", "stream-json", "--verbose"]


@dataclass(frozen=True)
class BuildCall:
    """One headless /build session: exit code, the envelope's permission_denials, the reply
    text, whether the close-out was reached, the tool calls made after it (orbit), cost, wall."""

    returncode: int
    denials: tuple[dict, ...]
    result: str
    closed_out: bool = False
    orbit: tuple[str, ...] = ()
    cost_usd: float | None = None
    wall: float = 0.0

    @property
    def denied(self) -> str:
        """One line naming what was refused; empty when nothing was."""
        parts = []
        for d in self.denials:
            name, command = d.get("tool_name", "?"), (d.get("tool_input") or {}).get("command", "")
            parts.append(f"{name}({command})" if command else str(name))
        return "; ".join(parts)


def tool_line(block: dict) -> str:
    """One line for a tool_use event: the command for Bash, the path for file tools."""
    inp = block.get("input") or {}
    name = block.get("name", "?")
    if name == "Bash":
        return f"$ {inp.get('command', '')}".rstrip()
    target = inp.get("file_path") or inp.get("path") or inp.get("pattern") or inp.get("command") or ""
    return f"{name} {target}".rstrip()


def clean_tree(cwd: Path) -> bool:
    """No tracked file modified or staged. Untracked files (the board, scratch output) do not count."""
    return subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd, capture_output=True, text=True).stdout.strip() == ""


def head_subject(cwd: Path) -> str:
    return subprocess.run(["git", "log", "-1", "--format=%s"], cwd=cwd, capture_output=True, text=True).stdout.strip()


def closed_out(cwd: Path, tid: str) -> bool:
    """The build reached its close-out: the committed ticket (HEAD) carries the status line.
    Committed state only: the builder's next command may already be running while the runner
    reads the previous result, so the working tree is never consulted here."""
    ticket = kanban_ops.find_ticket(cwd, tid)
    if ticket is None:
        return False
    rel = ticket.relative_to(cwd).as_posix()
    committed = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=cwd, capture_output=True, text=True).stdout
    return bool(STATUS_RE.search(committed))


def build(cwd: Path, tid: str, model: str, phase=None, out=None) -> BuildCall:
    """Run one /build session, streamed. `phase(name)` is called when the tests commit, the
    feat commit and the close-out appear. On the first tool call after close-out the session
    is terminated and the call is recorded in `orbit`."""
    phase = phase or (lambda name: None)
    out = out or sys.stdout
    t0 = time.monotonic()
    proc = subprocess.Popen(build_cmd(tid, model), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    report: dict = {}
    orbit: list[str] = []
    seen: set[str] = set()
    done = False
    texts: list[str] = []

    def after_tool() -> None:
        nonlocal done
        subject = head_subject(cwd)
        for mark, prefix in (("tests commit", f"test({tid})"), ("feat commit", f"feat({tid})")):
            if subject.startswith(prefix) and mark not in seen:
                seen.add(mark)
                phase(mark)
        if not done and closed_out(cwd, tid):
            done = True
            phase("build close-out")

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            out.write(f"  {line}\n")
            continue
        kind = ev.get("type")
        if kind == "assistant":
            for block in (ev.get("message") or {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    texts.append(block["text"])
                    for t in block["text"].strip().splitlines():
                        out.write(f"  {t}\n")
                elif block.get("type") == "tool_use":
                    call = tool_line(block)
                    if done:
                        orbit.append(call)
                        out.write(f"  ! orbit after close-out: {call} — session terminated\n")
                        proc.terminate()
                        break
                    out.write(f"  {call}\n")
            if done and orbit:
                break
        elif kind == "user":
            after_tool()
        elif kind == "result":
            report = ev
    stdout_rest, stderr = proc.communicate()
    if proc.returncode is None:
        proc.wait()
    sys.stderr.write(stderr or "")
    if not done and closed_out(cwd, tid):
        done = True
        phase("build close-out")
    denials = tuple(d for d in (report.get("permission_denials") or ()) if isinstance(d, dict))
    result = report.get("result") if isinstance(report.get("result"), str) else "\n".join(texts)
    cost = report.get("total_cost_usd")
    return BuildCall(proc.returncode if not orbit else 0, denials, result or "", done, tuple(orbit),
                     float(cost) if isinstance(cost, (int, float)) else None, time.monotonic() - t0)


def log_orbit(cwd: Path, tid: str, orbit: tuple[str, ...]) -> None:
    """The runner's finding on the ticket: what the builder did after its close-out."""
    path = kanban_ops.find_ticket(cwd, tid)
    if path is None:
        return
    for call in orbit:
        kanban_ops.append_log(path, "runner", f"orbit after close-out: {call}", ("- ignored; session terminated by runner",))
    kanban_ops.commit(cwd, [str(path.relative_to(cwd))], f"docs({tid}): runner — orbit after close-out")


def already_built(cwd: Path, tid: str, base: str) -> bool:
    """in_review with the tests, feat and close-out commits on the branch: skip the build session."""
    path = kanban_ops.find_ticket(cwd, tid)
    if path is None or _fm.read(path)[0].get("status") != "in_review":
        return False
    subjects = subprocess.run(["git", "log", f"{base}..HEAD", "--format=%s"], cwd=cwd, capture_output=True, text=True).stdout
    return (f"test({tid})" in subjects and f"feat({tid})" in subjects
            and ("close-out" in subjects or f"chore({tid})" in subjects))


def call_vendor(cmd: str, cwd: Path, output: Path) -> VendorCall:
    """Run the verdict command once. If it exited 0, did not write `output` itself, and its report
    carries the verdict in `result`, write that. `error` names the failure when there is one."""
    started, t0 = datetime.now(timezone.utc), time.monotonic()
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    wall = time.monotonic() - t0
    sys.stderr.write(r.stderr)
    cost_usd, tokens = vendor_usage(r.stdout)
    if r.returncode == 0 and not output.exists() and (v := verdict_from_result(r.stdout)) is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(v, indent=1) + "\n", encoding="utf-8")
    error = call_error(r.returncode, output)
    return VendorCall(r.stdout, r.returncode, cost_usd, tokens, wall, started, error,
                      error_envelope(error, r.stdout, r.stderr) if error else None)


def prep(cwd: Path, tid: str, arm: str) -> Path:
    r = subprocess.run([sys.executable, str(SCRIPTS / "verdict_prep.py"), tid, "--arm", arm],
                       cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"[runner] verdict_prep failed: {r.stderr.strip()[-400:]}")
    return cwd / r.stdout.strip().splitlines()[-1]


def opik_status() -> tuple[object | None, str]:
    """(client, line). The client exists when the package is importable and OPIK_URL_OVERRIDE
    (the SDK's own env name for the server) is set; OPIK_API_KEY alone is not a signal. The line
    is what the runner prints and the Gate 2 page shows: "tracing to <url>" or
    "untraced (<reason>)"."""
    url = next((os.environ.get(k) for k in OPIK_ENV if os.environ.get(k)), None)
    if not url:
        return None, f"untraced ({OPIK_ENV[0]} unset)"
    try:
        import opik
    except ImportError:
        return None, "untraced (opik package not importable)"
    return opik.Opik(), f"tracing to {url}"


def opik_client():
    return opik_status()[0]


def trace_verdict(client, *, tid: str, packet: Path, verdict: "schemas.Verdict | None", started: datetime, wall: float,
                  vendor_name: str, arm: str, error: dict | None = None, summary: dict | None = None) -> None:
    """One trace per model call, created whole after the call (the SDK batches; an end() right
    after create can lose data). Never raises into the state machine."""
    if client is None:
        return
    meta = verdict.meta.as_dict() if verdict and verdict.meta else {"arm": arm, "vendor": vendor_name}
    output = verdict.as_dict() if verdict else {"error": error or {"reason": "missing"}}
    if summary is not None:
        output["summary"] = summary
    try:
        client.trace(name="verdict", start_time=started, end_time=started + timedelta(seconds=wall),
                     input={"packet": packet.read_text(encoding="utf-8")},
                     output=output,
                     metadata={**meta, "ticket": tid, "wall_seconds": round(wall, 3)})
        client.flush()
    except Exception as e:  # tracing is observability, not a gate
        print(f"[runner] opik trace failed: {e}", file=sys.stderr)


VERDICT_ARTIFACTS = ("{tid}.input.md", "{tid}.json", "{tid}.summary.json", "{tid}.html")
GIT_IDENTITY = ["-c", "user.name=harness-runner", "-c", "user.email=runner@harness"]


def commit_verdict(cwd: Path, tid: str, decision: str) -> str | None:
    """Commit the verdict artifacts on the ticket branch (git add -f: projects ignore the html).
    Returns the short sha, or None when there was nothing new to commit."""
    files = [f"traces/verdict/{name.format(tid=tid)}" for name in VERDICT_ARTIFACTS if (cwd / "traces" / "verdict" / name.format(tid=tid)).exists()]
    subprocess.run(["git", "add", "-f", *files], cwd=cwd, capture_output=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=cwd).returncode == 0:
        return None
    r = subprocess.run(["git", *GIT_IDENTITY, "commit", "-q", "-m", f"docs({tid}): verdict {decision}"], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[runner] verdict artifacts not committed: {r.stderr.strip()[-200:]}")
        return None
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, capture_output=True, text=True).stdout.strip()


def verdict(cwd: Path, tid: str, model: str, arm: str = schemas.DEFAULT_ARM,
            template: str = DEFAULT_VERDICT_CMD, summary_model: str = render_verdict.DEFAULT_SUMMARY_MODEL) -> tuple[str, bool, bool]:
    """Returns (decision, retryable, progressed).
    retryable: an open block the builder can fix here (not spawn_child).
    progressed: at least one open block is new since the previous verdict; False means the
    builder and reviewer are stuck on the same findings — a plan problem, not a build problem."""
    p = cwd / "traces" / "verdict" / f"{tid}.json"
    prev = p.with_name(f"{tid}.prev.json")
    prev_open = set()
    if p.exists():
        p.replace(prev)
        prev_open = {f["id"] for f in schemas.Verdict.load(prev).open_blocks()}
    packet = prep(cwd, tid, arm)
    cmd = verdict_cmd(template, packet=str(packet.relative_to(cwd)), output=str(p.relative_to(cwd)), model=model, ticket=tid)
    client, opik_line = opik_status()
    call = call_vendor(cmd, cwd, p)
    if vendor_report(call.stdout) is None:
        sys.stdout.write(call.stdout)  # not a report: pass the vendor's output through
    else:
        print(f"[runner] verdict call: cost_usd={call.cost_usd} tokens={call.tokens}")
    if call.error:
        print(f"[runner] verdict error: {call.error}")
        trace_verdict(client, tid=tid, packet=packet, verdict=None, started=call.started, wall=call.wall,
                      vendor_name=vendor_of(template), arm=arm, error=call.envelope)
        return "reject", True, True
    violations = render_verdict.main(cwd, tid, vendor_of(template), cost_usd=call.cost_usd, tokens=call.tokens,
                                     seconds=call.wall, opik=opik_line, summary_model=summary_model)
    for x in violations:
        print(f"[runner] verdict invalid: {x}")
    v = schemas.Verdict.load(p)
    spath = render_verdict.summary_path(cwd, tid)
    summary = json.loads(spath.read_text(encoding="utf-8")) if spath.exists() else None
    sha = commit_verdict(cwd, tid, v.decision)
    print(f"[runner] verdict artifacts committed {sha}" if sha else "[runner] verdict artifacts unchanged")
    trace_verdict(client, tid=tid, packet=packet, verdict=v, started=call.started, wall=call.wall,
                  vendor_name=vendor_of(template), arm=arm, summary=summary)
    blocks = v.open_blocks()
    retryable = any(not f.get("spawn_child", False) for f in blocks)
    progressed = not blocks or any(f.get("id") not in prev_open for f in blocks)
    return v.decision, retryable, progressed


class Phases:
    """[runner hh:mm:ss +m:ss] <phase> lines, seconds per phase, and the board re-rendered into
    the main checkout after every line."""

    def __init__(self, repo: Path, tree: Path, out=None):
        self.repo, self.tree, self.out = repo, tree, out or sys.stdout
        self.t0 = time.monotonic()
        self.current: str | None = None
        self.started = self.t0
        self.seconds: dict[str, float] = {}

    def mark(self, name: str) -> None:
        now = time.monotonic()
        if self.current:
            self.seconds[self.current] = round(self.seconds.get(self.current, 0.0) + now - self.started, 3)
        self.current, self.started = name, now
        elapsed = int(now - self.t0)
        self.out.write(f"[runner {time.strftime('%H:%M:%S')} +{elapsed // 60}:{elapsed % 60:02d}] {name}\n")
        self.out.flush()
        self.board()

    def board(self) -> None:
        try:
            with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
                render_board.main(self.tree, self.repo / "traces" / "board.html")
        except Exception as e:  # the board is a view; never a gate
            print(f"[runner] board not rendered: {e}", file=sys.stderr)

    def finish(self) -> dict[str, float]:
        if self.current:
            self.seconds[self.current] = round(self.seconds.get(self.current, 0.0) + time.monotonic() - self.started, 3)
            self.current = None
        return dict(self.seconds)


def trace_build(client, *, tid: str, cwd: Path, base: str, attempt: int, call: BuildCall, ci_green: bool, seconds: dict) -> None:
    """One trace per build attempt: input = ticket + plan ACs, output = commits + CI, metadata =
    attempts, cost, seconds per phase. Never raises into the state machine."""
    if client is None:
        return
    path = kanban_ops.find_ticket(cwd, tid)
    ticket_text = path.read_text(encoding="utf-8") if path else ""
    plan = kanban_ops.find_plan(cwd, tid.split(".")[0])
    plan_acs = schemas.section(_fm.read(plan)[1], "Acceptance criteria") if plan else ""
    commits = subprocess.run(["git", "log", f"{base}..HEAD", "--format=%h %s"], cwd=cwd, capture_output=True, text=True).stdout.strip().splitlines()
    try:
        client.trace(name="build", input={"ticket": ticket_text, "plan_acs": plan_acs},
                     output={"commits": commits, "ci_green": ci_green, "closed_out": call.closed_out, "orbit": list(call.orbit),
                             "denials": list(call.denials), "result": call.result},
                     metadata={"ticket": tid, "attempt": attempt, "cost_usd": call.cost_usd, "seconds": seconds})
        client.flush()
    except Exception as e:
        print(f"[runner] opik trace failed: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticket")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--build-model", default="sonnet")
    ap.add_argument("--verdict-model", default=DEFAULT_VERDICT_MODEL)
    ap.add_argument("--arm", choices=schemas.ARMS, default=schemas.DEFAULT_ARM,
                    help="how much context the verdict sees: blind = diff + prompt; packet = everything (default); repo = packet + read-only tree")
    ap.add_argument("--verdict-cmd", default=DEFAULT_VERDICT_CMD, metavar="TEMPLATE", help=VERDICT_CMD_HELP)
    ap.add_argument("--summary-model", default=render_verdict.DEFAULT_SUMMARY_MODEL,
                    help="model for the Gate 2 page's two prose sections (default the cheapest Claude); `none` skips the call")
    ap.add_argument("--parallel", action="store_true", help="build in a worktree under .worktrees/<id>/ instead of checking the branch out in place")
    ap.add_argument("--override", metavar="FIELD=VALUE", help="backend=<x> or scrutiny=<y>: restamp the ticket's plan, log the router miss, commit; then run")
    a = ap.parse_args()
    repo = Path(a.cwd).resolve()
    client, opik_line = opik_status()
    print(f"[runner] opik: {opik_line}")

    if a.override:
        field, _, value = a.override.partition("=")
        n = a.ticket.split(".")[0]
        old, new = kanban_ops.override_plan(repo, n, field.strip(), value.strip(), who="human")
        plan = kanban_ops.find_plan(repo, n)
        sha = kanban_ops.commit(repo, [str(plan.relative_to(repo)), "traces/grill-misses.jsonl"], f"docs(plan {n}): router miss — {field} {old} → {new}")
        print(f"[runner] override: plan {n} {field} {old} → {new} (committed {sha})")

    tree, fresh, restore = workspace(repo, a.ticket, a.parallel)
    phases = Phases(repo, tree)
    try:
        phases.mark("worktree" if a.parallel else "branch")
        print(f"  {tree} on ticket/{a.ticket} ({'new' if fresh else 'existing'})")
        base = ticket_base(tree)
        if fresh:
            phases.mark("ci-pre")
            if not ci(tree):
                print("[runner] baseline red on a fresh branch — not this ticket's fault; fix main first")
                return 1
        for attempt in range(1, a.max_retries + 1):
            if already_built(tree, a.ticket, base):
                phases.mark(f"build skipped: in_review with tests/feat/close-out commits (attempt {attempt})")
                call = BuildCall(0, (), "", closed_out=True)
            else:
                phases.mark(f"build attempt {attempt}")
                call = build(tree, a.ticket, a.build_model, phase=phases.mark)
                if call.orbit:
                    log_orbit(tree, a.ticket, call.orbit)
                if call.denials and not call.closed_out:
                    print(f"[runner] permission denied: {call.denied} — the build session cannot proceed headless; widen BUILD_ALLOWED_TOOLS or the project allowlist")
                    return 4
                if call.denials:
                    print(f"[runner] permission denied after close-out, ignored: {call.denied}")
                st = build_status(tree, a.ticket)
                if st == "NEEDS_CONTEXT":
                    print("[runner] build needs context — grill miss logged; human needed")
                    log_grill_miss(tree, a.ticket)
                    return 3
                if st == "BLOCKED":
                    print("[runner] build blocked — see ticket Log; human needed")
                    return 3
            phases.mark("ci")
            green = ci(tree)
            trace_build(client, tid=a.ticket, cwd=tree, base=base, attempt=attempt, call=call, ci_green=green, seconds=phases.finish())
            if not green:
                print("[runner] ci red")
                continue
            phases.mark("verdict")
            decision, retryable, progressed = verdict(tree, a.ticket, a.verdict_model, a.arm, a.verdict_cmd, a.summary_model)
            phases.mark("close-out")
            print(f"[runner] verdict: {decision}{' (retryable)' if retryable else ''}")
            import verdict_eval  # here, not at the top: verdict_eval imports runner

            print(f"[runner] {verdict_eval.record_decision(tree, a.ticket, decision)}")
            if decision == "ship":
                print(f"[runner] ship — review {tree / 'traces' / 'verdict' / (a.ticket + '.html')}, then merge from the board")
                return 0
            if not retryable:
                print("[runner] blocking findings all spawn child tickets — human: create children, decide on this slice")
                return 2
            if not progressed:
                print("[runner] same blocks as previous verdict — build and verdict disagree; plan problem, human needed")
                return 2
        print("[runner] retry cap reached — human needed")
        return 1
    finally:
        phases.finish()
        phases.board()
        restore()


if __name__ == "__main__":
    sys.exit(main())
