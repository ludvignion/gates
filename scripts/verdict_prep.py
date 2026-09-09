#!/usr/bin/env python3
"""One packet for the single-call verdict. Run: verdict_prep.py <id> [--arm blind|packet|repo] [--base main] [--no-ci]

Writes traces/verdict/<id>.input.md and prints its path. The packet is the seam between the
harness and whichever vendor renders the verdict: frontmatter (ticket, arm, output path,
writes, always_writable, prompt_sha, plugin_version), the judging instructions copied from
skills/verdict/verdict-prompt.md, a seat line, then the evidence sections: the ticket's ACs,
Out of scope, human waivers; the plan's "Verdict must attack" items; the charter items; the
previous verdict's blocks with their repro commands (the existing <id>.json is archived as
<id>.prev.json first); `make ci` result; the mechanical findings from verdict_checks.py; tests
added on the branch that name ACs; the diff.

The diff rule (E17: a 532-line lock file made the reviewer read hashes): only files under src/,
docs/, tests/ (*.py only, never tests/fixtures/) and the lint config files (pyproject.toml,
ruff.toml, .ruff.toml, mypy.ini, .mypy.ini, .importlinter, setup.cfg) enter the Diff and the
Diff stat. Lock files, fixtures, kanban/ and traces/ are neither in the diff nor in the stat;
the stat ends with one line "N files excluded (lock, fixture, kanban)". An included file over
2000 diff lines is a stat line only, and the whole diff is truncated past 4000 lines.

The Spec section (0.8.0) carries the text of the units the ticket's plan's brief cites in
`spec_refs`, read from docs/spec/index.md and the spec file; `- none` without an index or a
citing brief. It counts toward the runner's packet cap like every other section.

`always_writable` names the paths every ticket may touch (docs/glossary.md, the parent plan's
Log) so the reviewer does not warn on them. Refuses to build without a charter, and refuses a
charter item without an `Applies to:` line. The Charter section lists only the items whose
globs reach an included changed file, each with its Pattern/Anti-pattern lines; an unreachable
item is mentioned nowhere (E22). ACs tagged `(human)` stay out of the AC section (E30). An empty
included diff is refused with "nothing to judge" before any model call (E20). The base branch
defaults to kanban_ops.base_branch (the plan's `base:`, else main/master) (E21);
`changed_vs_base` is the per-file numstat over the included files for the result block (E27).
The arm picks the sections (schemas.Packet): blind = instructions, seat, CI, diff; packet =
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
import kanban_ops  # noqa: E402
import schemas  # noqa: E402
import spec_intake  # noqa: E402
import verdict_checks as vc  # noqa: E402

DIFF_CAP = 4000  # lines; beyond this the diff is truncated and says so
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = PLUGIN_ROOT / "skills" / "verdict" / "verdict-prompt.md"

# E17: what the reviewer reads. Prefixes enter whole; tests/ enters for *.py only and never its
# fixtures; the config files enter by exact path. Everything else is excluded from diff and stat.
DIFF_INCLUDE = ("src/", "docs/")
TESTS_PREFIX = "tests/"
FIXTURES_PREFIX = "tests/fixtures/"
CONFIG_FILES = ("pyproject.toml", "ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini", ".importlinter", "setup.cfg")
LOCK_NAMES = ("package-lock.json", "poetry.lock", "uv.lock")
EXCLUDED_LINE = "{n} files excluded (lock, fixture, kanban)"  # the stat's last line; the parenthetical is fixed by the spec


def is_lock(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(".lock") or name in LOCK_NAMES


def included(path: str) -> bool:
    """True when the file belongs in the reviewer's diff: source, python tests, docs, lint config."""
    if is_lock(path):
        return False
    if path.startswith(TESTS_PREFIX):
        return path.endswith(".py") and not path.startswith(FIXTURES_PREFIX)
    return path.startswith(DIFF_INCLUDE) or path in CONFIG_FILES


def exclusion_reason(path: str) -> str:
    """Why an excluded file stays out: lock, fixture, kanban (kanban/ and traces/), other."""
    if is_lock(path):
        return "lock"
    if path.startswith(TESTS_PREFIX):
        return "fixture"
    if path.startswith(("kanban/", "traces/")):
        return "kanban"
    return "other"


def split_files(files: list[str]) -> tuple[list[str], list[str]]:
    """(included, excluded) in git order."""
    return [f for f in files if included(f)], [f for f in files if not included(f)]


