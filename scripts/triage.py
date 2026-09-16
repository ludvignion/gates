"""triage.py <brief> [--cwd .]: the four checks that route a brief `light` or `full` (plan 5
AC-1). A pure function of the brief text, `docs/domain-pack/charter.md` and
`docs/domain-pack/interfaces.md` — zero tokens, no model call, no network call (AC-2). Any one
check true, or the brief naming no path at all, routes `full`; the grill runs as today. All four
false with at least one named path routes `light`; the grill short-circuits to one ticket.

A "named path" is a backtick-quoted, slash-bearing token in the brief body — the shape every
path reference in this repo's briefs, plans and tickets already uses. A bare filename with no
directory (`` `render_verdict.py` ``) cannot match a charter or interface glob anyway, so it is
not counted.
"""
import re
import sys
from pathlib import Path

import _fm
import schemas

CHARTER = "docs/domain-pack/charter.md"
INTERFACES = "docs/domain-pack/interfaces.md"

PATH_RE = re.compile(r"`([\w.-]+(?:/[\w.-]+)+)`")
BACKTICK_RE = re.compile(r"`([^`]+)`")
URL_RE = re.compile(r"https?://\S+")
ENV_VAR_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
PACKAGE_INSTALL_RE = re.compile(r"\b(?:pip|pip3|npm|yarn|pnpm|cargo|gem)\s+(?:install|add)\s+\S+")
PACKAGE_MANIFESTS = frozenset({
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "pyproject.toml", "Pipfile", "Pipfile.lock", "poetry.lock",
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "Gemfile", "Gemfile.lock", "composer.json",
})


def named_paths(body: str) -> tuple[str, ...]:
    """Backtick-quoted, slash-bearing tokens in the brief body, deduplicated, order kept."""
    return tuple(dict.fromkeys(m.group(1).rstrip(".,;:") for m in PATH_RE.finditer(body)))


def _package_hit(body: str) -> str:
    for m in BACKTICK_RE.finditer(body):
        token = m.group(1).rsplit("/", 1)[-1]
        if token in PACKAGE_MANIFESTS:
            return m.group(1)
    m = PACKAGE_INSTALL_RE.search(body)
    return m.group(0) if m else ""


def route(brief_text: str, charter: schemas.Charter, interfaces: schemas.Charter) -> schemas.Triage:
    """The route for one brief: check 1 (charter), check 2 (interfaces), check 3 (package, URL
    or env var named), check 4 (`partner_facing: true` in the brief's own frontmatter)."""
    fm, body = _fm.parse(brief_text)
    paths = named_paths(body)
    charter_hits = charter.reachable(paths)
    interface_hits = interfaces.reachable(paths)
    package_hit = _package_hit(body)
    url_m = URL_RE.search(body)
    env_m = ENV_VAR_RE.search(body)
    check3_detail = package_hit or (url_m.group(0) if url_m else "") or (env_m.group(0) if env_m else "")
    partner_facing = str(fm.get("partner_facing", "")).strip().lower() == "true"

    checks = (
        schemas.TriageCheck(1, "charter glob", bool(charter_hits), ", ".join(charter_hits)),
        schemas.TriageCheck(2, "interface glob", bool(interface_hits), ", ".join(interface_hits)),
        schemas.TriageCheck(3, "package, URL or env var", bool(check3_detail), check3_detail),
        schemas.TriageCheck(4, "partner-facing", partner_facing, "partner_facing: true" if partner_facing else ""),
    )
    if not paths or any(c.fired for c in checks):
        return schemas.Triage(route="full", checks=checks, paths=paths)
    return schemas.Triage(route="light", checks=checks, paths=paths)


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("brief")
    ap.add_argument("--cwd", default=".")
    a = ap.parse_args(argv[1:])
    root = Path(a.cwd).resolve()
    brief_path = root / a.brief
    charter_path = root / CHARTER
    interfaces_path = root / INTERFACES
    charter = schemas.Charter.parse(charter_path.read_text(encoding="utf-8")) if charter_path.exists() else schemas.Charter()
    interfaces = schemas.Charter.parse(interfaces_path.read_text(encoding="utf-8")) if interfaces_path.exists() else schemas.Charter()
    triage = route(brief_path.read_text(encoding="utf-8"), charter, interfaces)
    print(triage.render(), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
