"""scripts/tickets.py: the marker gate, `make sync`'s mirror, the status-line property, and the
label bootstrap. `gh` is stubbed by tests/fixtures/fake_gh.py, as fake_claude.py stands in for
the runner tests."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
FAKE_GH = REPO / "tests" / "fixtures" / "fake_gh.py"
sys.path.insert(0, str(REPO / "scripts"))
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
        {"body": "### [human] 2026-09-14 09:00 — approved by ludvignion"},
    ],
}


class TicketsSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "kanban" / "tickets").mkdir(parents=True)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_GH} \"$@\"\n")
        gh.chmod(0o755)
        self.gh_log = self.tmp / "gh_calls.log"
        self.gh_data = self.tmp / "gh_data.json"
        self.env = mock.patch.dict(
            os.environ,
            {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_LOG": str(self.gh_log), "FAKE_GH_DATA": str(self.gh_data)},
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _calls(self) -> list[str]:
        return self.gh_log.read_text().splitlines() if self.gh_log.exists() else []

    def _write_issues(self, *issues: dict) -> None:
        self.gh_data.write_text(json.dumps({"issues": list(issues)}))

    def test_no_marker_makes_no_gh_call(self):
        written = tickets.sync(self.tmp)
        self.assertEqual(written, [])
        self.assertEqual(self._calls(), [])
        self.assertEqual(list((self.tmp / "kanban" / "tickets").iterdir()), [])

    def test_sync_mirrors_a_ticket_labelled_issue(self):
        (self.tmp / "kanban" / ".issues").write_text("ludvignion/gates\n")
        self._write_issues(ISSUE)
        written = tickets.sync(self.tmp)
        path = self.tmp / "kanban" / "tickets" / "42.tracer-bullet.md"
        self.assertEqual(written, [path])
        text = path.read_text()
        self.assertIn("id: 42", text)
        self.assertIn("status: ready", text)
        self.assertIn("parent: 4", text)
        self.assertIn("## Outcome\nOne sentence.", text)
        self.assertIn("- AC-1 (behavioral): x (plan 4 AC-1)", text)
        self.assertIn("## Out of scope\n- nothing", text)
        created = text.index("### [grill] 2026-09-14 08:08 — created")
        approved = text.index("### [human] 2026-09-14 09:00 — approved by ludvignion")
        self.assertLess(created, approved)

    def test_sync_makes_no_status_line_in_the_synced_body(self):
        (self.tmp / "kanban" / ".issues").write_text("ludvignion/gates\n")
        self._write_issues(ISSUE)
        tickets.sync(self.tmp)
        text = (self.tmp / "kanban" / "tickets" / "42.tracer-bullet.md").read_text()
        body_only = text.split("## Outcome", 1)[1]
        self.assertNotIn("status:", body_only)

    def test_sync_reports_a_status_line_in_the_source_body(self):
        (self.tmp / "kanban" / ".issues").write_text("ludvignion/gates\n")
        bad = {**ISSUE, "body": ISSUE["body"].replace("depends_on: []", "depends_on: []\nstatus: ready")}
        self._write_issues(bad)
        buf = tempfile.TemporaryFile(mode="w+")
        with mock.patch.object(sys, "stderr", buf):
            tickets.sync(self.tmp)
        buf.seek(0)
        self.assertIn("#42", buf.read())

    def test_sync_creates_ticket_and_status_labels(self):
        (self.tmp / "kanban" / ".issues").write_text("ludvignion/gates\n")
        self._write_issues(ISSUE)
        tickets.sync(self.tmp)
        creates = [c for c in self._calls() if c.startswith("label create")]
        named = {c.split()[2] for c in creates}
        self.assertEqual(named, {"ticket", "ready", "in_progress", "in_review", "done", "superseded"})
        self.assertTrue(all("--force" in c for c in creates))

    def test_sync_skips_issues_without_the_ticket_label(self):
        (self.tmp / "kanban" / ".issues").write_text("ludvignion/gates\n")
        other = {**ISSUE, "number": 7, "labels": ["bug"]}
        self._write_issues(ISSUE, other)
        written = tickets.sync(self.tmp)
        self.assertEqual([p.name for p in written], ["42.tracer-bullet.md"])


class MakefileTest(unittest.TestCase):
    def test_sync_target_runs_tickets_sync(self):
        text = (REPO / "templates" / "project" / "Makefile").read_text()
        self.assertRegex(text, r"\nsync:.*\n(?:\t.*\n)*\t.*tickets\.py sync\n")


if __name__ == "__main__":
    unittest.main()
