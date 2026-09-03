#!/usr/bin/env python3
"""Mechanical verdict findings, zero model tokens. Run: verdict_checks.py <id> [--base main]

Checks (ids C1..): files written outside the ticket's `writes:` (block), CI red (block), ticket
ACs that no test added on this branch names (warn), new src defs with one caller or none
(warn, Phase B q4), new public names absent from docs/glossary.md when the diff does not touch
it (warn), closed tickets edited (warn). Findings use the verdict JSON shape so the model copies
them verbatim. Loads ticket shape through scripts/schemas.py; git for everything else.

Also the check on the written verdict, `validate(verdict, arm)`: a block without an `ac` or
`charter` citation is a violation — except under the blind arm, which cannot cite by design, so
there the block is downgraded to a warn instead.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import lint_kanban  # noqa: E402
import schemas  # noqa: E402

ALWAYS_ALLOWED = ("kanban/", "traces/", "docs/")
AC_RE = re.compile(r"\bAC-\d+\b")
DEF_RE = re.compile(r"^\+\s*(?:def|class)\s+([A-Za-z_]\w*)")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True).stdout


def default_base(root: Path) -> str:
    for b in ("main", "master"):
        if subprocess.run(["git", "rev-parse", "--verify", "-q", b], cwd=root, capture_output=True).returncode == 0:
            return b
    return "HEAD~1"


def find_ticket(root: Path, tid: str) -> Path:
    m = [p for p in (root / "kanban").rglob(f"{tid}.*.md") if not p.name.endswith(".plan.md")]
    if not m:
        raise SystemExit(f"no ticket {tid} under kanban/")
    return m[0]


def finding(cid: str, severity: str, text: str, ac: str | None = None, repro: str | None = None) -> dict:
    return {"id": cid, "severity": severity, "status": "open", "ac": ac, "charter": None, "home": None,
            "spawn_child": False, "covered_by": None, "repro": repro, "waived_by": None, "text": text}


def checks(root: Path, tid: str, base: str, ci_green: bool | None = None) -> list[dict]:
    fm, body = _fm.read(find_ticket(root, tid))
    ticket = schemas.Ticket.parse(body)
    rng = f"{base}...HEAD"
    files = git(root, "diff", "--name-only", rng).split()
    diff = git(root, "diff", rng)
    out: list[dict] = []
    n = 0

    def add(severity, text, ac=None, repro=None):
        nonlocal n
        n += 1
        out.append(finding(f"C{n}", severity, text, ac, repro))

    writes = fm.get("writes") or []
    if writes:
        outside = [f for f in files if not f.startswith(ALWAYS_ALLOWED)
                   and not any(f.startswith(w.rstrip("/")) for w in writes)]
        if outside:
            add("block", f"Written outside writes: {', '.join(outside[:6])}", ac="writes:",
                repro=f"git diff --name-only {rng}")
    if ci_green is False:
        add("block", "make ci is red", ac="ci", repro="make ci")

    added_test_lines = "\n".join(
        l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")
    ) if any("test" in f for f in files) else ""
    tested = set(AC_RE.findall(added_test_lines))
    ac_ids = [m.group(0) for it in ticket.acs if (m := schemas.AC_ID_RE.match(it))]
    untested = [a for a in ac_ids if a not in tested]
    if untested and ac_ids:
        add("warn", f"No test added on this branch names {', '.join(untested)}", ac=untested[0])

    src_py = list((root / "src").rglob("*.py")) if (root / "src").is_dir() else []
    if src_py:
        corpus = "\n".join(p.read_text(errors="ignore") for p in src_py)
        new_defs: list[str] = []
        cur = None
        for l in diff.splitlines():
            if l.startswith("+++ b/"):
                cur = l[6:]
            elif cur and cur.startswith("src/") and (m := DEF_RE.match(l)):
                new_defs.append(m.group(1))
        lonely = [d for d in new_defs if not d.startswith("_" * 2) and len(re.findall(rf"\b{re.escape(d)}\(", corpus)) <= 2]
        if lonely:
            add("warn", f"New defs with one caller or none: {', '.join(lonely[:8])} (Phase B q4)")
        glossary = root / "docs" / "glossary.md"
        if glossary.exists() and "docs/glossary.md" not in files:
            g = glossary.read_text(errors="ignore")
            missing = [d for d in new_defs if d[0].isupper() and d not in g]
            if missing:
                add("warn", f"New names absent from docs/glossary.md: {', '.join(missing[:8])}")

    for v in lint_kanban.lint(root, base):
        add("warn", v)

    attempts = live_calls(root)
    if attempts:
        hosts = sorted({a.split("\n")[0] for a in attempts})
        add("block", f"Tests attempt {len(attempts)} live network call(s) with keys set: {', '.join(hosts)[:120]}",
            ac="no live calls", repro="verdict_checks.py <id>  (pytest -p nosock with .env.example keys set)")
    return out


def validate(v: schemas.Verdict, arm: str) -> tuple[schemas.Verdict, list[str]]:
    """The verdict as it should be stored, and the violations found. Blind cannot cite, so its
    uncited blocks become warns (text unchanged); under any other arm they are violations and
    the verdict is returned as written."""
    uncited = v.uncited_blocks()
    if not uncited:
        return v, []
    if arm != "blind":
        return v, [f"{f.get('id')}: block without an ac or charter citation ({arm} seat)" for f in uncited]
    ids = {id(f) for f in uncited}
    v = v.with_findings(tuple({**f, "severity": "warn"} if id(f) in ids else f for f in v.findings))
    # the prompt's rule, applied mechanically after the downgrade: reject iff an open block or CI red
    return v.with_decision("reject" if v.open_blocks() or v.ci.get("green") is False else "ship"), []


def live_calls(root: Path) -> list[str]:
    """Run the test suite with every .env.example key set to a dummy value and sockets blocked.
    Returns one entry per attempted connection. Empty when no tests dir or no pytest."""
    if not (root / "tests").is_dir():
        return []
    env = dict(os.environ)
    example = root / ".env.example"
    if example.exists():
        for line in example.read_text(errors="ignore").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                env[line.split("=", 1)[0].strip()] = "dummy-from-verdict-checks"
    log = root / "traces" / "verdict" / ".nosock.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.unlink(missing_ok=True)
    env["NOSOCK_LOG"] = str(log)
    env["PYTHONPATH"] = str(Path(__file__).parent) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTEST_ADDOPTS"] = (env.get("PYTEST_ADDOPTS", "") + " -p nosock -q").strip()
    runner = ["uv", "run", "pytest"] if (root / "uv.lock").exists() else [sys.executable, "-m", "pytest"]
    try:
        subprocess.run(runner + ["tests", "--ignore=tests/evals"], cwd=root, env=env, capture_output=True, timeout=900)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if not log.exists():
        return []
    entries = [e.strip() for e in log.read_text(errors="ignore").split("----") if e.strip()]
    log.unlink(missing_ok=True)
    return entries


def main(argv: list[str]) -> int:
    tid = argv[1]
    root = Path(".").resolve()
    base = argv[argv.index("--base") + 1] if "--base" in argv else default_base(root)
    print(json.dumps(checks(root, tid, base), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
