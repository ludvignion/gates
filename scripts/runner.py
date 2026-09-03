#!/usr/bin/env python3
"""Headless state machine for one ticket, run from the main checkout.
Usage: python runner.py <ticket id> [--max-retries 2] [--cwd .] [--build-model sonnet] [--verdict-model opus]
                        [--arm blind|packet|repo] [--verdict-cmd "<template>"]
States: worktree → baseline → build → status → ci → verdict → (ship | block→retry | child→human)
Each state is a `claude -p` call or a shell command; transitions only on objective signals
(exit codes, build status line in the ticket Log, verdict.json decision and findings).
The machine, not the model, owns the loop.

Verdict seat: the runner writes the packet (verdict_prep.py --arm), runs --verdict-cmd over it
(default: the packet on stdin as a tool-less claude prompt; the reply is the verdict), then
closes out (render_verdict.py: validate for the arm, stamp meta, render). The vendor in the
stamp is the template's executable name. A non-zero exit, an empty result, or JSON that fails
the Verdict schema is an error: logged, traced with the CLI envelope (stop_reason, num_turns,
permission_denials, is_error) and the stderr tail, treated as a reject to retry. If the opik
package is importable and OPIK_URL_OVERRIDE is set, the one model call is one Opik trace:
input = packet, output = verdict, metadata = stamp + ticket + wall seconds. Otherwise nothing
changes.

Exit codes: 0 ship (human reviews and pushes from the worktree) · 1 red baseline or retry cap ·
2 human gate (blocks all spawn child tickets, or same blocks as previous verdict) ·
3 build reported NEEDS_CONTEXT (grill miss logged) or BLOCKED (see ticket Log) ·
4 permission denied: the headless build session was refused a tool (never retried; see
BUILD_ALLOWED_TOOLS and the project's .claude/settings.json allowlist).
The worktree at ../<repo>-<id> is never removed here; `git worktree remove` after the human merges.
Run in a container when unattended (see Dockerfile).
"""
import argparse
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
import render_verdict  # noqa: E402
import schemas  # noqa: E402

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


def worktree(repo: Path, tid: str) -> tuple[Path, bool]:
    """Idempotent: one worktree per ticket at ../<repo>-<tid> on branch ticket/<tid>.
    Returns (path, created). A pre-existing worktree means this is a retry; keep it."""
    path = repo.parent / f"{repo.name}-{tid}"
    if path.exists():
        return path, False
    branch = f"ticket/{tid}"
    branch_exists = sh(["git", "rev-parse", "--verify", "-q", branch], repo) == 0
    cmd = (
        ["git", "worktree", "add", str(path), branch]
        if branch_exists
        else ["git", "worktree", "add", "-b", branch, str(path), "HEAD"]
    )
    if sh(cmd, repo) != 0:
        raise SystemExit(f"[runner] worktree add failed for {tid}")
    return path, True


def ci(cwd: Path) -> bool:
    return sh(["make", "ci"], cwd) == 0


def build_status(cwd: Path, tid: str) -> str:
    """Last build status line in the ticket Log; DONE if the builder wrote none."""
    tickets = sorted((cwd / "kanban" / "tickets").glob(f"{tid}.*.md"))
    if not tickets:
        return "DONE"
    found = STATUS_RE.findall(tickets[0].read_text())
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


def vendor_report(stdout: str) -> dict | None:
    """stdout as one JSON object (claude --output-format json), else None."""
    try:
        d = json.loads(stdout.strip() or "null")
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def vendor_usage(stdout: str) -> tuple[float | None, dict | None]:
    """(cost_usd, tokens) from a vendor's report carrying total_cost_usd / usage; (None, None) for
    anything else."""
    d = vendor_report(stdout)
    if d is None:
        return None, None
    cost, usage = d.get("total_cost_usd"), d.get("usage")
    return (float(cost) if isinstance(cost, (int, float)) else None), (dict(usage) if isinstance(usage, dict) else None)


def verdict_from_result(stdout: str) -> dict | None:
    """The verdict JSON object inside the report's `result` text (the model's reply), fenced or
    bare; None when there is no report, no result, or no object with a `decision` in it."""
    d = vendor_report(stdout)
    text = d.get("result") if d else None
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "decision" in obj:
            return obj
    return None


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
# human sessions; the worktree carries that file, so both apply there.
BUILD_ALLOWED_TOOLS = (
    "Bash(make *)", "Bash(make)", "Bash(uv *)", "Bash(git *)", "Bash(pytest *)",
    "Bash(python3 -m pytest *)", "Bash(python -m pytest *)",
)


def build_cmd(tid: str, model: str) -> list[str]:
    return ["claude", "-p", f"/build {tid}", "--model", model, "--permission-mode", "acceptEdits",
            "--permission-prompts", "none", "--allowedTools", ",".join(BUILD_ALLOWED_TOOLS), "--output-format", "json"]


@dataclass(frozen=True)
class BuildCall:
    """One headless /build session: exit code, the CLI envelope's permission_denials (tool names
    with what they asked for), and the reply text."""

    returncode: int
    denials: tuple[dict, ...]
    result: str

    @property
    def denied(self) -> str:
        """One line naming what was refused; empty when nothing was."""
        parts = []
        for d in self.denials:
            name, command = d.get("tool_name", "?"), (d.get("tool_input") or {}).get("command", "")
            parts.append(f"{name}({command})" if command else str(name))
        return "; ".join(parts)


