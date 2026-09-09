"""Create a project from the template the plugin ships in ``templates/project/``, or bring an
existing project's template files up to this plugin's version.

``init [--name <n>]`` in a git repo with no tracked files: copies the template, fills the
placeholders (``{{project_name}}`` from --name or the directory, ``{{plugin_ref}}`` from this
plugin's own version), runs ``make install && make ci``, commits, prints the Next line.
``init --update`` diffs the template-owned files (Makefile, .gitignore, .env.example, the CI
workflow, docs/spec/.gitkeep, the plugin pin in .claude/settings.json) against the repo and
applies with --yes. It never touches kanban/, docs/ (beyond that .gitkeep), src/, tests/ or
traces/: those are the project's. Both make calls of init run with PLUGIN set to this plugin's
own root, so the template's ci target finds the scripts of the version that created the project.
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = PLUGIN_ROOT / "templates" / "project"
SETTINGS = ".claude/settings.json"
UPDATE_SET = ("Makefile", ".gitignore", ".env.example", ".github/workflows/ci.yml", "docs/spec/.gitkeep")
PROJECT_OWNED = ("kanban", "docs", "src", "tests", "traces")
NEXT = "Next: write kanban/briefs/1-<slug>.md, then /harness-plugin:grill 1"


def plugin_version() -> str:
    return json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


def package_name(raw: str) -> str:
    """A python identifier from --name or the directory name: lower, ``-`` and spaces to ``_``."""
    name = re.sub(r"[^a-z0-9_]", "", re.sub(r"[-\s]+", "_", raw.strip().lower()))
    if not name.isidentifier():
        raise ValueError(f"{raw!r} is not a package name; pass --name <identifier>")
    return name


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def template_files() -> list[Path]:
    return sorted(p.relative_to(TEMPLATE) for p in TEMPLATE.rglob("*") if p.is_file())


def fill(text: str, name: str, ref: str) -> str:
    return text.replace("{{project_name}}", name).replace("{{plugin_ref}}", ref)


def copy_template(root: Path, name: str, ref: str) -> list[Path]:
    """Write every template file under root with the placeholders filled, in paths too.
    Refuses before writing anything when a destination already exists."""
    plan = [(rel, root / fill(str(rel), name, ref)) for rel in template_files()]
    if clashes := [dest for _, dest in plan if dest.exists()]:
        raise FileExistsError("exists: " + " ".join(str(d.relative_to(root)) for d in clashes))
    for rel, dest in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fill((TEMPLATE / rel).read_text(encoding="utf-8"), name, ref).encode("utf-8"))
    return [dest.relative_to(root) for _, dest in plan]


def pinned_settings(root: Path, ref: str) -> str:
    """The repo's .claude/settings.json with only the marketplace ref moved to ``ref``; the
    template's settings when the repo has none."""
    path = root / SETTINGS
    text = path.read_text(encoding="utf-8") if path.exists() else (TEMPLATE / SETTINGS).read_text(encoding="utf-8")
    data = json.loads(fill(text, "", ref))
    data.setdefault("extraKnownMarketplaces", {}).setdefault("ludvignion", {}).setdefault("source", {})["ref"] = ref
    return json.dumps(data, indent=2) + "\n"


def update_diffs(root: Path, ref: str) -> list[tuple[str, str, str]]:
    """(path, current, wanted) for every template-owned file that differs from the template."""
    out = []
    for rel in UPDATE_SET:
        wanted = fill((TEMPLATE / rel).read_text(encoding="utf-8"), "", ref)
        current = (root / rel).read_text(encoding="utf-8") if (root / rel).exists() else None
        if current != wanted:  # a missing file always differs, an empty .gitkeep included
            out.append((rel, current or "", wanted))
    wanted = pinned_settings(root, ref)
    current = (root / SETTINGS).read_text(encoding="utf-8") if (root / SETTINGS).exists() else ""
    if current != wanted:
        out.append((SETTINGS, current, wanted))
    return out


def run_update(root: Path, ref: str, yes: bool) -> int:
    diffs = update_diffs(root, ref)
    if not diffs:
        print(f"up to date with harness-plugin {ref}")
        print("Next: continue.")
        return 0
    for rel, current, wanted in diffs:
        lines = list(difflib.unified_diff(current.splitlines(keepends=True), wanted.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"))
        sys.stdout.writelines(lines or [f"--- a/{rel}\n+++ b/{rel}\n(new empty file)\n"])
    if not yes:
        print(f"\n{len(diffs)} file(s) differ; nothing written.")
        print("Next: /harness-plugin:init --update --yes")
        return 0
    for rel, _, wanted in diffs:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(wanted, encoding="utf-8")
    print(f"\nupdated: {' '.join(rel for rel, _, _ in diffs)}")
    print(f"Next: git commit -am 'chore: harness-plugin {ref}'")
    return 0


def run_init(root: Path, name: str | None) -> int:
    version = plugin_version()
    ref = f"v{version}"
    if tracked := git(root, "ls-files").stdout.split():
        print(f"refused: {root} has {len(tracked)} tracked files; init is for an empty repo. "
              "To refresh the template files of an existing project: init --update", file=sys.stderr)
        return 1
    if not git(root, "config", "user.name").stdout.strip():
        print("refused: git user.name is unset; set it before init", file=sys.stderr)
        return 1
    pkg = package_name(name or root.name)
    written = copy_template(root, pkg, ref)
    print(f"wrote {len(written)} files for package {pkg}, plugin pinned to {ref}")
    for target in ("install", "ci"):
        proc = subprocess.run(["make", target], cwd=root, capture_output=True, text=True,
                              env={**os.environ, "PLUGIN": str(PLUGIN_ROOT)})
        if proc.returncode != 0:
            print(proc.stdout[-2000:] + proc.stderr[-2000:], file=sys.stderr)
            print(f"refused: make {target} failed; nothing committed", file=sys.stderr)
            return 1
    git(root, "add", "-A")
    commit = git(root, "commit", "-q", "-m", f"init from harness-plugin {ref}")
    if commit.returncode != 0:
        print(commit.stderr, file=sys.stderr)
        return 1
    print(f"make install, make ci: OK; committed 'init from harness-plugin {ref}'")
    print(NEXT)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create a project here, or --update its template files")
    p.add_argument("--name", help="package and project name (default: the directory name)")
    p.add_argument("--update", action="store_true", help="diff template-owned files against the repo")
    p.add_argument("--yes", action="store_true", help="with --update: write the differing files")
    p.add_argument("--cwd", default=".", help="the project root (default: .)")
    args = ap.parse_args(argv)
    top = git(Path(args.cwd).resolve(), "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        print(f"refused: {Path(args.cwd).resolve()} is not a git repo", file=sys.stderr)
        return 1
    root = Path(top.stdout.strip())
    try:
        return run_update(root, f"v{plugin_version()}", args.yes) if args.update else run_init(root, args.name)
    except (ValueError, FileExistsError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
