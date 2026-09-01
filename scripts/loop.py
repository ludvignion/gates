#!/usr/bin/env python3
"""Headless state machine for one ticket, run from the main checkout.
Usage: python loop.py <ticket id> [--max-retries 2] [--cwd .] [--build-model sonnet] [--verdict-model opus]
States: worktree → baseline → build → status → ci → verdict → (ship | block→retry | child→human)
Each state is a `claude -p` call or a shell command; transitions only on objective signals
(exit codes, build status line in the ticket Log, verdict.json decision and findings).
The machine, not the model, owns the loop.
Exit codes: 0 ship (human reviews and pushes from the worktree) · 1 red baseline or retry cap ·
2 human gate (all blocking findings spawn child tickets) ·
3 build reported NEEDS_CONTEXT (grill miss logged) or BLOCKED (see ticket Log).
The worktree at ../<repo>-<id> is never removed here; `git worktree remove` after the human merges.
Run in a container when unattended (see Dockerfile).
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

STATUS_RE = re.compile(r"^### \[build\] .*— status: (NEEDS_CONTEXT|BLOCKED|DONE)\b", re.M)


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
        raise SystemExit(f"[loop] worktree add failed for {tid}")
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


def verdict(cwd: Path, tid: str, model: str) -> tuple[str, bool]:
    """Returns (decision, retryable). Retryable = at least one block finding the builder can fix
    in this slice, i.e. not spawn_child. A missing verdict is a loop failure: reject, retryable."""
    p = cwd / "traces" / "verdict" / f"{tid}.json"
    p.unlink(missing_ok=True)  # never reread a stale verdict from a previous attempt
    claude(f"/verdict {tid}", cwd, model)
    if not p.exists():
        return "reject", True
    v = json.loads(p.read_text())
    retryable = any(
        f.get("severity") == "block" and not f.get("spawn_child", False)
        for f in v.get("findings", [])
    )
    return v["decision"], retryable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticket")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--build-model", default="sonnet")
    ap.add_argument("--verdict-model", default="opus")
    a = ap.parse_args()
    repo = Path(a.cwd).resolve()

    wt, created = worktree(repo, a.ticket)
    print(f"[loop] worktree {wt} ({'new' if created else 'existing'})")
    if created and not ci(wt):
        print("[loop] baseline red in a fresh worktree — not this ticket's fault; fix main first")
        return 1

    for attempt in range(1, a.max_retries + 1):
        print(f"[loop] {a.ticket} attempt {attempt}")
        claude(f"/build {a.ticket}", wt, a.build_model)
        st = build_status(wt, a.ticket)
        if st == "NEEDS_CONTEXT":
            print("[loop] build needs context — grill miss logged; human needed")
            log_grill_miss(wt, a.ticket)
            return 3
        if st == "BLOCKED":
            print("[loop] build blocked — see ticket Log; human needed")
            return 3
        if not ci(wt):
            print("[loop] ci red")
            continue
        decision, retryable = verdict(wt, a.ticket, a.verdict_model)
        print(f"[loop] verdict: {decision}{' (retryable)' if retryable else ''}")
        if decision == "ship":
            print(f"[loop] ship — review and push from {wt}")
            return 0
        if not retryable:
            print("[loop] blocking findings all spawn child tickets — human: create children, decide on this slice")
            return 2
    print("[loop] retry cap reached — human needed")
    return 1


if __name__ == "__main__":
    sys.exit(main())