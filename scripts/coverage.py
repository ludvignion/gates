#!/usr/bin/env python3
"""Spec coverage. Run: python3 coverage.py [root]  (default .). Exit 1 on any violation; when
docs/spec/index.md is absent it prints nothing and exits 0, so a project without a spec is unchanged.

Rules, each loading through scripts/schemas.py (SpecIndex, Deferred, briefs):
1. PARTITION. Every index id appears in exactly one brief's ``spec_refs`` or on one line of
   docs/spec/deferred.md (``- <id> — <reason>``): never both, never neither, never twice.
2. EXISTS. Every id a brief or the deferred file cites is in the index; a renamed heading is
   reported at the brief that cited the old id.
3. DRIFT. For each brief, the sha of every cited unit equals its sha in the index at the last
   commit that touched the brief (``git log -1 -- <brief>``, then ``git show <sha>:index``).
   A unit edited in the same commit as the brief is not drift; an uncommitted brief, or one whose
   last commit predates the index, is not judged. To acknowledge drift, review the brief, edit
   it, and commit it together with the re-run index: that commit becomes the record. No
   frontmatter field; git is the record.
4. AFTER. ``after`` names briefs that have a file, and there is no cycle.
5. REASON. Every deferred line carries a reason.
6. CITES. A brief numbered 1 or higher has a non-empty ``spec_refs`` while an index exists.
Errors print as ``path: message``. A brief whose cited units sum past WORDS_WARN words is a
warning on stderr, not a violation: the verdict packet carries all of them (item 1 of 0.8.1).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import schemas  # noqa: E402

WORDS_WARN = 4000  # words of cited units per brief; past this the Spec section is a packet risk


def _git(root: Path, *args: str) -> str | None:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def index_at_brief_commit(root: Path, brief: Path) -> schemas.SpecIndex | None:
    """The index as it was in the last commit that touched the brief; None when there is no such
    commit (uncommitted brief) or no index in it."""
    log = _git(root, "log", "-1", "--format=%H", "--", _rel(root, brief))
    if not log or not log.split():
        return None
    old = _git(root, "show", f"{log.split()[0]}:{schemas.SPEC_INDEX}")
    return schemas.SpecIndex.parse(old) if old is not None else None


def partition(root: Path, index: schemas.SpecIndex, briefs: list[schemas.BriefRefs], deferred: schemas.Deferred) -> list[str]:
    out = []
    ids = set(index.ids)
    cited: dict[str, schemas.BriefRefs] = {}
    for b in briefs:
        rel = _rel(root, b.path)
        if b.n >= 1 and not b.spec_refs:
            out.append(f"{rel}: brief {b.n} has no spec_refs while {schemas.SPEC_INDEX} exists")
        for uid in b.spec_refs:
            if uid not in ids:
                out.append(f"{rel}: [{uid}] is not in the index (renamed heading? re-run intake and update brief {b.n})")
            if uid in cited:
                out.append(f"{rel}: [{uid}] is also cited by brief {cited[uid].n}; one brief per id")
            else:
                cited[uid] = b
    seen_deferred: set[str] = set()
    for line in deferred.lines:
        where = f"{schemas.SPEC_DEFERRED}: line {line.line_no}"
        if not line.reason:
            out.append(f"{where} has no reason: [{line.id}]")
        if line.id not in ids:
            out.append(f"{where}: [{line.id}] is not in the index")
        if line.id in seen_deferred:
            out.append(f"{where}: [{line.id}] deferred twice")
        seen_deferred.add(line.id)
        if line.id in cited:
            out.append(f"{_rel(root, cited[line.id].path)}: [{line.id}] is cited by brief {cited[line.id].n} and deferred ({where})")
    for uid in index.ids:
        if uid not in cited and uid not in seen_deferred:
            out.append(f"{schemas.SPEC_INDEX}: [{uid}] is in no brief and not deferred")
    return out


def drift(root: Path, index: schemas.SpecIndex, briefs: list[schemas.BriefRefs]) -> list[str]:
    out = []
    for b in briefs:
        if not b.spec_refs:
            continue
        old = index_at_brief_commit(root, b.path)
        if old is None:
            continue
        for uid in b.spec_refs:
            then, now = old.sha_of(uid), index.sha_of(uid)
            if then and now and then != now:
                out.append(f"{_rel(root, b.path)}: drift: [{uid}] changed since brief {b.n}")
    return out


def after(root: Path, briefs: list[schemas.BriefRefs]) -> list[str]:
    out = []
    by_n = {b.n: b for b in briefs}
    for b in briefs:
        for a in b.after:
            if a not in by_n:
                out.append(f"{_rel(root, b.path)}: after names brief {a}, which has no file")
    reported: set[frozenset] = set()
    state: dict[int, int] = {}  # 1 = on the stack, 2 = done

    def walk(n: int, stack: list[int]) -> None:
        state[n] = 1
        stack.append(n)
        for a in by_n[n].after:
            if a not in by_n:
                continue
            if state.get(a) == 1:
                cycle = stack[stack.index(a):] + [a]
                if frozenset(cycle) not in reported:
                    reported.add(frozenset(cycle))
                    out.append(f"{_rel(root, by_n[a].path)}: after cycle: {' after '.join(str(x) for x in cycle)}")
            elif a not in state:
                walk(a, stack)
        stack.pop()
        state[n] = 2

    for n in sorted(by_n):
        if n not in state:
            walk(n, [])
    return out


def warnings(root: Path, index: schemas.SpecIndex, briefs: list[schemas.BriefRefs]) -> list[str]:
    """Briefs whose cited units sum past WORDS_WARN words; advisory, never a violation."""
    words = {u.id: u.words for u in index.units}
    out = []
    for b in briefs:
        total = sum(words.get(uid, 0) for uid in b.spec_refs)
        if total > WORDS_WARN:
            out.append(f"warn: {_rel(root, b.path)}: brief {b.n} cites {total} words over {len(b.spec_refs)} units (over {WORDS_WARN}); the verdict packet carries them all")
    return out


def lint(root: Path) -> list[str] | None:
    """None when there is no index (nothing to say); else the violations."""
    index_path = root / schemas.SPEC_INDEX
    if not index_path.is_file():
        return None
    index = schemas.SpecIndex.load(index_path)
    deferred_path = root / schemas.SPEC_DEFERRED
    deferred = schemas.Deferred.parse(deferred_path.read_text(encoding="utf-8")) if deferred_path.is_file() else schemas.Deferred()
    briefs = schemas.briefs(root / "kanban")
    return partition(root, index, briefs, deferred) + drift(root, index, briefs) + after(root, briefs)


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    violations = lint(root)
    if violations is None:
        return 0
    for v in violations:
        print(v)
    index = schemas.SpecIndex.load(root / schemas.SPEC_INDEX)
    for w in warnings(root, index, schemas.briefs(root / "kanban")):
        print(w, file=sys.stderr)
    print(f"coverage: {len(violations)} violation(s) over {len(index.units)} units")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
