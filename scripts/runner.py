#!/usr/bin/env python3
"""Headless state machine for one ticket, or a plan's tickets in order, run from the main checkout.
Usage: python runner.py <ticket id> | --plan <n> [--max-retries 2] [--cwd .] [--build-model sonnet]
                        [--verdict-model opus] [--arm blind|packet|repo] [--verdict-cmd "<template>"]
                        [--summary-model haiku] [--parallel] [--override backend=<x>|scrutiny=<y>]
                        [--packet-cap 40000]
States: branch → ci-pre → build (skipped when in_review with tests/feat/close-out commits) →
status → ci → verdict → close-out → (ship | block→retry | child→human). Phase names:
branch|worktree · ci-pre · build <n> · build <n> close-out · build <n> skipped · tests-commit ·
feat-commit · ci · verdict · close-out · done <decision>.
Each state is a `claude -p` call or a shell command; transitions only on objective signals
(exit codes, build status line in the ticket Log, verdict.json decision and findings).
The machine, not the model, owns the loop. Every phase prints `[runner hh:mm:ss +m:ss] <phase>`
with the builder's output streamed under it (two-space indent), appends
`<hh:mm:ss> <+m:ss> <phase>` to traces/runs/<id>.state (the last line of a finished run starts
with `done `: `done ship`, `done reject`, or `done error <reason>`), and re-renders
traces/board.html in the main checkout. While the build session streams, a heartbeat line
`<hh:mm:ss> <+m:ss> <phase> · running · last <hh:mm:ss> · <last builder line>` lands in the
state file every HEARTBEAT_SECONDS (E25: a 15-minute build looked dead); it is not a phase
change. The runner's pid goes to traces/runs/<id>.pid right after the state file is truncated
(the skill's watch tails on it); nothing is removed at exit. After the verdict,
traces/runs/<id>.result holds the end-of-run lines (render_verdict.result_lines, contract C:
the summary sentences, the header, one line per open finding with its citation, file:line and
recommended action, the charter line, the human ACs, what changed vs the base branch, the
Recommended line, the page path), also printed last on stdout.

Where it builds: branch ticket/<id> checked out in place (the tree must be clean); the run stays
on ticket/<id> so the human sees what was built and the Gate 2 page in the folder (E16); ship
from kanban_ops merges and returns to base. With --parallel a worktree under .worktrees/<id>/
(kept in .git/info/exclude; ship removes it). The base branch is kanban_ops.base_branch (the
plan's `base:`, else main, else master; E21), the one place every base decision is made.

Refused before any branch is created or checked out (E23: a shipped ticket was rebuilt and
re-judged): a ticket whose status is done, or whose branch ticket/<id> is already merged into
the base (an ancestor with commits of its own). Both print the reason, write `done error
<reason>` and exit 2.

The project's .env (KEY=value lines) is loaded first; the environment already set wins (E15).

Build seat: /build streamed, with a shell allowlist and denied prompts. The session ends at the
close-out (status line committed): any later tool call is logged on the ticket as
`orbit after close-out` and the session is terminated; permission denials are fatal only when
the close-out was not reached. Traced to Opik per attempt: input = ticket + plan ACs, output =
commits + CI, metadata = attempt, cost, seconds per phase.

Verdict seat: the runner writes the packet (verdict_prep.py --arm --base), runs --verdict-cmd
over it (default: the packet on stdin as a tool-less, one-turn claude prompt; the reply is the
verdict), then closes out (render_verdict.py: validate for the arm, stamp meta, summarise,
render), commits traces/verdict/<id>.{input.md,json,summary.json,html} on the ticket branch,
records the Gate 2 decision as the Opik dataset item for the packet, and reads the decision.
The vendor in the stamp is the template's executable name. A non-zero exit, an empty result, or
JSON that fails the Verdict schema is an error: logged, traced with the CLI envelope and the
stderr tail, treated as a reject to retry. Three stops that never reach a retry, each `done
error <reason>` and exit 2: the packet refuses to build because no included file changed
against the base ("nothing to judge", E20: a verdict ran on an empty diff); the packet exceeds
PACKET_TOKEN_CAP (--packet-cap; ~bytes/4) and no model is called; the vendor report says
num_turns is not 1 (E28: the seat must be one turn, a multi-turn verdict cost ten times).
"opik: tracing to <url>" or "opik: untraced (<reason>)" is the first line printed and lands in
meta.opik.

--override backend=<x>|scrutiny=<y> restamps the ticket's plan before the run, writes the router
miss to the plan Log and traces/grill-misses.jsonl, and commits.

--plan <n> walks the plan's open tickets in depends_on order (kanban_ops.plan_order): one ticket
runs, the walk stops at Gate 2 and polls the ticket file every POLL_SECONDS; status done means
shipped (next ticket), in_progress means rejected (walk stopped, exit 2). A plan stamped
backend: session is refused unless --override backend=runner restamps it (E14, E18). Progress
lands in traces/runs/plan-<n>.state: `ticket <id>`, the ticket's phase lines, `gate2 <id>`, and
`done <summary>` (`done ship 2.1 2.2`, `done stopped 2.2 rejected`, `done refused backend: session`).

Exit codes: 0 ship (review the Gate 2 page, then say ship: kanban_ops merges) · 1 red baseline or retry cap ·
2 human gate (blocks all spawn child tickets, or same blocks as previous verdict) or a refusal
(done ticket, merged branch, nothing to judge, packet too big, num_turns not 1) ·
3 build reported NEEDS_CONTEXT (grill miss logged) or BLOCKED (see ticket Log) ·
4 permission denied before the close-out (never retried; see BUILD_ALLOWED_TOOLS and the
project's .claude/settings.json allowlist).
Run in a container when unattended (see Dockerfile).
"""
import argparse
import contextlib
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
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
DEFAULT_VERDICT_CMD = 'claude -p --model {model} --output-format json --tools "" --max-turns 1 < {packet}'
VERDICT_CMD_HELP = (
    "shell template for the one verdict call, run in the worktree. Placeholders: "
    "{packet} = packet path (traces/verdict/<id>.input.md), {output} = where the verdict JSON must land "
    "(traces/verdict/<id>.json), {model} = --verdict-model, {ticket} = ticket id. Paths are relative to "
    "the worktree and contain no spaces. The default feeds the packet on stdin as the prompt with no tools "
    "and one turn; never as an argument, a packet starts with ---. The model's reply is the verdict JSON, "
    "which the runner takes from the `result` field of claude's --output-format json and writes to {output}. "
    "A command that writes {output} itself is left alone. A non-zero exit, an empty result, or JSON that "
    "fails the Verdict schema is an error, treated as a reject to retry; a report whose num_turns is not 1 "
    "stops the run (exit 2). The stamp's vendor is the template's first word. When stdout is one JSON "
    "object with total_cost_usd and usage, they are stamped as meta.cost_usd and meta.tokens; other vendors "
    "leave them null. "
    f"Default: {DEFAULT_VERDICT_CMD}"
)
OPIK_ENV = ("OPIK_URL_OVERRIDE",)
POLL_SECONDS = 5.0  # --plan: how often the ticket file is read while the walk waits at Gate 2
HEARTBEAT_SECONDS = 30.0  # contract B: a heartbeat line in the state file while the build streams (E25)
PACKET_TOKEN_CAP = 40000  # contract F: the verdict packet's size in ~tokens (bytes // 4) beyond which no model is called (E28)


