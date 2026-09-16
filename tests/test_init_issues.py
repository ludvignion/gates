"""scripts/init_project.py's `init --issues <owner/repo>` (plan 4 AC-13 first half / ticket 4.6
AC-1, AC-2): preflight, marker, labels, git-ignore, migration, first sync, one commit. A `gh`
failure partway through leaves the marker and `.gitignore` edit uncommitted, so a plain marker
check would refuse every retry forever; the fix and its tests are ticket 4.6.1 AC-1. `gh` stubbed
by tests/fixtures/fake_gh.py, as in test_tickets_sync.py and test_migrate_tickets.py.

The first sync runs as `make sync`, not `tickets.sync` in-process (ticket 4.6.2 AC-1, resolving
4.6 F3): the fixture project's Makefile touches a sentinel so the tests can tell the real target
ran, and a `gh` failure at that stage refuses the same way a failure during migrate does."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAKE_GH = REPO / "tests" / "fixtures" / "fake_gh.py"
SCRIPT = REPO / "scripts" / "init_project.py"

TICKET = """---
id: 4.1
parent: 4
status: ready
depends_on: []
writes: [a.py]
---

# 4.1 First slice

## Outcome
Do a thing.

## Acceptance criteria
- AC-1 (behavioral): Given X, When Y, Then Z

## Out of scope
- nothing

## Findings (append-only)

## Log (append-only)
### [grill] 2026-09-14 08:08 — created
"""

PLAN = """---
brief: 4
status: approved
approved: ludvignion 2026-09-14
---

# 4 Tickets live in GitHub Issues

## Slices
1. `4.1` — first slice.
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


class InitIssuesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "proj"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "tester")
        git(self.root, "config", "user.email", "tester@example.com")
        (self.root / "kanban" / "tickets").mkdir(parents=True)
        (self.root / "kanban" / "plans").mkdir(parents=True)
        (self.root / "kanban" / "tickets" / "4.1.first-slice.md").write_text(TICKET, encoding="utf-8")
        (self.root / "kanban" / "plans" / "4.plan.md").write_text(PLAN, encoding="utf-8")
        (self.root / ".gitignore").write_text("kanban/.active\nsync.marker\n", encoding="utf-8")
        (self.root / "Makefile").write_text(
            "PLUGIN ?= .\n"
            "SCRIPTS := $(PLUGIN)/scripts\n"
            "sync:\n"
            "\t@touch sync.marker\n"
            "\tpython3 $(SCRIPTS)/tickets.py sync\n",
            encoding="utf-8",
        )
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "seed")

        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_GH} \"$@\"\n")
        gh.chmod(0o755)
        self.gh_log = self.tmp / "gh_calls.log"
        self.gh_data = self.tmp / "gh_data.json"
        self.gh_data.write_text(json.dumps({"issues": []}))
        self.gh_env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_LOG": str(self.gh_log),
            "FAKE_GH_DATA": str(self.gh_data),
        }

    def _run(self, extra_data: dict | None = None) -> subprocess.CompletedProcess:
        if extra_data:
            self.gh_data.write_text(json.dumps({"issues": [], **extra_data}))
        return subprocess.run(
            [sys.executable, str(SCRIPT), "init", "--cwd", str(self.root), "--issues", "ludvignion/gates"],
            capture_output=True, text=True, env={**os.environ, **self.gh_env},
        )

    def test_refuses_when_gh_auth_fails_and_writes_nothing(self):
        proc = self._run({"auth_fail": True})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("gh auth status", proc.stderr)
        self.assertIn("nothing written", proc.stderr)
        self.assertFalse((self.root / "kanban" / ".issues").exists())
        self.assertEqual(git(self.root, "status", "--short"), "")

    def test_refuses_when_issues_are_disabled_and_writes_nothing(self):
        proc = self._run({"has_issues": False})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("issues enabled", proc.stderr)
        self.assertFalse((self.root / "kanban" / ".issues").exists())
        self.assertEqual(git(self.root, "status", "--short"), "")

    def test_refuses_when_the_repo_lookup_fails_and_writes_nothing(self):
        proc = self._run({"repo_fail": True})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("repo ludvignion/gates", proc.stderr)
        self.assertFalse((self.root / "kanban" / ".issues").exists())

    def test_opts_in_migrates_and_commits_once(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual((self.root / "kanban" / ".issues").read_text().strip(), "ludvignion/gates")
        self.assertIn("kanban/tickets/", (self.root / ".gitignore").read_text().splitlines())
        data = json.loads(self.gh_data.read_text())
        self.assertEqual(len(data["issues"]), 1)
        self.assertEqual(git(self.root, "status", "--short"), "")
        log = git(self.root, "log", "--format=%s%n%b", "-1")
        self.assertIn("opt in to kanban/.issues (ludvignion/gates)", log)
        self.assertIn(f"4.1 → #{data['issues'][0]['number']}", log)
        self.assertFalse((self.root / "kanban" / "tickets" / "4.1.first-slice.md").exists())
        mirrored = list((self.root / "kanban" / "tickets").glob(f"{data['issues'][0]['number']}.*.md"))
        self.assertEqual(len(mirrored), 1)  # the first sync (plan 4 AC-13)
        plan_text = (self.root / "kanban" / "plans" / "4.plan.md").read_text()
        self.assertIn(f"`#{data['issues'][0]['number']}` — first slice.", plan_text)
        creates = [c for c in self.gh_log.read_text().splitlines() if c.startswith("label create")]
        self.assertEqual({c.split()[2] for c in creates}, {"ticket", "ready", "in_progress", "in_review", "done", "superseded"})

    def test_refuses_when_already_opted_in(self):
        (self.root / "kanban" / ".issues").write_text("ludvignion/gates\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "opt in")
        proc = self._run()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("already opted in", proc.stderr)
        self.assertFalse(self.gh_log.exists())

    def test_resumes_after_a_gh_failure_mid_migrate_instead_of_refusing(self):
        self.gh_data.write_text(json.dumps({"issues": [], "fail_create_after": 0}))
        proc = self._run()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("gh issue create", proc.stderr)
        self.assertEqual((self.root / "kanban" / ".issues").read_text().strip(), "ludvignion/gates")
        self.assertIn("kanban/tickets/", (self.root / ".gitignore").read_text().splitlines())
        self.assertNotEqual(git(self.root, "status", "--short"), "")  # nothing committed
        self.assertTrue((self.root / "kanban" / "tickets" / "4.1.first-slice.md").exists())

        data = json.loads(self.gh_data.read_text())
        del data["fail_create_after"]
        self.gh_data.write_text(json.dumps(data))
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(git(self.root, "status", "--short"), "")
        self.assertEqual(len(json.loads(self.gh_data.read_text())["issues"]), 1)

    def test_first_sync_runs_make_sync_not_tickets_sync_in_process(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((self.root / "sync.marker").exists())  # only the Makefile's `sync` target writes this
        self.assertIn("make sync: OK", proc.stdout)

    def test_refuses_when_make_sync_fails(self):
        # A `## Log` entry makes migrate replay it via `tickets.log`, which itself calls
        # `gh issue view`; strip it so `fail_view` hits only sync's own call, after migrate
        # (create, replay, unlink) has cleanly finished — proving the refusal comes from
        # `make sync` itself, not from an earlier step.
        no_log_ticket = TICKET.split("## Log (append-only)")[0] + "## Log (append-only)\n"
        (self.root / "kanban" / "tickets" / "4.1.first-slice.md").write_text(no_log_ticket, encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "drop log entry for this test")

        proc = self._run({"fail_view": [1]})  # migrate succeeds (issue #1 created); sync's own gh call fails
        self.assertEqual(proc.returncode, 1)
        self.assertIn("gh issue view 1", proc.stderr)
        self.assertIn("re-run to retry", proc.stderr)
        self.assertEqual((self.root / "kanban" / ".issues").read_text().strip(), "ludvignion/gates")
        self.assertNotEqual(git(self.root, "status", "--short"), "")  # nothing committed


if __name__ == "__main__":
    unittest.main()