def default_base(root: Path, plan_n: str | None = None) -> str:
    """The plan's base branch through kanban_ops.base_branch (E21: never the current branch)."""
    return kanban_ops.base_branch(root, plan_n)


def changed_files(root: Path, base: str) -> list[str]:
    """The included files changed on base...HEAD, in git order."""
    return split_files(vc.git(root, "diff", "--name-only", f"{base}...HEAD").splitlines())[0]


def changed_vs_base(root: Path, base: str) -> dict:
    """{"base", "files": [{"path", "added", "removed"}]} from `git diff --numstat base...HEAD`
    over the included files; a binary file's "-" counts as 0 (E27: the result block shows this)."""
    kept = changed_files(root, base)
    files = []
    for line in (vc.git(root, "diff", "--numstat", f"{base}...HEAD", "--", *kept) if kept else "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        num = lambda x: int(x) if x.isdigit() else 0
        files.append({"path": parts[2], "added": num(parts[0]), "removed": num(parts[1])})
    return {"base": base, "files": files}


def charter_section(charter: schemas.Charter, paths: list[str]) -> list[str]:
    """One item per reachable charter item: "charter-<n>: <title>" with its Pattern/Anti-pattern
    lines folded under it. Unreachable items are not mentioned (E22)."""
    reach = set(charter.reachable(paths))
    return ["\n  ".join([f"{it.id}: {it.title}", *it.pattern_lines()]) for it in charter.items if it.id in reach]


def spec_section(root: Path, plan_fm: dict) -> list[str]:
    """One entry per unit the plan's brief cites: ``<id> — <title>`` with the unit text under it.
    Empty without an index, a brief, or spec_refs; an id the spec no longer has says so."""
    index_path = root / schemas.SPEC_INDEX
    if not index_path.is_file() or not str(plan_fm.get("brief", "")).isdigit():
        return []
    brief = next((b for b in schemas.briefs(root / "kanban") if b.n == int(plan_fm["brief"])), None)
    if brief is None or not brief.spec_refs:
        return []
    texts = spec_intake.unit_texts(root, schemas.SpecIndex.load(index_path))
    out = []
    for uid in brief.spec_refs:
        if uid in texts:
            title, text = texts[uid]
            out.append(f"{uid} — {title}\n  " + text.replace("\n", "\n  "))
        else:
            out.append(f"{uid} — not in the spec (coverage.py reports it)")
    return out


def plugin_version() -> str:
    manifest = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    return json.loads(manifest.read_text()).get("version", "") if manifest.exists() else ""


def prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def packet_path(root: Path, tid: str) -> Path:
    return root / "traces" / "verdict" / f"{tid}.input.md"


def always_writable(plan_n: str) -> list[str]:
    """Paths every ticket may touch without a writes: entry; the reviewer does not warn on them."""
    return [x.format(n=plan_n) for x in vc.ALWAYS_WRITABLE]


def run_ci(root: Path) -> tuple[bool | None, str]:
    if not (root / "Makefile").exists() or "ci:" not in (root / "Makefile").read_text(errors="ignore"):
        return None, "no `make ci` target"
    r = subprocess.run(["make", "-s", "ci"], cwd=root, capture_output=True, text=True, timeout=900)
    tail = "\n".join((r.stdout + r.stderr).splitlines()[-25:])
    return r.returncode == 0, tail


def _sec(items) -> str:
    return "\n" + ("\n".join(f"- {i}" for i in items) if items else "- none") + "\n\n"


def diff_and_stat(root: Path, rng: str) -> tuple[str, str]:
    """The included files' diff (folded, capped) and their stat plus the excluded-count line."""
    kept, dropped = split_files(vc.git(root, "diff", "--name-only", rng).splitlines())
    if kept:
        diff = fold_large(vc.git(root, "diff", rng, "--", *kept))
        stat = vc.git(root, "diff", "--stat", rng, "--", *kept)
    else:
        diff, stat = "no included files changed\n", ""
    lines = diff.splitlines()
    if len(lines) > DIFF_CAP:
        diff = "\n".join(lines[:DIFF_CAP]) + f"\n... truncated: {len(lines) - DIFF_CAP} more lines; run `git diff {rng} -- <file>` for a file\n"
    if dropped:
        stat += EXCLUDED_LINE.format(n=len(dropped)) + "\n"
    return diff, stat


def gather(root: Path, tid: str, base: str, ci: bool) -> schemas.Packet:
    """The full (packet-seat) packet; ``Packet.rearm`` narrows it."""
    tpath = vc.find_ticket(root, tid)
    fm, body = _fm.read(tpath)
    ticket = schemas.Ticket.parse(body)
    plan_n = str(fm.get("parent", tid.split(".")[0]))
    plan_path = next((root / "kanban").rglob(f"{plan_n}.plan.md"), None)
    plan_fm, plan_body = _fm.read(plan_path) if plan_path else ({}, "")
    attacks = schemas.Attacks.parse(plan_body).items
    charter_path = root / "docs" / "domain-pack" / "charter.md"
    charter = schemas.Charter.parse(charter_path.read_text(errors="ignore")) if charter_path.exists() else schemas.Charter()
    if not charter.items:
        raise SystemExit(f"no charter: {charter_path.relative_to(root)} is missing or has no `## <n>. <title>` items; the verdict cannot judge without it")
    if missing := charter.missing_applies():
        raise SystemExit(f"charter item {missing[0]} has no Applies to: line ({charter_path.relative_to(root)}); every item names the paths it reaches")
    kept = changed_files(root, base)
    if not kept:  # E20: a verdict ran on an empty included diff and warned on nothing
        raise SystemExit(f"nothing to judge: no included file changed against {base}")
    prev_path = root / "traces" / "verdict" / f"{tid}.prev.json"
    cur = root / "traces" / "verdict" / f"{tid}.json"
    if cur.exists():  # the last verdict on this ticket is the previous one; archive before the new run
        shutil.copyfile(cur, prev_path)
    prev_blocks = schemas.Verdict.load(prev_path).blocks() if prev_path.exists() else ()
    ci_green, ci_tail = run_ci(root) if ci else (None, "skipped (--no-ci)")
    found = vc.checks(root, tid, base, ci_green)
    rng = f"{base}...HEAD"
    diff, stat = diff_and_stat(root, rng)
    test_lines = [
        f"{f}: {l[1:].strip()[:120]}"
        for f, l in _added_lines(diff) if "test" in f and schemas.AC_ID_RE.search(l)
    ]
    head = {
        "ticket": tid, "arm": schemas.DEFAULT_ARM, "base": base,
        "output": str(packet_path(root, tid).with_name(f"{tid}.json").relative_to(root)),
        "ticket_file": str(tpath.relative_to(root)), "status": fm.get("status"), "writes": fm.get("writes") or [],
        "always_writable": always_writable(plan_n),
        "plugin_version": plugin_version(), "prompt_sha": schemas.sha256(prompt_text()),
    }
    sections = (
        ("Instructions", "\n" + prompt_text().rstrip() + "\n\n"),
        ("Seat", "\n" + schemas.SEAT_LINE[schemas.DEFAULT_ARM] + "\n\n"),
        ("Acceptance criteria", _sec(ticket.acs)),
        ("Out of scope", _sec(ticket.out_of_scope)),
        ("Human waivers (Log)", _sec(ticket.waivers)),
        ("Plan: verdict must attack", _sec(attacks)),
        ("Spec", _sec(spec_section(root, plan_fm))),
        ("Charter items", _sec(charter_section(charter, kept))),
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


FILE_CAP = 2000  # diff lines per included file; beyond this the file is a stat line only


def fold_large(diff: str) -> str:
    """The diff with any file over FILE_CAP diff lines reduced to one line, so a generated
    module does not become 30K tokens of reviewer context."""
    out: list[str] = []
    chunk: list[str] = []
    path = ""

    def flush() -> None:
        if not chunk:
            return
        if len(chunk) > FILE_CAP:
            out.append(f"diff --git a/{path} b/{path}\n# {path}: {len(chunk)} diff lines omitted (over {FILE_CAP} lines); see the diff stat\n")
        else:
            out.append("\n".join(chunk) + "\n")

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            chunk, path = [], line.split(" b/", 1)[-1] if " b/" in line else line[len("diff --git a/"):].split(" ")[0]
        chunk.append(line)
    flush()
    return "".join(out)


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
    ap.add_argument("--base", default=None, help="the branch the diff is judged against; default: the plan's base: (kanban_ops.base_branch)")
    ap.add_argument("--no-ci", action="store_true")
    a = ap.parse_args(argv[1:])
    root = Path(".").resolve()
    base = a.base or default_base(root, a.ticket.split(".")[0])
    text = build(root, a.ticket, base, ci=not a.no_ci, arm=a.arm)
    out = packet_path(root, a.ticket)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(out.relative_to(root))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