def build(cwd: Path, tid: str, model: str) -> BuildCall:
    r = subprocess.run(build_cmd(tid, model), cwd=cwd, capture_output=True, text=True)
    sys.stderr.write(r.stderr)
    report = vendor_report(r.stdout) or {}
    denials = report.get("permission_denials") or ()
    result = report.get("result") if isinstance(report.get("result"), str) else r.stdout
    if result:
        print(result.rstrip())
    return BuildCall(r.returncode, tuple(d for d in denials if isinstance(d, dict)), result or "")


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


def opik_client():
    """An Opik client when the package is importable and OPIK_URL_OVERRIDE (the SDK's own env
    name for the server) is set; else None and nothing is traced. OPIK_API_KEY alone is not a
    signal."""
    if not any(os.environ.get(k) for k in OPIK_ENV):
        return None
    try:
        import opik
    except ImportError:
        return None
    return opik.Opik()


def trace_verdict(client, *, tid: str, packet: Path, verdict: "schemas.Verdict | None", started: datetime, wall: float,
                  vendor: str, arm: str, error: dict | None = None) -> None:
    """One trace per model call, created whole after the call (the SDK batches; an end() right
    after create can lose data). Never raises into the state machine."""
    if client is None:
        return
    meta = verdict.meta.as_dict() if verdict and verdict.meta else {"arm": arm, "vendor": vendor}
    try:
        client.trace(name="verdict", start_time=started, end_time=started + timedelta(seconds=wall),
                     input={"packet": packet.read_text(encoding="utf-8")},
                     output=verdict.as_dict() if verdict else {"error": error or {"reason": "missing"}},
                     metadata={**meta, "ticket": tid, "wall_seconds": round(wall, 3)})
        client.flush()
    except Exception as e:  # tracing is observability, not a gate
        print(f"[runner] opik trace failed: {e}", file=sys.stderr)


def verdict(cwd: Path, tid: str, model: str, arm: str = schemas.DEFAULT_ARM,
            template: str = DEFAULT_VERDICT_CMD) -> tuple[str, bool, bool]:
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
    client = opik_client()
    call = call_vendor(cmd, cwd, p)
    if vendor_report(call.stdout) is None:
        sys.stdout.write(call.stdout)  # not a report: pass the vendor's output through
    else:
        print(f"[runner] verdict call: cost_usd={call.cost_usd} tokens={call.tokens}")
    if call.error:
        print(f"[runner] verdict error: {call.error}")
        trace_verdict(client, tid=tid, packet=packet, verdict=None, started=call.started, wall=call.wall,
                      vendor=vendor_of(template), arm=arm, error=call.envelope)
        return "reject", True, True
    violations = render_verdict.main(cwd, tid, vendor=vendor_of(template), cost_usd=call.cost_usd, tokens=call.tokens)
    for x in violations:
        print(f"[runner] verdict invalid: {x}")
    v = schemas.Verdict.load(p)
    trace_verdict(client, tid=tid, packet=packet, verdict=v, started=call.started, wall=call.wall, vendor=vendor_of(template), arm=arm)
    blocks = v.open_blocks()
    retryable = any(not f.get("spawn_child", False) for f in blocks)
    progressed = not blocks or any(f.get("id") not in prev_open for f in blocks)
    return v.decision, retryable, progressed


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
    a = ap.parse_args()
    repo = Path(a.cwd).resolve()

    wt, created = worktree(repo, a.ticket)
    print(f"[runner] worktree {wt} ({'new' if created else 'existing'})")
    if created and not ci(wt):
        print("[runner] baseline red in a fresh worktree — not this ticket's fault; fix main first")
        return 1

    for attempt in range(1, a.max_retries + 1):
        print(f"[runner] {a.ticket} attempt {attempt}")
        call = build(wt, a.ticket, a.build_model)
        if call.denials:
            print(f"[runner] permission denied: {call.denied} — the build session cannot proceed headless; widen BUILD_ALLOWED_TOOLS or the project allowlist")
            return 4
        st = build_status(wt, a.ticket)
        if st == "NEEDS_CONTEXT":
            print("[runner] build needs context — grill miss logged; human needed")
            log_grill_miss(wt, a.ticket)
            return 3
        if st == "BLOCKED":
            print("[runner] build blocked — see ticket Log; human needed")
            return 3
        if not ci(wt):
            print("[runner] ci red")
            continue
        decision, retryable, progressed = verdict(wt, a.ticket, a.verdict_model, a.arm, a.verdict_cmd)
        print(f"[runner] verdict: {decision}{' (retryable)' if retryable else ''}")
        if decision == "ship":
            print(f"[runner] ship — review and push from {wt}")
            return 0
        if not retryable:
            print("[runner] blocking findings all spawn child tickets — human: create children, decide on this slice")
            return 2
        if not progressed:
            print("[runner] same blocks as previous verdict — build and verdict disagree; plan problem, human needed")
            return 2
    print("[runner] retry cap reached — human needed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