class Refusal(Exception):
    """A stop the machine decides on its own, never retried: `code` is the exit code, `state` the
    `done <state>` line, the message is what the runner prints."""

    def __init__(self, code: int, state: str, message: str):
        super().__init__(message)
        self.code, self.state = code, state


def packet_tokens(packet: Path) -> int:
    """The packet's size estimate the cap is checked against: bytes // 4."""
    return packet.stat().st_size // 4


def load_dotenv(root: Path) -> dict[str, str]:
    """Read root/.env — KEY=value lines, optional matching quotes, blanks and # comments skipped,
    no `export` — into os.environ for the keys not already set (E15: a runner started from a
    session had no OPIK env). Returns what was loaded."""
    path = root / ".env"
    loaded: dict[str, str] = {}
    if not path.is_file():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key.startswith("export "):  # `export KEY=value` is the same assignment
            key = key[len("export "):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def sh(args: list[str], cwd: Path) -> int:
    return subprocess.run(args, cwd=cwd).returncode


def git_exclude(repo: Path, pattern: str) -> None:
    """Add a pattern to .git/info/exclude once: ignored without touching the project's .gitignore."""
    exclude = repo / ".git" / "info" / "exclude"
    if exclude.parent.is_dir() and pattern not in (exclude.read_text() if exclude.exists() else "").splitlines():
        with exclude.open("a") as f:
            f.write(pattern + "\n")


def workspace(repo: Path, tid: str, parallel: bool) -> tuple[Path, bool]:
    """Where the ticket is built. Default: branch ticket/<id> checked out in place in the main
    checkout, and left there (E16). --parallel: a worktree under .worktrees/<id>/ (excluded via
    .git/info/exclude), kept until ship removes it. Returns (path, fresh). Refuses
    a dirty tree."""
    if not clean_tree(repo):
        raise SystemExit("[runner] the tree is dirty; commit or stash before running a ticket")
    git_exclude(repo, "traces/runs/")  # the state, log and result files: the builder's `git add -A` must not commit them
    git_exclude(repo, ".env")  # the secrets the runner loads: never on a ticket branch
    branch = f"ticket/{tid}"
    branch_exists = sh(["git", "rev-parse", "--verify", "-q", branch], repo) == 0
    if parallel:
        path = repo / ".worktrees" / tid
        git_exclude(repo, ".worktrees/")
        if path.exists():
            return path, False
        cmd = (["git", "worktree", "add", str(path), branch] if branch_exists
               else ["git", "worktree", "add", "-b", branch, str(path), "HEAD"])
        if sh(cmd, repo) != 0:
            raise SystemExit(f"[runner] worktree add failed for {tid}")
        return path, True
    if sh(["git", "checkout", "-q", branch] if branch_exists else ["git", "checkout", "-q", "-b", branch], repo) != 0:
        raise SystemExit(f"[runner] checkout of {branch} failed")
    return repo, not branch_exists


def ticket_base(cwd: Path, base: str) -> str:
    """The commit the ticket branch started from: the merge base of HEAD with the base branch
    (kanban_ops.base_branch, E21). Refuses when git cannot say: a guessed base is E21 again."""
    r = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"[runner] no merge base between {base} and HEAD")
    return r.stdout.strip()


