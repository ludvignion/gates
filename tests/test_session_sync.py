"""Plan 4 ticket 4.5: a collaborator's comment reaches the next session before a build reads the
ticket.

AC-1: the SessionStart startup hook and the build skill both run the sync before the ticket is
read; the build skill names the step (a test pins the sentence). AC-2: with no `kanban/.issues`
marker, the startup hook exits 0 at once with no `gh` call. `gh` is stubbed by
tests/fixtures/fake_gh.py, as it is for tests/test_tickets_sync.py.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
FAKE_GH = REPO / "tests" / "fixtures" / "fake_gh.py"
sys.path.insert(0, str(REPO / "scripts"))
import schemas  # noqa: E402
import tickets  # noqa: E402

ISSUE = {
    "number": 42,
    "title": "Tracer bullet",
    "labels": ["ticket", "ready"],
    "body": (
        "---\nparent: 4\ndepends_on: []\nwrites: []\n---\n\n"
        "## Outcome\nOne sentence.\n\n"
        "## Acceptance criteria\n- AC-1 (behavioral): x (plan 4 AC-1)\n\n"
        "## Out of scope\n- nothing\n"
    ),
    "comments": [
        {"body": "### [grill] 2026-09-14 08:08 — created"},
        {"body": "### [human] 2026-09-15 09:00 — a collaborator's comment"},
    ],
}


def _startup_command() -> str:
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for entry in hooks["hooks"]["SessionStart"]:
        if entry.get("matcher") == "startup":
            return entry["hooks"][0]["command"]
    raise AssertionError("hooks.json: no SessionStart startup hook")


class StartupHookTest(unittest.TestCase):
    """The wiring in hooks/hooks.json, run as the real shell command Claude Code would run."""

    def setUp(self):
        self.command = _startup_command()
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "kanban" / "tickets").mkdir(parents=True)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_GH} \"$@\"\n")
        gh.chmod(0o755)
        self.gh_log = self.tmp / "gh_calls.log"
        self.gh_data = self.tmp / "gh_data.json"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "CLAUDE_PLUGIN_ROOT": str(REPO),
            "FAKE_GH_LOG": str(self.gh_log),
            "FAKE_GH_DATA": str(self.gh_data),
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", self.command], cwd=self.tmp, env=self.env,
                               capture_output=True, text=True)

    def test_no_marker_exits_zero_at_once_with_no_gh_call(self):
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.gh_log.exists())
        self.assertEqual(list((self.tmp / "kanban" / "tickets").iterdir()), [])

    def test_marker_present_pulls_a_new_comment_into_the_mirror_before_read(self):
        (self.tmp / "kanban" / ".issues").write_text("ludvignion/gates\n")
        self.gh_data.write_text(json.dumps({"issues": [ISSUE]}))
        r = self._run()
        self.assertEqual(r.returncode, 0)
        mirror = self.tmp / "kanban" / "tickets" / f"42.{tickets.slug(ISSUE['title'])}.md"
        text = mirror.read_text(encoding="utf-8")
        self.assertIn("### [human] 2026-09-15 09:00 — a collaborator's comment", text)

    def test_a_gh_failure_still_exits_zero_so_a_session_can_start(self):
        (self.tmp / "kanban" / ".issues").write_text("ludvignion/gates\n")
        self.gh_data.write_text(json.dumps({"issues": [ISSUE], "fail_list": True}))
        r = self._run()
        self.assertEqual(r.returncode, 0)


class BuildSkillNamesTheSyncStepTest(unittest.TestCase):
    """AC-1: the build skill names the sync step, and it runs before the ticket is read."""

    def test_sequence_names_a_sync_step_before_the_ticket_is_read(self):
        text = (REPO / "skills" / "build" / "SKILL.md").read_text(encoding="utf-8")
        seq = schemas.section(text, "Sequence")
        self.assertIn("make sync", seq)
        self.assertIn("before the ticket is read", seq)
        sync_at = seq.index("Sync")
        branch_at = seq.index("Branch check")
        self.assertLess(sync_at, branch_at)


if __name__ == "__main__":
    unittest.main()
