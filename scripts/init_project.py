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
NEXT = "Next: write kanban/briefs/1-<slug>.md, then /gates:grill 1"


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


PLUGIN_NAME = "gates"
OLD_PLUGIN_NAMES = ("harness-plugin",)  # renamed in 0.10.0; --update migrates the pin and the enabled key
MARKETPLACE = "ludvignion"


def pinned_settings(root: Path, ref: str) -> str:
    """The repo's .claude/settings.json with the marketplace source moved to this plugin's repo
    and ``ref``, and an old plugin name under ``enabledPlugins`` replaced by the current one;
    the template's settings when the repo has none. Everything else in the file is kept."""
    path = root / SETTINGS
    text = path.read_text(encoding="utf-8") if path.exists() else (TEMPLATE / SETTINGS).read_text(encoding="utf-8")
    data = json.loads(fill(text, "", ref))
    source = data.setdefault("extraKnownMarketplaces", {}).setdefault(MARKETPLACE, {}).setdefault("source", {})
    source.update({"source": "github", "repo": f"{MARKETPLACE}/{PLUGIN_NAME}", "ref": ref})
    enabled = data.setdefault("enabledPlugins", {})
    for old in OLD_PLUGIN_NAMES:
        if f"{old}@{MARKETPLACE}" in enabled:
            enabled[f"{PLUGIN_NAME}@{MARKETPLACE}"] = enabled.pop(f"{old}@{MARKETPLACE}")
    enabled.setdefault(f"{PLUGIN_NAME}@{MARKETPLACE}", True)
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
        print(f"up to date with gates {ref}")
        print("Next: continue.")
        return 0
    for rel, current, wanted in diffs:
        lines = list(difflib.unified_diff(current.splitlines(keepends=True), wanted.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"))
        sys.stdout.writelines(lines or [f"--- a/{rel}\n+++ b/{rel}\n(new empty file)\n"])
    if not yes:
        print(f"\n{len(diffs)} file(s) differ; nothing written.")
        print("Next: /gates:init --update --yes")
        return 0
    for rel, _, wanted in diffs:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(wanted, encoding="utf-8")
    print(f"\nupdated: {' '.join(rel for rel, _, _ in diffs)}")
    print(f"Next: git commit -am 'chore: gates {ref}'")
    return 0


def preflight_issues(repo: str) -> list[str]:
    """What's missing before `init --issues <repo>` may write anything (plan 4 AC-13): `gh auth
    status`, and the repo has Issues enabled. Empty list means clear to proceed."""
    missing = []
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        missing.append("gh auth status")
        return missing
    api = subprocess.run(["gh", "api", f"repos/{repo}"], capture_output=True, text=True)
    if api.returncode != 0:
        missing.append(f"repo {repo} (gh api repos/{repo} failed)")
        return missing
    if not json.loads(api.stdout).get("has_issues"):
        missing.append(f"issues enabled on {repo}")
    return missing


def run_issues(root: Path, repo: str) -> int:
    """`init --issues <owner/repo>` (plan 4 AC-13/AC-14, this ticket AC-1/AC-2): on a clean
    preflight, write the marker, create the labels, git-ignore `kanban/tickets/`, migrate every
    file ticket to an issue and run the first sync — one commit, named after what's missing when
    the preflight refuses, and nothing written in that case."""
    if (root / "kanban" / ".issues").exists():
        print("refused: kanban/.issues already exists; this project already opted in", file=sys.stderr)
        return 1
    if missing := preflight_issues(repo):
        print(f"refused: missing {', '.join(missing)}; nothing written", file=sys.stderr)
        return 1
    import _fm  # local import, as in override_plan: keeps this importable from a bare python3
    import kanban_ops
    import migrate_tickets
    import tickets

    ticket_paths = [p for p, _, _ in _fm.tickets(root / "kanban")]
    (root / "kanban").mkdir(parents=True, exist_ok=True)
    (root / "kanban" / ".issues").write_text(repo + "\n", encoding="utf-8")
    tickets.ensure_labels(repo)
    gitignore = root / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if "kanban/tickets/" not in lines:
        lines.append("kanban/tickets/")
        gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mapping, changed_plans = migrate_tickets.migrate(root, repo)
    tickets.sync(root)
    message = f"chore: opt in to kanban/.issues ({repo})"
    if mapping:
        listing = ", ".join(f"{old} → #{new}" for old, new in sorted(mapping, key=lambda t: [int(x) for x in t[0].split(".")]))
        message += f"\n\nmigrate {len(mapping)} ticket(s): {listing}"
    paths = ["kanban/.issues", ".gitignore"]
    paths += [str(p.relative_to(root)) for p in changed_plans]
    paths += [str(p.relative_to(root)) for p in ticket_paths]
    kanban_ops.commit(root, paths, message)
    print(f"opted in to {repo}; migrated {len(mapping)} ticket(s); make sync: OK")
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
    commit = git(root, "commit", "-q", "-m", f"init from gates {ref}")
    if commit.returncode != 0:
        print(commit.stderr, file=sys.stderr)
        return 1
    print(f"make install, make ci: OK; committed 'init from gates {ref}'")
    print(NEXT)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create a project here, or --update its template files")
    p.add_argument("--name", help="package and project name (default: the directory name)")
    p.add_argument("--update", action="store_true", help="diff template-owned files against the repo")
    p.add_argument("--yes", action="store_true", help="with --update: write the differing files")
    p.add_argument("--issues", metavar="OWNER/REPO", help="opt in to kanban/.issues: preflight, marker, labels, migrate file tickets, first sync")
    p.add_argument("--cwd", default=".", help="the project root (default: .)")
    args = ap.parse_args(argv)
    top = git(Path(args.cwd).resolve(), "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        print(f"refused: {Path(args.cwd).resolve()} is not a git repo", file=sys.stderr)
        return 1
    root = Path(top.stdout.strip())
    try:
        if args.issues:
            return run_issues(root, args.issues)
        return run_update(root, f"v{plugin_version()}", args.yes) if args.update else run_init(root, args.name)
    except (ValueError, FileExistsError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