def merged_into(repo: Path, branch: str, base: str) -> bool:
    """ticket/<id> already merged (E23): an ancestor of the base with commits of its own. A branch
    still at the base tip (created, nothing built) is not merged, just empty."""
    if sh(["git", "rev-parse", "--verify", "-q", branch], repo) != 0:
        return False
    tips = [subprocess.run(["git", "rev-parse", ref], cwd=repo, capture_output=True, text=True).stdout.strip() for ref in (branch, base)]
    return tips[0] != tips[1] and sh(["git", "merge-base", "--is-ancestor", branch, base], repo) == 0


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


def one_turn_error(report: dict | None) -> str | None:
    """Contract F: a vendor report that says num_turns and not 1 is not a verdict (E28: the seat
    must be one turn); None when the report carries no num_turns or says 1."""
    turns = (report or {}).get("num_turns")
    return None if turns in (None, 1) else f"invalid verdict: num_turns={turns} (the seat must be one turn)"


def call_error(returncode: int, output: Path, report: dict | None = None) -> str | None:
    if returncode != 0:
        return f"exit {returncode}"
    if (turns := one_turn_error(report)) is not None:
        return turns
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


BOARD_TICK = 10.0  # seconds between board renders while the build streams (the page refreshes every 5 s)
STREAM_WAIT = 1.0  # seconds the streaming loop waits for a builder line before it checks the clocks
LAST_LINE_CHARS = 80  # contract B: the heartbeat's builder line, at most this long


