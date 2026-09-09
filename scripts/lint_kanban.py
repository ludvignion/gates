#!/usr/bin/env python3
"""Kanban invariants. Run: python3 lint_kanban.py [base]  (default HEAD: uncommitted changes;
in CI pass the merge base, e.g. origin/main). Exit 1 on any violation.

Rules, each loading through scripts/schemas.py (no parsing of its own):
1. CLOSED TICKETS ARE IMMUTABLE. A ticket that was done|superseded at <base> differs now
   outside its append-only sections, or was deleted. Compares <base> with the working tree, so
   it catches edits made outside a Claude session too. (ClosedTicketDiff)
2. FINDING HOMES. Every ``— finding:`` entry in a ticket ``## Log`` carries an explicit
   ``home: <id>`` / ``homed to <id>`` marker naming a ticket file, or a ``[human]`` waiver or
   build close-out names its title. Ticket ids in the text without a marker do not home it;
   they are reported as candidates. A finding homed to a ticket id with no file, and a
   done|superseded ticket whose Log never mentions a finding homed to it, are violations.
   (Log, Finding)
3. NO SHIP PAST OPEN BLOCK. A ticket whose traces/verdict/<id>.json has an open block may not
   carry a ``[verdict] … — ship`` Log entry or ``status: done`` unless a ``[human]`` Log entry
   names that finding id. (Log)
4. ROUTING STAMP. An approved plan carries ``signals`` (spend, partner_facing, parallel_ready,
   tickets), ``scrutiny`` and ``backend`` in its frontmatter. (RoutingStamp)
5. CHARTER REACH. Every item in ``docs/domain-pack/charter.md`` carries an ``Applies to:`` glob
   line, so the verdict can tell which items a diff reaches (E22). (Charter)
6. SPEC IDS IN ACS. An approved plan whose brief has ``spec_refs`` names every cited id in
   square brackets on an AC line, ``[contacts/call-log]``. (briefs, Plan)
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import schemas  # noqa: E402


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def closed_tickets(root: Path, base: str = "HEAD") -> list[str]:
    out = []
    if subprocess.run(["git", "rev-parse", "--verify", "-q", base + "^{commit}"], cwd=root, capture_output=True).returncode != 0:
        # An unborn HEAD (init runs ci before its first commit) has closed nothing; a named base
        # that does not resolve is a CI misconfiguration, and rule 1 would silently pass.
        return [] if base == "HEAD" else [f"lint_kanban: base {base} does not resolve; rule 1 (closed tickets) not checked"]
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


def finding_homes(root: Path) -> list[str]:
    out = []
    tickets = _fm.tickets(root / "kanban") if (root / "kanban").is_dir() else []
    by_id = {fm["id"]: (p, fm, schemas.Log.parse(body)) for p, fm, body in tickets}
    homed_to: dict[str, list[tuple[str, str]]] = {}
    for tid, (p, fm, log) in by_id.items():
        rel = _rel(root, p)
        for f in log.findings(tid):
            if f.homes:
                for h in f.homes:
                    if h not in by_id:
                        out.append(f"{rel}: ticket {tid} finding homed to ticket {h}, which has no file: {f.title}")
                    else:
                        homed_to.setdefault(h, []).append((tid, f.title))
            elif not log.addresses(f.title):
                hint = f" (candidate homes seen: {', '.join(f.candidates)} — add 'home:' if intended)" if f.candidates else ""
                out.append(f"{rel}: ticket {tid} finding has no home (no home: marker, [human] waiver, or close-out names it): {f.title}{hint}")
    for tid, items in homed_to.items():
        p, fm, log = by_id[tid]
        if fm.get("status") not in schemas.CLOSED_STATUSES:
            continue
        for src, title in items:
            if not (log.mentions(title) or log.mentions(src)):
                out.append(f"{_rel(root, p)}: ticket {tid} is {fm['status']} but the finding homed here from {src} is unaddressed in its Log: {title}")
    return out


def open_blocks(root: Path) -> list[str]:
    out = []
    tickets = _fm.tickets(root / "kanban") if (root / "kanban").is_dir() else []
    for p, fm, body in tickets:
        tid = fm["id"]
        vpath = root / "traces" / "verdict" / f"{tid}.json"
        if not vpath.exists():
            continue
        try:
            blocks = schemas.Verdict.load(vpath).open_blocks()
        except (json.JSONDecodeError, ValueError):
            out.append(f"{_rel(root, vpath)}: not a verdict JSON object")
            continue
        if not blocks:
            continue
        log = schemas.Log.parse(body)
        how = "is done" if fm.get("status") == "done" else "has a [verdict] ship entry" if log.shipped() else ""
        if not how:
            continue
        waived = log.waived_ids()
        for f in blocks:
            if f.get("id") not in waived:
                out.append(f"{_rel(root, p)}: ticket {tid} {how} with open block {f.get('id')} and no [human] waiver naming it: {f.get('text', '')}")
    return out


def routing_stamps(root: Path) -> list[str]:
    out = []
    plans = sorted((root / "kanban").rglob("*.plan.md")) if (root / "kanban").is_dir() else []
    for p in plans:
        text = p.read_text()
        fm, _ = _fm.parse(text)
        if not fm.get("approved"):
            continue
        missing = schemas.RoutingStamp.parse(text).missing()
        if missing:
            n = p.name[: -len(".plan.md")]
            out.append(f"{_rel(root, p)}: plan {n} is approved but its frontmatter lacks {', '.join(missing)}")
    return out


CHARTER_PATH = "docs/domain-pack/charter.md"


def charter_reach(root: Path) -> list[str]:
    """Rule 5: a charter item without an ``Applies to:`` line, when the charter exists."""
    path = root / CHARTER_PATH
    if not path.exists():
        return []
    charter = schemas.Charter.parse(path.read_text(errors="ignore"))
    return [f"{CHARTER_PATH}: charter item {n} has no Applies to: line" for n in charter.missing_applies()]


def spec_ids_in_acs(root: Path) -> list[str]:
    """Rule 6: every id the plan's brief cites is named ``[id]`` on one of the plan's AC lines."""
    out = []
    plans = sorted((root / "kanban").rglob("*.plan.md")) if (root / "kanban").is_dir() else []
    briefs = {b.n: b for b in schemas.briefs(root / "kanban")} if plans else {}
    for p in plans:
        text = p.read_text()
        fm, body = _fm.parse(text)
        if not fm.get("approved") or not str(fm.get("brief", "")).isdigit():
            continue
        brief = briefs.get(int(fm["brief"]))
        if brief is None or not brief.spec_refs:
            continue
        named = {m for line in schemas.Plan.ac_lines(body) for m in schemas.SPEC_ID_BRACKET_RE.findall(line)}
        n = p.name[: -len(".plan.md")]
        for uid in brief.spec_refs:
            if uid not in named:
                out.append(f"{_rel(root, p)}: plan {n} is approved but no AC line names [{uid}] from brief {brief.n}")
    return out


def lint(root: Path, base: str = "HEAD") -> list[str]:
    return (closed_tickets(root, base) + finding_homes(root) + open_blocks(root) + routing_stamps(root)
            + charter_reach(root) + spec_ids_in_acs(root))


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
