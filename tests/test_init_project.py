"""scripts/init_project.py: init in an empty repo copies the shipped template, fills the placeholders,
pins the plugin to its own version and commits after a green make ci; refuses a non-empty repo;
--update shows a diff and writes only with --yes, never under the project-owned directories."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import init_project  # noqa: E402

SCRIPT = REPO / "scripts" / "init_project.py"
VERSION = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())["version"]


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def empty_repo(tmp: Path, name: str) -> Path:
    root = tmp / name
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "tester")
    git(root, "config", "user.email", "tester@example.com")
    return root


def run(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "init", "--cwd", str(root), *args],
                          capture_output=True, text=True, env={**os.environ, **(env or {})})


def tree_hash(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


class TemplateTest(unittest.TestCase):
    def test_placeholders_are_the_two_named_and_nothing_else(self):
        files = init_project.template_files()
        self.assertGreaterEqual(len(files), 24, files)
        self.assertIn(Path("src/{{project_name}}/__init__.py"), files)
        self.assertNotIn(Path("uv.lock"), files)
        settings = (init_project.TEMPLATE / ".claude/settings.json").read_text()
        self.assertIn('"ref": "{{plugin_ref}}"', settings)
        pyproject = (init_project.TEMPLATE / "pyproject.toml").read_text()
        self.assertIn('name = "{{project_name}}"', pyproject)
        for rel in files:  # no third placeholder; ci.yml keeps GitHub's own ${{ github.* }}
            text = init_project.fill((init_project.TEMPLATE / rel).read_text(), "", "")
            self.assertNotRegex(text, r"\{\{(?! github)", rel)
        for rel in init_project.UPDATE_SET:
            self.assertNotIn("{{project_name}}", (init_project.TEMPLATE / rel).read_text(), rel)
        makefile = (init_project.TEMPLATE / "Makefile").read_text()
        self.assertIn("plugin:", makefile)
        self.assertNotIn("mismatch", makefile)
        self.assertIn("ci: lint test complexity kanban coverage", makefile)  # 0.8.0: coverage.py in ci; 0.8.1: lint_kanban.py too
        self.assertIn("spec_intake.py $(F)", makefile); self.assertIn("coverage.py .", makefile); self.assertIn("lint_kanban.py $(BASE)", makefile)
        self.assertIn("BASE ?= HEAD", makefile)
        self.assertIn("env: { BASE: ", (init_project.TEMPLATE / ".github/workflows/ci.yml").read_text())
        self.assertIn(Path("docs/spec/.gitkeep"), files); self.assertIn(Path("kanban/briefs/.gitkeep"), files)
        self.assertNotIn(Path("kanban/briefs/0-example.md"), files)  # the lint reads nothing from it; templates/brief.md is the example
        self.assertIn("docs/spec/.gitkeep", init_project.UPDATE_SET)

    def test_package_name(self):
        self.assertEqual(init_project.package_name("My-Proj 2"), "my_proj_2")
        with self.assertRaises(ValueError):
            init_project.package_name("2nd")


class InitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_init_in_an_empty_repo_pins_fills_and_commits_after_green_ci(self):
        if not shutil.which("uv") or not shutil.which("make"):
            self.skipTest("uv or make not installed")
        root = empty_repo(self.tmp, "demo-proj")
        proc = run(root, env={"UV_PROJECT_ENVIRONMENT": str(self.tmp / "venv")})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout.rstrip().splitlines()[-1], init_project.NEXT)
        self.assertEqual(git(root, "log", "--format=%s"), f"init from harness-plugin v{VERSION}\n")
        self.assertEqual(git(root, "status", "--short"), "")
        tracked = git(root, "ls-files").split()
        self.assertIn("src/demo_proj/__init__.py", tracked)
        self.assertIn("uv.lock", tracked)
        for rel in tracked:
            self.assertNotIn("{{p", (root / rel).read_text(), rel)
        settings = json.loads((root / ".claude/settings.json").read_text())
        self.assertEqual(settings["extraKnownMarketplaces"]["ludvignion"]["source"]["ref"], f"v{VERSION}")
        self.assertIn('name = "demo_proj"', (root / "pyproject.toml").read_text())
        ci = subprocess.run(["make", "ci"], cwd=root, capture_output=True, text=True,
                            env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(self.tmp / "venv"), "PLUGIN": str(REPO)})
        self.assertEqual(ci.returncode, 0, ci.stdout + ci.stderr)
        self.assertIn("coverage.py", ci.stdout); self.assertNotIn("coverage:", ci.stdout)  # no spec: silent
        self.assertIn("lint_kanban: 0 violation(s) against HEAD", ci.stdout)

    def test_refuses_a_repo_with_tracked_files(self):
        root = empty_repo(self.tmp, "busy")
        (root / "a.txt").write_text("x\n")
        git(root, "add", "a.txt")
        git(root, "commit", "-q", "-m", "a")
        proc = run(root)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--update", proc.stderr)
        self.assertEqual(git(root, "ls-files").split(), ["a.txt"])
        self.assertFalse((root / "Makefile").exists())

    def test_refuses_when_a_destination_exists(self):
        root = empty_repo(self.tmp, "clash")
        (root / "Makefile").write_text("mine\n")
        proc = run(root)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("exists: Makefile", proc.stderr)
        self.assertEqual((root / "Makefile").read_text(), "mine\n")
        self.assertFalse((root / "pyproject.toml").exists())

    def test_update_shows_the_diff_and_writes_only_with_yes(self):
        root = empty_repo(self.tmp, "older")
        init_project.copy_template(root, "older", "v0.0.1")
        makefile = re.sub(r"intake:.*?(?=ci: )", "", (root / "Makefile").read_text(), flags=re.S)  # a 0.7.0 Makefile: no intake, kanban, coverage
        makefile = makefile.replace("ci: lint test complexity kanban coverage", "ci: lint test complexity").replace(" intake kanban coverage ci ", " ci ")
        self.assertNotIn("coverage", makefile); self.assertNotIn("kanban:", makefile); self.assertIn("\nci: lint test complexity  ##", makefile)
        (root / "Makefile").write_text(makefile + "\n# local line\n")
        shutil.rmtree(root / "docs/spec")  # a 0.7.0 project: no docs/spec/
        (root / "kanban/briefs/1-x.md").write_text("# Brief\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "old")
        before = tree_hash(root)

        proc = run(root, "--update")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--- a/Makefile", proc.stdout)
        self.assertIn("-# local line", proc.stdout)
        self.assertIn("+ci: lint test complexity kanban coverage", proc.stdout)
        self.assertIn("+coverage:", proc.stdout); self.assertIn("+kanban:", proc.stdout)
        self.assertIn("--- a/docs/spec/.gitkeep", proc.stdout)
        self.assertIn("--- a/.claude/settings.json", proc.stdout)
        self.assertIn(f'+        "ref": "v{VERSION}"', proc.stdout)
        self.assertIn("nothing written", proc.stdout)
        self.assertEqual(tree_hash(root), before)

        proc = run(root, "--update", "--yes")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("updated: Makefile docs/spec/.gitkeep .claude/settings.json", proc.stdout)
        after = tree_hash(root)
        changed = {k for k in before if before[k] != after.get(k)}
        self.assertEqual(changed, {"Makefile", ".claude/settings.json"})
        self.assertEqual(set(after) - set(before), {"docs/spec/.gitkeep"})
        self.assertEqual((root / "Makefile").read_text(), (init_project.TEMPLATE / "Makefile").read_text())
        settings = json.loads((root / ".claude/settings.json").read_text())
        self.assertEqual(settings["extraKnownMarketplaces"]["ludvignion"]["source"]["ref"], f"v{VERSION}")
        self.assertEqual(settings["permissions"]["allow"][0], "Bash(make *)")
        for owned in init_project.PROJECT_OWNED:
            self.assertTrue(all(before[k] == after[k] for k in before if k.startswith(owned + "/")), owned)

        proc = run(root, "--update")
        self.assertIn(f"up to date with harness-plugin v{VERSION}", proc.stdout)


class InitSkillTest(unittest.TestCase):
    def test_thin_wrapper_over_the_script(self):
        text = (REPO / "skills" / "init" / "SKILL.md").read_text(encoding="utf-8")
        lines = [l for l in text.split("---", 2)[2].splitlines() if l.strip()]
        self.assertLessEqual(len(lines), 12, lines)
        self.assertEqual(len([l for l in lines if "python3 " in l]), 1)
        self.assertIn("init_project.py init", text)
        for word in ("--name", "--update", "--yes"):
            self.assertIn(word, text, word)
        self.assertEqual(lines[-1], init_project.NEXT)
        manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        self.assertIn("./skills/init", manifest["skills"])


if __name__ == "__main__":
    unittest.main()
