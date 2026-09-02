#!/usr/bin/env python3
"""One prepared input for a single-call verdict. Run: verdict_prep.py <id> [--base main] [--no-ci]

Writes traces/verdict/<id>.input.md and prints its path. Contents: the ticket's ACs, Out of
scope, writes: and human waivers; the plan's "Verdict must attack" items; the charter items;
the previous verdict's open blocks with their repro commands; `make ci` result; the mechanical
findings from verdict_checks.py; tests added on the branch that name ACs; the diff. The model
reads this file and nothing else. Shapes load through scripts/schemas.py.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import schemas  # noqa: E402
import verdict_checks as vc  # noqa: E402

DIFF_CAP = 4000  # lines; beyond this the diff is truncated and says so


def run_ci(root: Path) -> tuple[bool | None, str]:
    if not (root / "Makefile").exists() or "ci:" not in (root / "Makefile").read_text(errors="ignore"):
        return None, "no `make ci` target"
    r = subprocess.run(["make", "-s", "ci"], cwd=root, capture_output=True, text=True, timeout=900)
    tail = "\n".join((r.stdout + r.stderr).splitlines()[-25:])
    return r.returncode == 0, tail


def build(root: Path, tid: str, base: str, ci: bool) -> str:
    tpath = vc.find_ticket(root, tid)
    fm, body = _fm.read(tpath)
    ticket = schemas.Ticket.parse(body)
    plan_path = next((root / "kanban").rglob(f"{fm.get('parent', tid.split('.')[0])}.plan.md"), None)
    plan_body = _fm.read(plan_path)[1] if plan_path else ""
    attacks = schemas.Attacks.parse(plan_body).items
    charter_path = root / "docs" / "domain-pack" / "charter.md"
    charter = schemas.Charter.parse(charter_path.read_text(errors="ignore")).items if charter_path.exists() else ()
    prev_path = root / "traces" / "verdict" / f"{tid}.prev.json"
    prev_blocks = []
    if prev_path.exists():
        prev = json.loads(prev_path.read_text())
        prev_blocks = [f for f in prev.get("findings", []) if f.get("severity") == "block"]
    ci_green, ci_tail = run_ci(root) if ci else (None, "skipped (--no-ci)")
    found = vc.checks(root, tid, base, ci_green)
    rng = f"{base}...HEAD"
    diff = vc.git(root, "diff", rng)
    lines = diff.splitlines()
    if len(lines) > DIFF_CAP:
        diff = "\n".join(lines[:DIFF_CAP]) + f"\n... truncated: {len(lines) - DIFF_CAP} more lines; run `git diff {rng} -- <file>` for a file\n"
    stat = vc.git(root, "diff", "--stat", rng)
    test_lines = [
        f"{f}: {l[1:].strip()[:120]}"
        for f, l in _added_lines(diff) if "test" in f and schemas.AC_ID_RE.search(l)
    ]
    sec = lambda title, items: f"## {title}\n" + ("\n".join(f"- {i}" for i in items) if items else "- none") + "\n\n"
    out = f"# Verdict input — ticket {tid} (branch vs {base})\n\n"
    out += f"status: {fm.get('status')} · writes: {fm.get('writes') or []} · ticket file: {tpath.relative_to(root)}\n\n"
    out += sec("Acceptance criteria", ticket.acs)
    out += sec("Out of scope", ticket.out_of_scope)
    out += sec("Human waivers (Log)", ticket.waivers)
    out += sec("Plan: verdict must attack", attacks)
    out += sec("Charter items", [f"charter-{n}: {t}" for n, t in charter])
    out += sec("Previous verdict: blocks to re-run", [f"{b['id']} {b.get('status')} ({b.get('ac') or b.get('charter')}): {b['text']} — repro: `{b.get('repro')}`" for b in prev_blocks])
    out += f"## CI\n{'green' if ci_green else 'RED' if ci_green is False else 'not run'}\n```\n{ci_tail}\n```\n\n"
    out += "## Mechanical findings (copy verbatim into findings)\n```json\n" + json.dumps(found, indent=1) + "\n```\n\n"
    out += sec("Tests added on this branch that name an AC", test_lines[:60])
    out += f"## Diff stat\n```\n{stat}```\n\n## Diff\n```diff\n{diff}\n```\n"
    return out


def _added_lines(diff: str):
    cur = None
    for l in diff.splitlines():
        if l.startswith("+++ b/"):
            cur = l[6:]
        elif cur and l.startswith("+") and not l.startswith("+++"):
            yield cur, l


def main(argv: list[str]) -> int:
    tid = argv[1]
    root = Path(".").resolve()
    base = argv[argv.index("--base") + 1] if "--base" in argv else vc.default_base(root)
    text = build(root, tid, base, ci="--no-ci" not in argv)
    out = root / "traces" / "verdict" / f"{tid}.input.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(out.relative_to(root))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
