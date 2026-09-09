#!/usr/bin/env python3
"""Index a spec. Run: spec_intake.py <file>  (from the project root; the file lives in docs/spec/)

Writes docs/spec/index.md: frontmatter ``spec:`` and ``units:``, then one table row per
requirement unit — ``| id | title | words | sha |`` — where ``sha`` is the first 12 hex of
sha256 over the unit text with line endings folded to LF and trailing whitespace stripped
(schemas.unit_sha). The spec file itself is never modified; re-running rewrites the index fully.

Two input shapes, nothing else:
- Markdown with headings. A unit is every heading whose body (up to the next heading of any
  level; fenced code blocks are text, never headings) is non-empty. Its id is the heading
  path slugged: lowercase, runs of non [a-z0-9] become ``-``, segments joined with ``/``
  (``## Contacts`` / ``### Call log`` is ``contacts/call-log``). A heading with an empty body is
  a container: no unit, but it stays in the path of the headings under it. A lone ``#`` heading
  that opens the file is the document title: not a path segment, not a unit.
- CSV with an ``id`` column (header row, UTF-8, a BOM tolerated). Ids are kept verbatim; the
  unit text is the row's other cells as ``column: value`` lines; the title is the ``title``
  column when there is one, else the first other cell.

Refused, one line on stderr, exit 1: no headings and no id column; a duplicate id (the same
heading path twice, or a repeated CSV id); an empty id cell; a heading that slugs to nothing
(non-Latin text: add an id column); a file outside docs/spec/; a second spec file in docs/spec/
(v1 is one file per project). A unit over 400 words is a warning on stderr, not an error.
"""
import csv
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import schemas  # noqa: E402

WORD_WARN = 400
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
INDEX_NAMES = ("index.md", "deferred.md")  # docs/spec/ files that are not the spec


class Unit:
    __slots__ = ("id", "title", "text")

    def __init__(self, uid: str, title: str, text: str) -> None:
        self.id, self.title, self.text = uid, title, text


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def markdown_units(text: str) -> list[Unit]:
    """Units from headings; raises ValueError on a duplicate path or a heading that slugs to nothing."""
    heads: list[tuple[int, str]] = []  # (level, text)
    bodies: list[list[str]] = []
    in_fence = False
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(line)
        if m:
            heads.append((len(m.group("hashes")), m.group("text").strip()))
            bodies.append([])
        elif bodies:
            bodies[-1].append(line)
    if not heads:
        raise ValueError("no headings and no id column: the spec is markdown with headings or a CSV with an id column")
    if heads[0][0] == 1 and sum(1 for lvl, _ in heads if lvl == 1) == 1:  # the document title
        heads, bodies = heads[1:], bodies[1:]
    path: list[tuple[int, str]] = []
    units: list[Unit] = []
    seen: set[str] = set()
    for (level, title), body in zip(heads, bodies):
        seg = slug(title)
        if not seg:
            raise ValueError(f"heading {title!r} slugs to nothing: add an id column (use the CSV shape)")
        while path and path[-1][0] >= level:
            path.pop()
        path.append((level, seg))
        uid = "/".join(s for _, s in path)
        unit_text = "\n".join(body).strip()
        if not unit_text:
            continue  # a container: no unit, stays in the path
        if uid in seen:
            raise ValueError(f"duplicate id {uid}: the same heading path twice")
        seen.add(uid)
        units.append(Unit(uid, title, unit_text))
    return units


def csv_units(text: str) -> list[Unit]:
    """Units from CSV rows; raises ValueError without an id column, on an empty or repeated id."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise ValueError("no headings and no id column: the file is empty")
    header = [h.strip() for h in rows[0]]
    lower = [h.lower() for h in header]
    if "id" not in lower:
        raise ValueError("no headings and no id column: the CSV header row has no `id` column")
    id_col = lower.index("id")
    title_col = lower.index("title") if "title" in lower else next((i for i in range(len(header)) if i != id_col), None)
    units: list[Unit] = []
    seen: set[str] = set()
    for n, row in enumerate(rows[1:], 2):
        if not any(c.strip() for c in row):
            continue
        uid = row[id_col].strip() if id_col < len(row) else ""
        if not uid:
            raise ValueError(f"row {n} has an empty id cell")
        if uid in seen:
            raise ValueError(f"duplicate id {uid} (row {n})")
        seen.add(uid)
        cells = [(header[i], row[i].strip()) for i in range(len(header)) if i != id_col and i < len(row)]
        body = "\n".join(f"{h}: {v}" for h, v in cells if v)
        title = row[title_col].strip() if title_col is not None and title_col < len(row) else uid
        units.append(Unit(uid, (title or uid)[:80], body))
    return units


def units(path: Path) -> list[Unit]:
    text = path.read_text(encoding="utf-8-sig")
    return csv_units(text) if path.suffix.lower() == ".csv" else markdown_units(text)


def index_for(root: Path, spec: Path) -> tuple[schemas.SpecIndex, list[str]]:
    """The index and the over-length warnings for the spec at ``spec``."""
    rel = spec.resolve().relative_to(root.resolve()).as_posix()
    if not rel.startswith(schemas.SPEC_DIR + "/"):
        raise ValueError(f"{rel} is outside {schemas.SPEC_DIR}/: put the spec there first")
    others = [p.name for p in (root / schemas.SPEC_DIR).iterdir()
              if p.is_file() and p.name not in INDEX_NAMES and not p.name.startswith(".") and p.resolve() != spec.resolve()]
    if others:
        raise ValueError(f"more than one spec file in {schemas.SPEC_DIR}/ ({', '.join(sorted(others))}); v1 is one file per project")
    found = units(spec)
    rows = tuple(schemas.SpecUnit(u.id, u.title, len(u.text.split()), schemas.unit_sha(u.text)) for u in found)
    warns = [f"warn: {u.id} has {u.words} words (over {WORD_WARN})" for u in rows if u.words > WORD_WARN]
    return schemas.SpecIndex(spec=rel, units=rows), warns


def unit_texts(root: Path, index: schemas.SpecIndex) -> dict[str, tuple[str, str]]:
    """{id: (title, text)} for every unit of the indexed spec, as the verdict packet shows them."""
    spec = root / index.spec
    if not spec.is_file():
        return {}
    try:
        return {u.id: (u.title, u.text) for u in units(spec)}
    except ValueError:
        return {}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: spec_intake.py <docs/spec/file>", file=sys.stderr)
        return 2
    root = Path(".").resolve()
    spec = Path(argv[1])
    if not spec.is_file():
        print(f"refused: {argv[1]} is not a file", file=sys.stderr)
        return 1
    try:
        index, warns = index_for(root, spec)
    except (ValueError, UnicodeDecodeError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    out = root / schemas.SPEC_INDEX
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(index.render(), encoding="utf-8")
    for w in warns:
        print(w, file=sys.stderr)
    print(f"{schemas.SPEC_INDEX}: {len(index.units)} units from {index.spec}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