def build(cwd: Path, tid: str, model: str, phase=None, out=None, attempt: int = 1, tick=None, heartbeat=None) -> BuildCall:
    """Run one /build session, streamed. `phase(name)` is called with `tests-commit`,
    `feat-commit` and `build <attempt> close-out` as they appear; `tick()` every BOARD_TICK;
    `heartbeat(last_line, last_at)` every HEARTBEAT_SECONDS with the most recent line written
    under the phase (text or tool line, stripped, LAST_LINE_CHARS) and the clock it arrived at,
    also while the builder is silent (E25: a reader thread feeds a queue, so the clocks run
    between lines). On the first tool call after close-out the session is terminated and the
    call is recorded in `orbit`."""
    phase = phase or (lambda name: None)
    tick = tick or (lambda: None)
    heartbeat = heartbeat or (lambda last_line, last_at: None)
    out = out or sys.stdout
    t0 = time.monotonic()
    last_tick = last_beat = t0
    last_line, last_at = "", time.strftime("%H:%M:%S")
    proc = subprocess.Popen(build_cmd(tid, model), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    report: dict = {}
    orbit: list[str] = []
    seen: set[str] = set()
    done = False
    texts: list[str] = []

    def after_tool() -> None:
        nonlocal done
        subject = head_subject(cwd)
        for mark, prefix in (("tests-commit", f"test({tid})"), ("feat-commit", f"feat({tid})")):
            if subject.startswith(prefix) and mark not in seen:
                seen.add(mark)
                phase(mark)
        if not done and closed_out(cwd, tid):
            done = True
            phase(f"build {attempt} close-out")

    def under_phase(text: str) -> None:
        nonlocal last_line, last_at
        out.write(f"  {text}\n")
        last_line, last_at = text.strip()[:LAST_LINE_CHARS], time.strftime("%H:%M:%S")

    assert proc.stdout is not None
    lines: queue.Queue = queue.Queue()

    def pump() -> None:
        for raw in proc.stdout:
            lines.put(raw)
        lines.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    while True:
        try:
            raw = lines.get(timeout=STREAM_WAIT)
        except queue.Empty:
            raw = ""
        if raw is None:
            break
        now = time.monotonic()
        if now - last_tick > BOARD_TICK:  # the board shows the last builder lines while the build runs
            last_tick = now
            tick()
        if now - last_beat >= HEARTBEAT_SECONDS:
            last_beat = now
            heartbeat(last_line, last_at)
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            under_phase(line)
            continue
        kind = ev.get("type")
        if kind == "assistant":
            for block in (ev.get("message") or {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    texts.append(block["text"])
                    for t in block["text"].strip().splitlines():
                        under_phase(t)
                elif block.get("type") == "tool_use":
                    call = tool_line(block)
                    if done:
                        orbit.append(call)
                        out.write(f"  ! orbit after close-out: {call} — session terminated\n")
                        proc.terminate()
                        break
                    under_phase(call)
            if done and orbit:
                break
        elif kind == "user":
            after_tool()
        elif kind == "result":
            report = ev
    stderr = proc.stderr.read() if proc.stderr else ""  # the reader thread drains stdout until the process ends
    proc.wait()
    reader.join(timeout=STREAM_WAIT)
    for pipe in (proc.stdout, proc.stderr):  # the pipes outlive wait(); closing them keeps the runner's fd table flat over a plan walk
        if pipe:
            pipe.close()
    sys.stderr.write(stderr or "")
    if not done and closed_out(cwd, tid):
        done = True
        phase(f"build {attempt} close-out")
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
    report = vendor_report(r.stdout)
    if r.returncode == 0 and one_turn_error(report) is None and not output.exists() and (v := verdict_from_result(r.stdout)) is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(v, indent=1) + "\n", encoding="utf-8")
    error = call_error(r.returncode, output, report)
    return VendorCall(r.stdout, r.returncode, cost_usd, tokens, wall, started, error,
                      error_envelope(error, r.stdout, r.stderr) if error else None)


NOTHING_TO_JUDGE = "nothing to judge"  # verdict_prep's refusal on an empty included diff (contract G, E20)


def prep(cwd: Path, tid: str, arm: str, base: str | None = None) -> Path:
    """Write the packet through verdict_prep.py (--base from kanban_ops.base_branch, E21). Its
    "nothing to judge" refusal becomes a Refusal here, before any model call (E20)."""
    r = subprocess.run([sys.executable, str(SCRIPTS / "verdict_prep.py"), tid, "--arm", arm, *(["--base", base] if base else [])],
                       cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        if any(l.startswith(NOTHING_TO_JUDGE) for l in r.stderr.splitlines()):
            raise Refusal(2, f"error {NOTHING_TO_JUDGE}", f"[runner] {NOTHING_TO_JUDGE}")
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
            template: str = DEFAULT_VERDICT_CMD, summary_model: str = render_verdict.DEFAULT_SUMMARY_MODEL,
            base: str | None = None, packet_cap: int = PACKET_TOKEN_CAP) -> tuple[str, bool, bool]:
    """Returns (decision, retryable, progressed).
    retryable: an open block the builder can fix here (not spawn_child).
    progressed: at least one open block is new since the previous verdict; False means the
    builder and reviewer are stuck on the same findings — a plan problem, not a build problem.
    Raises Refusal (exit 2, never retried) when the packet has nothing to judge, exceeds
    `packet_cap` (~tokens, checked before the model call), or the vendor report's num_turns is
    not 1 (contracts F and G)."""
    p = cwd / "traces" / "verdict" / f"{tid}.json"
    prev = p.with_name(f"{tid}.prev.json")
    prev_open = set()
    if p.exists():
        p.replace(prev)
        prev_open = {f["id"] for f in schemas.Verdict.load(prev).open_blocks()}
    packet = prep(cwd, tid, arm, base)
    if (size := packet_tokens(packet)) > packet_cap:
        raise Refusal(2, "error packet too big", f"[runner] packet too big: ~{size} tokens (cap {packet_cap}); narrow the ticket or raise --packet-cap")
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
        if call.error.startswith("invalid verdict: num_turns="):  # E28: not a retry, the seat itself is wrong
            raise Refusal(2, f"error num_turns={(call.envelope or {}).get('num_turns')}", f"[runner] verdict error: {call.error}")
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


def state_path(repo: Path, tid: str) -> Path:
    return repo / "traces" / "runs" / f"{tid}.state"


def plan_state_path(repo: Path, n: str) -> Path:
    return repo / "traces" / "runs" / f"plan-{n}.state"


def state_line(path: Path, name: str, elapsed: int) -> None:
    """Append `<hh:mm:ss> <+m:ss> <name>` to a state file (the skill's loop and the board read
    these; a ticket is running iff its file exists and its last line does not start with `done `)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} +{elapsed // 60}:{elapsed % 60:02d} {name}\n")


def start_state(path: Path) -> None:
    """A new run: truncate the state file, then write the pid beside it (<stem>.pid; contract B:
    the skill's watch tails on the pid). Nothing removes the pid file at exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")  # the previous run's lines go
    path.with_suffix(".pid").write_text(f"{os.getpid()}\n", encoding="utf-8")


def refused_state(repo: Path, tid: str, reason: str, plan_state: Path | None = None) -> None:
    """A refusal before the machine starts: the state file says so (never a stale run), the
    plan's too when walking."""
    sp = state_path(repo, tid)
    start_state(sp)
    state_line(sp, f"done error {reason[:120]}", 0)
    if plan_state is not None:
        state_line(plan_state, f"done stopped {tid} {reason[:120]}", 0)


class Phases:
    """[runner hh:mm:ss +m:ss] <phase> lines, the same line into traces/runs/<id>.state (and the
    plan's state when walking), seconds per phase, and the board re-rendered into the main
    checkout after every line. `done(text)` writes the final `done <text>` line once.
    `heartbeat(last_line, last_at)` appends the running line of contract B without changing the
    phase (E25)."""

    def __init__(self, repo: Path, tree: Path, tid: str, out=None, plan_state: Path | None = None):
        self.repo, self.tree, self.tid, self.out = repo, tree, tid, out or sys.stdout
        self.t0 = time.monotonic()
        self.current: str | None = None
        self.started = self.t0
        self.seconds: dict[str, float] = {}
        self.state = state_path(repo, tid)
        self.plan_state = plan_state
        self.finished = False
        start_state(self.state)

    def elapsed(self) -> int:
        return int(time.monotonic() - self.t0)

    def lines(self, text: str, elapsed: int) -> None:
        """One line into the ticket's state file and the plan's when walking, then the board."""
        state_line(self.state, text, elapsed)
        if self.plan_state is not None:
            state_line(self.plan_state, text, elapsed)
        self.board()

    def mark(self, name: str) -> None:
        now = time.monotonic()
        if self.current:
            self.seconds[self.current] = round(self.seconds.get(self.current, 0.0) + now - self.started, 3)
        self.current, self.started = name, now
        elapsed = int(now - self.t0)
        self.out.write(f"[runner {time.strftime('%H:%M:%S')} +{elapsed // 60}:{elapsed % 60:02d}] {name}\n")
        self.out.flush()
        self.lines(name, elapsed)

    def heartbeat(self, last_line: str, last_at: str | None = None) -> None:
        """`<phase> · running · last <hh:mm:ss> · <last builder line>`: the build is alive; not a
        phase change (no seconds, no stdout line), but the board is re-rendered."""
        self.lines(f"{self.current or 'build'} · running · last {last_at or time.strftime('%H:%M:%S')} · {last_line[:LAST_LINE_CHARS] or '(no output yet)'}", self.elapsed())

    def done(self, text: str) -> None:
        """The run's last line, `done <text>`; a second call is ignored."""
        if not self.finished:
            self.finished = True
            self.mark(f"done {text}")

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
    """One trace per build <n>: input = ticket + plan ACs, output = commits + CI, metadata =
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


def read_verdict(cwd: Path, tid: str) -> "schemas.Verdict | None":
    """The verdict as stored when it passes the schema, else None (a vendor error left none)."""
    p = cwd / "traces" / "verdict" / f"{tid}.json"
    if not p.exists():
        return None
    try:
        v = schemas.Verdict.load(p)
    except (json.JSONDecodeError, ValueError):
        return None
    return None if v.problems() else v


def total_cost(*parts: "float | None") -> float | None:
    known = [c for c in parts if c is not None]
    return round(sum(known), 6) if known else None


def human_acs_of(tree: Path, tid: str) -> tuple:
    """The ticket's `(human)` ACs (schemas.Ticket.human_acs, E30); empty without the ticket."""
    path = kanban_ops.find_ticket(tree, tid)
    return tuple(getattr(schemas.Ticket.parse(_fm.read(path)[1]), "human_acs", ())) if path else ()


def write_result(tree: Path, repo: Path, tid: str, v: "schemas.Verdict", cost_usd: "float | None", seconds: float,
                 base: str | None = None, costs: dict | None = None) -> list[str]:
    """traces/runs/<id>.result: the end-of-run lines (contract C), from verdict.json, the
    summary cache, the packet's charter items (verdict_checks.charter_report), the ticket's
    human ACs and the included files changed vs the base (verdict_prep.changed_vs_base);
    returned for printing."""
    import verdict_checks  # here, not at the top: the packet side imports kanban_ops, and the runner stays importable without it
    import verdict_prep

    spath = render_verdict.summary_path(tree, tid)
    try:
        summary = json.loads(spath.read_text(encoding="utf-8")) if spath.exists() else None
    except json.JSONDecodeError:
        summary = None
    ppath = tree / "traces" / "verdict" / f"{tid}.input.md"
    packet = schemas.Packet.load(ppath) if ppath.exists() else None
    charter = verdict_checks.charter_report(v, packet) if packet is not None and hasattr(verdict_checks, "charter_report") else None
    changed = verdict_prep.changed_vs_base(tree, base) if base and hasattr(verdict_prep, "changed_vs_base") else None
    lines = render_verdict.result_lines(v, render_verdict.tickets_of(tree), cost_usd, seconds, f"traces/verdict/{tid}.html",
                                        summary=summary, changed=changed, charter=charter, human_acs=human_acs_of(tree, tid), costs=costs)
    path = repo / "traces" / "runs" / f"{tid}.result"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def refusal_before_git(repo: Path, tid: str, base: str) -> str | None:
    """Contract H (E23): the reason not to touch git at all — the ticket is done, or its branch is
    already merged into the base; None when the run may start."""
    if ticket_status(repo, tid) == "done":
        return f"{tid} is done; nothing to run"
    if merged_into(repo, f"ticket/{tid}", base):
        return f"ticket/{tid} is already merged into {base}"
    return None


def run_ticket(repo: Path, tid: str, a: argparse.Namespace, client, plan_state: Path | None = None) -> int:
    """One ticket through the machine. Every exit writes the `done ...` line (contract A) and,
    when a verdict was read, the result file (contract C), printed last. The refusals of
    contract H come first, before any branch exists."""
    try:
        base_name = kanban_ops.base_branch(repo, tid.split(".")[0])
    except ValueError as e:
        print(f"[runner] {e}")
        refused_state(repo, tid, str(e), plan_state)
        return 2
    if (reason := refusal_before_git(repo, tid, base_name)) is not None:
        print(f"[runner] {reason}")
        refused_state(repo, tid, reason, plan_state)
        return 2
    try:
        tree, fresh = workspace(repo, tid, a.parallel)
    except SystemExit as e:  # a refusal (dirty tree, checkout failed): the state file says so, never a stale run
        reason = str(e).strip().splitlines()[0] if str(e).strip() else "refused"
        refused_state(repo, tid, reason.removeprefix("[runner] "), plan_state)
        raise
    phases = Phases(repo, tree, tid, plan_state=plan_state)
    result: list[str] = []
    build_cost: float | None = None

    def finish(code: int, done: str, v: "schemas.Verdict | None" = None) -> int:
        nonlocal result
        if v is not None:
            spath = render_verdict.summary_path(tree, tid)
            summary_cost = json.loads(spath.read_text(encoding="utf-8")).get("cost_usd") if spath.exists() else None
            build_s = sum(s for name, s in {**phases.seconds, **({phases.current: 0.0} if phases.current else {})}.items() if name.startswith("build ") or name in ("tests-commit", "feat-commit"))
            costs = {"verdict_usd": v.meta.cost_usd if v.meta else None, "verdict_s": v.meta.seconds if v.meta else None,
                     "build_usd": build_cost, "build_s": build_s}  # E28: the verdict call and the build session named apart
            result = write_result(tree, repo, tid, v, total_cost(build_cost, v.meta.cost_usd if v.meta else None, summary_cost), phases.elapsed(), base_name, costs)
        phases.done(done)
        return code

    try:
        phases.mark("branch worktree" if a.parallel else "branch")
        print(f"  {tree} on ticket/{tid} ({'new' if fresh else 'existing'})")
        base = ticket_base(tree, base_name)
        if fresh:
            phases.mark("ci-pre")
            if not ci(tree):
                print("[runner] baseline red on a fresh branch — not this ticket's fault; fix main first")
                return finish(1, "error baseline red")
        for attempt in range(1, a.max_retries + 1):
            if already_built(tree, tid, base):
                phases.mark(f"build {attempt} skipped in_review with tests/feat/close-out commits")
                call = BuildCall(0, (), "", closed_out=True)
            else:
                phases.mark(f"build {attempt}")
                call = build(tree, tid, a.build_model, phase=phases.mark, attempt=attempt, tick=phases.board, heartbeat=phases.heartbeat)
                build_cost = total_cost(build_cost, call.cost_usd)
                if call.orbit:
                    log_orbit(tree, tid, call.orbit)
                if call.denials and not call.closed_out:
                    print(f"[runner] permission denied: {call.denied} — the build session cannot proceed headless; widen BUILD_ALLOWED_TOOLS or the project allowlist")
                    return finish(4, "error permission denied")
                if call.denials:
                    print(f"[runner] permission denied after close-out, ignored: {call.denied}")
                st = build_status(tree, tid)
                if st == "NEEDS_CONTEXT":
                    print("[runner] build needs context — grill miss logged; human needed")
                    log_grill_miss(tree, tid)
                    return finish(3, "error needs context")
                if st == "BLOCKED":
                    print("[runner] build blocked — see ticket Log; human needed")
                    return finish(3, "error blocked")
            phases.mark("ci")
            green = ci(tree)
            trace_build(client, tid=tid, cwd=tree, base=base, attempt=attempt, call=call, ci_green=green, seconds=phases.finish())
            if not green:
                print("[runner] ci red")
                continue
            phases.mark("verdict")
            decision, retryable, progressed = verdict(tree, tid, a.verdict_model, a.arm, a.verdict_cmd, a.summary_model,
                                                      base=base_name, packet_cap=getattr(a, "packet_cap", PACKET_TOKEN_CAP))
            phases.mark("close-out")
            print(f"[runner] verdict: {decision}{' (retryable)' if retryable else ''}")
            import verdict_eval  # here, not at the top: verdict_eval imports runner

            print(f"[runner] {verdict_eval.record_decision(tree, tid, decision)}")
            v = read_verdict(tree, tid)
            if decision == "ship":
                print(f"[runner] ship — review {tree / 'traces' / 'verdict' / (tid + '.html')}, then say ship")
                return finish(0, "ship", v)
            if not retryable:
                print("[runner] blocking findings all spawn child tickets — human: create children, decide on this slice")
                return finish(2, "reject", v)
            if not progressed:
                print("[runner] same blocks as previous verdict — build and verdict disagree; plan problem, human needed")
                return finish(2, "reject", v)
        print("[runner] retry cap reached — human needed")
        return finish(1, "error retry cap", read_verdict(tree, tid))
    except Refusal as e:  # contracts F and G: the machine stopped itself; no retry, no result block
        print(str(e))
        return finish(e.code, e.state)
    except BaseException as e:  # noqa: BLE001 — the state file must end in `done` whatever stopped the run
        reason = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        phases.done(f"error {reason[:120]}")
        raise
    finally:
        if not phases.finished:
            phases.done("error aborted")
        phases.finish()
        phases.board()
        for line in result:
            print(line)


def ticket_status(root: Path, tid: str) -> str:
    p = kanban_ops.find_ticket(root, tid)
    return str(_fm.read(p)[0].get("status", "")) if p else "missing"


def gate2_status(repo: Path, tid: str) -> str:
    """What Gate 2 did, read from git, never from the working tree (ship writes the file, commits,
    checks base out and merges in steps; the walk must not wake in between): "shipped" when the
    ticket branch is gone (ship deletes it last), "rejected" when the branch's committed ticket
    says in_progress, "missing" without a ticket file, else "waiting"."""
    branch = f"ticket/{tid}"
    if subprocess.run(["git", "rev-parse", "--verify", "-q", branch], cwd=repo, capture_output=True).returncode != 0:
        return "shipped"
    path = kanban_ops.find_ticket(repo, tid)
    if path is None:
        return "missing"
    committed = subprocess.run(["git", "show", f"{branch}:{path.relative_to(repo).as_posix()}"], cwd=repo, capture_output=True, text=True).stdout
    return "rejected" if _fm.parse(committed)[0].get("status") == "in_progress" else "waiting"


class Tee:
    """stdout during a plan walk: the plan's log (the process stdout) and traces/runs/<id>.log
    both, so the board's last builder lines exist for a walked ticket too."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s: str) -> int:
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self) -> None:
        for st in self.streams:
            st.flush()


def walk_plan(repo: Path, n: str, a: argparse.Namespace, client) -> int:
    """--plan <n>: the tickets in depends_on order, one run each; after a ship exit the walk
    waits at Gate 2 until the ticket file says done (shipped) and stops when it says
    in_progress (rejected). Refuses a plan stamped backend: session (contract F)."""
    state = plan_state_path(repo, n)
    start_state(state)  # plan-<n>.state and plan-<n>.pid
    t0 = time.monotonic()
    plan = kanban_ops.find_plan(repo, n)
    if plan is None:
        print(f"[runner] no plan {n} under kanban/")
        state_line(state, "done refused no plan", 0)
        return 2
    if schemas.RoutingStamp.parse(plan.read_text(encoding="utf-8")).backend == "session":
        print(f"[runner] plan {n} is stamped backend: session; pass --override backend=runner to run it")
        state_line(state, "done refused backend: session", 0)
        return 2
    shipped: list[str] = []
    while True:
        try:
            order = [t for t in kanban_ops.plan_order(repo, n) if t not in shipped]
        except ValueError as e:
            print(f"[runner] {e}")
            state_line(state, f"done error {e}", int(time.monotonic() - t0))
            return 2
        if not order:
            break
        tid = order[0]
        state_line(state, f"ticket {tid}", int(time.monotonic() - t0))
        log = repo / "traces" / "runs" / f"{tid}.log"
        try:
            with log.open("w", encoding="utf-8") as f, contextlib.redirect_stdout(Tee(sys.stdout, f)):
                rc = run_ticket(repo, tid, a, client, plan_state=state)
        except SystemExit as e:
            state_line(state, f"done stopped {tid} {str(e).strip().splitlines()[0] if str(e).strip() else 'exit'}"[:160], int(time.monotonic() - t0))
            raise
        if rc != 0:
            print(f"[runner] walk stopped: {tid} exit {rc}")
            state_line(state, f"done stopped {tid} exit {rc}", int(time.monotonic() - t0))
            return rc
        state_line(state, f"gate2 {tid}", int(time.monotonic() - t0))
        print(f"[runner] gate 2: {tid} awaits ship")
        while True:
            status = gate2_status(repo, tid)
            if status == "shipped":
                shipped.append(tid)
                break
            if status in ("rejected", "missing"):
                print(f"[runner] walk stopped: {tid} {status}")
                state_line(state, f"done stopped {tid} {status}", int(time.monotonic() - t0))
                return 2
            time.sleep(POLL_SECONDS)
    print(f"[runner] plan {n} walked: {' '.join(shipped) or 'nothing to do'}")
    state_line(state, f"done ship {' '.join(shipped)}".rstrip(), int(time.monotonic() - t0))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticket", nargs="?", help="the ticket to run; or give --plan <n>")
    ap.add_argument("--plan", metavar="N", help="walk plan N's open tickets in depends_on order, pausing at every Gate 2")
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
    ap.add_argument("--packet-cap", type=int, default=PACKET_TOKEN_CAP, metavar="TOKENS",
                    help=f"refuse the verdict call when the packet is over this many ~tokens (bytes/4); default {PACKET_TOKEN_CAP}")
    a = ap.parse_args()
    if bool(a.ticket) == bool(a.plan):
        ap.error("give a ticket id or --plan <n>, not both")
    repo = Path(a.cwd).resolve()
    load_dotenv(repo)
    client, opik_line = opik_status()
    print(f"[runner] opik: {opik_line}")

    if a.override:
        field, _, value = a.override.partition("=")
        n = a.plan or a.ticket.split(".")[0]
        old, new = kanban_ops.override_plan(repo, n, field.strip(), value.strip(), who="human")
        plan = kanban_ops.find_plan(repo, n)
        sha = kanban_ops.commit(repo, [str(plan.relative_to(repo)), "traces/grill-misses.jsonl"], f"docs(plan {n}): router miss — {field} {old} → {new}")
        print(f"[runner] override: plan {n} {field} {old} → {new} (committed {sha})")

    if a.plan:
        return walk_plan(repo, a.plan, a, client)
    return run_ticket(repo, a.ticket, a, client)


if __name__ == "__main__":
    sys.exit(main())
