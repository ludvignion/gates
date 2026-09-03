#!/usr/bin/env python3
"""Headless state machine for one ticket, run from the main checkout.
Usage: python runner.py <ticket id> [--max-retries 2] [--cwd .] [--build-model sonnet] [--verdict-model opus]
                        [--arm blind|packet|repo] [--verdict-cmd "<template>"]
States: worktree → baseline → build → status → ci → verdict → (ship | block→retry | child→human)
Each state is a `claude -p` call or a shell command; transitions only on objective signals
(exit codes, build status line in the ticket Log, verdict.json decision and findings).
The machine, not the model, owns the loop.

Verdict seat: the runner writes the packet (verdict_prep.py --arm), runs --verdict-cmd over it,
then closes out (render_verdict.py: validate for the arm, stamp meta, render). The vendor in the
stamp is the template's executable name. If the opik package is importable and OPIK_URL_OVERRIDE
or OPIK_API_KEY is set, the one model call is one Opik trace: input = packet, output = verdict,
metadata = stamp + ticket + wall seconds. Otherwise nothing changes.

Exit codes: 0 ship (human reviews and pushes from the worktree) · 1 red baseline or retry cap ·
2 human gate (blocks all spawn child tickets, or same blocks as previous verdict) ·
3 build reported NEEDS_CONTEXT (grill miss logged) or BLOCKED (see ticket Log).
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import render_verdict  # noqa: E402
import schemas  # noqa: E402

STATUS_RE = re.compile(r"^### \[build\] .*— status: (NEEDS_CONTEXT|BLOCKED|DONE)\b", re.M)
SCRIPTS = Path(__file__).resolve().parent
DEFAULT_VERDICT_MODEL = "opus"
DEFAULT_VERDICT_CMD = "claude -p '/verdict {packet}' --model {model} --permission-mode acceptEdits"
VERDICT_CMD_HELP = (
    "shell template for the one verdict call, run in the worktree. Placeholders: "
    "{packet} = packet path (traces/verdict/<id>.input.md), {output} = where the verdict JSON must land "
    "(traces/verdict/<id>.json), {model} = --verdict-model, {ticket} = ticket id. Paths are relative to "
    "the worktree and contain no spaces. The stamp's vendor is the template's first word. "
    f"Default: {DEFAULT_VERDICT_CMD}"
)
OPIK_ENV = ("OPIK_URL_OVERRIDE", "OPIK_API_KEY")


def sh(args: list[str], cwd: Path) -> int:
    return subprocess.run(args, cwd=cwd).returncode


def claude(prompt: str, cwd: Path, model: str) -> int:
    return sh(["claude", "-p", prompt, "--model", model, "--permission-mode", "acceptEdits"], cwd)


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


def prep(cwd: Path, tid: str, arm: str) -> Path:
    r = subprocess.run([sys.executable, str(SCRIPTS / "verdict_prep.py"), tid, "--arm", arm],
                       cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"[runner] verdict_prep failed: {r.stderr.strip()[-400:]}")
    return cwd / r.stdout.strip().splitlines()[-1]


def opik_client():
    """An Opik client when the package is importable and the SDK's own env names a server
    (OPIK_URL_OVERRIDE, or OPIK_API_KEY for the cloud); else None and nothing is traced."""
    if not any(os.environ.get(k) for k in OPIK_ENV):
        return None
    try:
        import opik
    except ImportError:
        return None
    return opik.Opik()


def trace_verdict(client, *, tid: str, packet: Path, verdict: "schemas.Verdict | None", started: datetime, wall: float,
                  vendor: str, arm: str) -> None:
    """One trace per model call, created whole after the call (the SDK batches; an end() right
    after create can lose data). Never raises into the state machine."""
    if client is None:
        return
    meta = verdict.meta.as_dict() if verdict and verdict.meta else {"arm": arm, "vendor": vendor}
    try:
        client.trace(name="verdict", start_time=started, end_time=started + timedelta(seconds=wall),
                     input={"packet": packet.read_text(encoding="utf-8")},
                     output=verdict.as_dict() if verdict else {"missing": True},
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
    started, t0 = datetime.now(timezone.utc), time.monotonic()
    subprocess.run(cmd, shell=True, cwd=cwd)
    wall = time.monotonic() - t0
    if not p.exists():
        trace_verdict(client, tid=tid, packet=packet, verdict=None, started=started, wall=wall, vendor=vendor_of(template), arm=arm)
        return "reject", True, True
    violations = render_verdict.main(cwd, tid, vendor=vendor_of(template))
    for x in violations:
        print(f"[runner] verdict invalid: {x}")
    v = schemas.Verdict.load(p)
    trace_verdict(client, tid=tid, packet=packet, verdict=v, started=started, wall=wall, vendor=vendor_of(template), arm=arm)
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
        claude(f"/build {a.ticket}", wt, a.build_model)
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
