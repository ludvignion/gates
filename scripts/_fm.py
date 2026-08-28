"""Tiny frontmatter reader shared by the scripts. No YAML dependency."""
from pathlib import Path


def read(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    if not text.startswith("---"):
        return {}, text
    _, head, body = text.split("---", 2)
    fm: dict = {}
    for line in head.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.split("#", 1)[0].strip()
        if v.startswith("[") and v.endswith("]"):
            fm[k.strip()] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            fm[k.strip()] = v
    return fm, body


def tickets(kanban: Path) -> list[tuple[Path, dict, str]]:
    out = []
    for p in sorted(kanban.rglob("*.md")):
        if p.name.endswith(".plan.md"):
            continue
        fm, body = read(p)
        if "id" in fm:
            out.append((p, fm, body))
    return out
