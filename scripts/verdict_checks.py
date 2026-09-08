#!/usr/bin/env python3
"""Mechanical verdict findings, zero model tokens. Run: verdict_checks.py <id> [--base main]

Checks (ids C1..): files written outside the ticket's `writes:` (block), CI red (block), each
ticket AC that no test added on this branch names (block, one per AC), new src defs with one caller or none
(warn, Phase B q4; files new in the diff are exempt), new public names absent from docs/glossary.md when the diff does not touch
it (warn), closed tickets edited (warn). Findings use the verdict JSON shape so the model copies
them verbatim. Loads ticket shape through scripts/schemas.py; git for everything else.

Also the check on the written verdict, `validate(verdict, arm, packet)`: a block without an `ac`
or `charter` citation is a violation — except under the blind arm, which cannot cite by design, so
there the block is downgraded to a warn instead — and every AC / charter item the packet showed
must be in `held` or cited by a finding, else a C-warn "unaccounted: <id>" is appended. The packet
lists only the charter items the diff reaches, so an unreachable item never becomes a warn (E22);
`charter_report` folds the reachable items, the held ones and the findings citing the rest into
the result block's charter line. Human ACs never enter `ticket.acs`, so "no test names AC-n"
never fires on them (E30).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import kanban_ops  # noqa: E402
import lint_kanban  # noqa: E402
import schemas  # noqa: E402

ALWAYS_ALLOWED = ("kanban/", "traces/", "docs/")  # prefixes a write never blocks on (C1)
ALWAYS_WRITABLE = ("docs/glossary.md", "kanban/plans/{n}.plan.md (Log)")  # the same scope as the packet names it to the reviewer (always_writable)
AC_RE = re.compile(r"\bAC-\d+\b")
DEF_RE = re.compile(r"^\+\s*(?:def|class)\s+([A-Za-z_]\w*)")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True).stdout


def default_base(root: Path, plan_n: str | None = None) -> str:
    """The plan's base branch through kanban_ops.base_branch (E21: never the current branch)."""
    return kanban_ops.base_branch(root, plan_n)


def find_ticket(root: Path, tid: str) -> Path:
    p = kanban_ops.find_ticket(root, tid)
    if p is None:
        raise SystemExit(f"no ticket {tid} under kanban/")
    return p


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
    for ac in ac_ids:
        if ac not in tested:
            add("block", f"no test names {ac}", ac=ac)

    src_py = list((root / "src").rglob("*.py")) if (root / "src").is_dir() else []
    if src_py:
        corpus = "\n".join(p.read_text(errors="ignore") for p in src_py)
        new_defs: list[str] = []
        in_new_file: list[str] = []  # defs in files new in the diff: a new module is all "lonely" by construction
        cur, new_file = None, False
        for l in diff.splitlines():
            if l.startswith("--- "):
                new_file = l.startswith("--- /dev/null")
            elif l.startswith("+++ b/"):
                cur = l[6:]
            elif cur and cur.startswith("src/") and (m := DEF_RE.match(l)):
                new_defs.append(m.group(1))
                if new_file:
                    in_new_file.append(m.group(1))
        lonely = [d for d in new_defs if d not in in_new_file and not d.startswith("_" * 2)
                  and len(re.findall(rf"\b{re.escape(d)}\(", corpus)) <= 2]
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

    for e in schemas.Log.parse(body).entries:
        if e.role == "runner" and e.head.startswith("orbit after close-out"):
            add("warn", e.head)

    attempts = live_calls(root)
    if attempts:
        hosts = sorted({a.split("\n")[0] for a in attempts})
        add("block", f"Tests attempt {len(attempts)} live network call(s) with keys set: {', '.join(hosts)[:120]}",
            ac="no live calls", repro="verdict_checks.py <id>  (pytest -p nosock with .env.example keys set)")
    return out


_CHARTER_ID_RE = re.compile(r"\bcharter-\d+\b")


def packet_items(packet: "schemas.Packet | None") -> list[str]:
    """Every AC id and charter id the packet put in front of the reviewer."""
    if packet is None:
        return []
    acs = [m.group(0) for it in schemas._items(packet.section("Acceptance criteria")) if (m := schemas.AC_ID_RE.search(it))]
    charter = [m.group(0) for it in schemas._items(packet.section("Charter items")) if (m := _CHARTER_ID_RE.search(it))]
    return acs + charter


def unaccounted(v: schemas.Verdict, packet: "schemas.Packet | None") -> list[str]:
    """Packet items in neither `held` nor any finding's ac/charter. Silence is not a pass. The
    packet's Charter section holds reachable items only, so nothing unreachable lands here (E22)."""
    held = set(v.held)
    cited = {f.get("ac") for f in v.findings} | {f.get("charter") for f in v.findings}
    return [i for i in packet_items(packet) if i not in held and i not in cited]


def packet_charter(packet: "schemas.Packet | None") -> list[str]:
    """The charter ids the packet showed, in packet order: the reachable items and nothing else."""
    return [i for i in packet_items(packet) if i.startswith("charter-")]


def charter_report(v: schemas.Verdict, packet: "schemas.Packet | None") -> dict:
    """{"reachable": [ids], "held": [ids], "findings": {"charter-n": ["F3", ...]}} for the result
    block's charter line: the reachable items, those in `held`, and for each reachable item not
    held the finding ids citing it (E22: what the diff could reach, and what happened to it)."""
    reachable = packet_charter(packet)
    held = [i for i in reachable if i in set(v.held)]
    findings = {}
    for i in reachable:
        if i in held:
            continue
        ids = [str(f.get("id")) for f in v.findings if f.get("charter") == i and f.get("id")]
        if ids:
            findings[i] = ids
    return {"reachable": reachable, "held": held, "findings": findings}


def validate(v: schemas.Verdict, arm: str, packet: "schemas.Packet | None" = None) -> tuple[schemas.Verdict, list[str]]:
    """The verdict as it should be stored, and the violations found. Blind cannot cite, so its
    uncited blocks become warns (text unchanged); under any other arm they are violations and
    the verdict is returned as written. Every AC and charter item the packet showed must be in
    `held` or cited by a finding; the rest become C-warns "unaccounted: <id>" (appended once;
    a re-run does not duplicate them)."""
    violations: list[str] = []
    uncited = v.uncited_blocks()
    if uncited and arm != "blind":
        violations = [f"{f.get('id')}: block without an ac or charter citation ({arm} seat)" for f in uncited]
    elif uncited:
        ids = {id(f) for f in uncited}
        v = v.with_findings(tuple({**f, "severity": "warn"} if id(f) in ids else f for f in v.findings))
        # the prompt's rule, applied mechanically after the downgrade: reject iff an open block or CI red
        v = v.with_decision("reject" if v.open_blocks() or v.ci.get("green") is False else "ship")
    missing = unaccounted(v, packet)
    if missing:
        n = max((int(f["id"][1:]) for f in v.findings if isinstance(f.get("id"), str) and re.fullmatch(r"C\d+", f["id"])), default=0)
        extra = []
        for item in missing:
            n += 1
            extra.append(finding(f"C{n}", "warn", f"unaccounted: {item}", ac=item if item.startswith("AC-") else None))
            if item.startswith("charter-"):
                extra[-1]["charter"] = item
        v = v.with_findings(v.findings + tuple(extra))
    return v, violations


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
    base = argv[argv.index("--base") + 1] if "--base" in argv else default_base(root, tid.split(".")[0])
    print(json.dumps(checks(root, tid, base), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
