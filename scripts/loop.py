#!/usr/bin/env python3
"""Headless state machine for one ticket, run from the main checkout.
Usage: python loop.py <ticket id> [--max-retries 2] [--cwd .]
States: worktree → baseline → build → ci → verdict → (ship | critical→retry | non-critical→human)
Each state is a `claude -p` call or a shell command; transitions only on objective signals
(exit codes, verdict.json decision and finding severities). The machine, not the model, owns the loop.
Exit codes: 0 ship (human reviews and pushes from the worktree) · 1 red baseline or retry cap ·
2 human gate (non-critical findings only: accept, or reject to a child ticket).
The worktree at ../<repo>-<id> is never removed here; `git worktree remove` after the human merges.
Run in a container when unattended (see Dockerfile).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

CRITICAL = "critical"


def sh(args: list[str], cwd: Path) -> int:
    return subprocess.run(args, cwd=cwd).returncode


def claude(prompt: str, cwd: Path) -> int:
    return sh(["claude", "-p", prompt, "--permission-mode", "acceptEdits"], cwd)


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


def verdict(cwd: Path, tid: str) -> tuple[str, set[str]]:
    """Returns (decision, severities). A missing severity defaults to critical, so a verdict
    skill that predates the field keeps the old always-retry behaviour."""
    p = cwd / "traces" / "verdict" / f"{tid}.json"
    p.unlink(missing_ok=True)  # never reread a stale verdict from a previous attempt
    claude(f"/verdict {tid}", cwd)
    if not p.exists():
        return "reject", {CRITICAL}  # no verdict written is a loop failure, treated as critical
    v = json.loads(p.read_text())
    return v["decision"], {f.get("severity", CRITICAL) for f in v.get("findings", [])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticket")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--cwd", default=".")
    a = ap.parse_args()
    repo = Path(a.cwd).resolve()

    wt, created = worktree(repo, a.ticket)
    print(f"[loop] worktree {wt} ({'new' if created else 'existing'})")
    if created and not ci(wt):
        print("[loop] baseline red in a fresh worktree — not this ticket's fault; fix main first")
        return 1

    for attempt in range(1, a.max_retries + 1):
        print(f"[loop] {a.ticket} attempt {attempt}")
        claude(f"/build {a.ticket}", wt)
        if not ci(wt):
            print("[loop] ci red")
            continue
        decision, severities = verdict(wt, a.ticket)
        print(f"[loop] verdict: {decision} {sorted(severities)}")
        if decision == "ship":
            print(f"[loop] ship — review and push from {wt}")
            return 0
        if CRITICAL not in severities:
            print("[loop] non-critical findings only — human gate: accept, or reject to a child ticket")
            return 2
    print("[loop] retry cap reached — human needed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
