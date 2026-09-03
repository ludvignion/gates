#!/usr/bin/env python3
"""One packet for the single-call verdict. Run: verdict_prep.py <id> [--arm blind|packet|repo] [--base main] [--no-ci]

Writes traces/verdict/<id>.input.md and prints its path. The packet is the seam between the
harness and whichever vendor renders the verdict: frontmatter (ticket, arm, output path,
prompt_sha, plugin_version), the judging instructions copied from skills/verdict/verdict-prompt.md,
a seat line, then the evidence sections: the ticket's ACs, Out of scope, human waivers; the
plan's "Verdict must attack" items; the charter items; the previous verdict's blocks with their
repro commands (the existing <id>.json is archived as <id>.prev.json first); `make ci` result;
the mechanical findings from verdict_checks.py; tests added on the branch that name ACs; the
diff. The arm picks the sections (schemas.Packet): blind = instructions, seat, CI, diff; packet =
everything; repo = everything plus leave to read the tree read-only. Shapes load through
scripts/schemas.py.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import schemas  # noqa: E402
import verdict_checks as vc  # noqa: E402

DIFF_CAP = 4000  # lines; beyond this the diff is truncated and says so
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = PLUGIN_ROOT / "skills" / "verdict" / "verdict-prompt.md"


def plugin_version() -> str:
    manifest = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    return json.loads(manifest.read_text()).get("version", "") if manifest.exists() else ""


def prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def packet_path(root: Path, tid: str) -> Path:
    return root / "traces" / "verdict" / f"{tid}.input.md"


def run_ci(root: Path) -> tuple[bool | None, str]:
    if not (root / "Makefile").exists() or "ci:" not in (root / "Makefile").read_text(errors="ignore"):
        return None, "no `make ci` target"
    r = subprocess.run(["make", "-s", "ci"], cwd=root, capture_output=True, text=True, timeout=900)
    tail = "\n".join((r.stdout + r.stderr).splitlines()[-25:])
    return r.returncode == 0, tail


def _sec(items) -> str:
    return "\n" + ("\n".join(f"- {i}" for i in items) if items else "- none") + "\n\n"


def gather(root: Path, tid: str, base: str, ci: bool) -> schemas.Packet:
    """The full (packet-seat) packet; ``Packet.rearm`` narrows it."""
    tpath = vc.find_ticket(root, tid)
    fm, body = _fm.read(tpath)
    ticket = schemas.Ticket.parse(body)
    plan_path = next((root / "kanban").rglob(f"{fm.get('parent', tid.split('.')[0])}.plan.md"), None)
    plan_body = _fm.read(plan_path)[1] if plan_path else ""
    attacks = schemas.Attacks.parse(plan_body).items
    charter_path = root / "docs" / "domain-pack" / "charter.md"
    charter = schemas.Charter.parse(charter_path.read_text(errors="ignore")).items if charter_path.exists() else ()
    prev_path = root / "traces" / "verdict" / f"{tid}.prev.json"
    cur = root / "traces" / "verdict" / f"{tid}.json"
    if cur.exists():  # the last verdict on this ticket is the previous one; archive before the new run
        shutil.copyfile(cur, prev_path)
    prev_blocks = schemas.Verdict.load(prev_path).blocks() if prev_path.exists() else ()
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
    head = {
        "ticket": tid, "arm": schemas.DEFAULT_ARM, "base": base,
        "output": str(packet_path(root, tid).with_name(f"{tid}.json").relative_to(root)),
        "ticket_file": str(tpath.relative_to(root)), "status": fm.get("status"), "writes": fm.get("writes") or [],
        "plugin_version": plugin_version(), "prompt_sha": schemas.sha256(prompt_text()),
    }
    sections = (
        ("Instructions", "\n" + prompt_text().rstrip() + "\n\n"),
        ("Seat", "\n" + schemas.SEAT_LINE[schemas.DEFAULT_ARM] + "\n\n"),
        ("Acceptance criteria", _sec(ticket.acs)),
        ("Out of scope", _sec(ticket.out_of_scope)),
        ("Human waivers (Log)", _sec(ticket.waivers)),
        ("Plan: verdict must attack", _sec(attacks)),
        ("Charter items", _sec([f"charter-{n}: {t}" for n, t in charter])),
        ("Previous verdict: blocks to re-run", _sec([
            f"{b['id']} {b.get('status')} ({b.get('ac') or b.get('charter')}): {b['text']} — repro: `{b.get('repro')}`" for b in prev_blocks])),
        ("CI", f"\n{'green' if ci_green else 'RED' if ci_green is False else 'not run'}\n```\n{ci_tail}\n```\n\n"),
        ("Mechanical findings (copy verbatim into findings)", "\n```json\n" + json.dumps(found, indent=1) + "\n```\n\n"),
        ("Tests added on this branch that name an AC", _sec(test_lines[:60])),
        ("Diff stat", f"\n```\n{stat}```\n\n"),
        ("Diff", f"\n```diff\n{diff}\n```\n"),
    )
    assert tuple(t for t, _ in sections) == schemas.PACKET_SECTIONS
    return schemas.Packet(fm=head, sections=sections)


def build(root: Path, tid: str, base: str, ci: bool, arm: str = schemas.DEFAULT_ARM) -> str:
    return gather(root, tid, base, ci).rearm(arm).render()


def _added_lines(diff: str):
    cur = None
    for l in diff.splitlines():
        if l.startswith("+++ b/"):
            cur = l[6:]
        elif cur and l.startswith("+") and not l.startswith("+++"):
            yield cur, l


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ticket")
    ap.add_argument("--arm", choices=schemas.ARMS, default=schemas.DEFAULT_ARM,
                    help="how much context the reviewer sees: blind = diff + prompt; packet = everything (default); repo = packet + read-only access to the tree")
    ap.add_argument("--base", default=None)
    ap.add_argument("--no-ci", action="store_true")
    a = ap.parse_args(argv[1:])
    root = Path(".").resolve()
    base = a.base or vc.default_base(root)
    text = build(root, a.ticket, base, ci=not a.no_ci, arm=a.arm)
    out = packet_path(root, a.ticket)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(out.relative_to(root))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
