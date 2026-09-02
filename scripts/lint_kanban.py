#!/usr/bin/env python3
"""Closed tickets are immutable. Fail when a ticket that was done|superseded at <base> differs
now outside its append-only sections. Run: python3 lint_kanban.py [base]  (default HEAD:
uncommitted changes; in CI pass the merge base, e.g. origin/main).

Compares the ticket at <base> with the working tree, so it catches edits made outside a Claude
session too. Loads through scripts/schemas.py (ClosedTicketDiff); no parsing of its own.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import schemas  # noqa: E402


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def lint(root: Path, base: str = "HEAD") -> list[str]:
    out = []
    changed = _git(root, "diff", "--name-only", base, "--", "kanban").split()
    for rel in changed:
        if not rel.endswith(".md") or rel.endswith(".plan.md"):
            continue
        try:
            old = _git(root, "show", f"{base}:{rel}")
        except subprocess.CalledProcessError:
            continue  # new file, nothing was closed
        fm, _ = _fm.parse(old)
        if "id" not in fm or fm.get("status") not in schemas.CLOSED_STATUSES:
            continue
        path = root / rel
        if not path.exists():
            out.append(f"{rel}: ticket {fm['id']} is {fm['status']} and was deleted")
            continue
        diff = schemas.ClosedTicketDiff.compare(old, path.read_text())
        for v in diff.violations:
            out.append(f"{rel}: ticket {fm['id']} is {fm['status']}; {v}")
    return out


def main(argv: list[str]) -> int:
    root = Path(".").resolve()
    base = argv[1] if len(argv) > 1 else "HEAD"
    violations = lint(root, base)
    for v in violations:
        print(v)
    print(f"lint_kanban: {len(violations)} violation(s) against {base}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
