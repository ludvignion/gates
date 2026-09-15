"""The write path when a project opts into GitHub Issues (plan 4, ticket 4.2): kanban_ops.py's
`log`/`status` verbs, board.py's ship/reject/waive/home/child routed through tickets.py instead
of a ticket file, and the build/verdict skills naming those verbs. `gh` is stubbed by
tests/fixtures/fake_gh.py, as it is for tests/test_tickets_sync.py."""
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
import board  # noqa: E402
import kanban_ops  # noqa: E402
import runner  # noqa: E402
import schemas  # noqa: E402
import tickets  # noqa: E402
from test_verdict_scripts import git  # noqa: E402

ISSUE_42 = {
    "number": 42,
    "title": "Child fixture",
    "labels": ["ticket", "in_review"],
    "body": "---\nparent: 9\ndepends_on: []\nwrites: [\"src/\", \"tests/\"]\n---\n\n## Outcome\nx\n\n## Acceptance criteria\n- AC-1 (behavioral): x\n\n## Out of scope\n- nothing\n",
    "comments": [{"body": "### [grill] 2026-09-14 08:00 — created"}],
}


def verdict_json(tid: str) -> dict:
    return {
        "ticket": tid, "decision": "reject", "held": [],
        "findings": [
            {"id": "C1", "severity": "block", "status": "open", "ac": "AC-1", "charter": None, "home": None,
             "spawn_child": False, "covered_by": None, "repro": None, "waived_by": None, "text": "no test names AC-1"},
            {"id": "F1", "severity": "block", "status": "open", "ac": "AC-1", "charter": None, "home": None,
             "spawn_child": False, "covered_by": None, "repro": "python3 -c ...", "waived_by": None, "text": "AC-1 unmet: a thing"},
        ],
        "ci": {"green": True, "mutation_score": None},
        "meta": {"arm": "packet", "vendor": "claude", "plugin_version": "0.11.3"},
    }


class IssueModeBoardTest(unittest.TestCase):
    """A git repo opted into kanban/.issues: board actions write the issue, not a ticket file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "proj"
        (self.root / "kanban" / "tickets").mkdir(parents=True)
        (self.root / "kanban" / ".issues").write_text("ludvignion/gates\n")
        (self.root / ".gitignore").write_text("kanban/tickets/\ntraces/board.html\n")
        bin_dir = self.root / "bin"
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
        self.gh_data.write_text(json.dumps({"issues": [dict(ISSUE_42)]}))
        tickets.sync(self.root)
        (self.root / "traces" / "verdict").mkdir(parents=True)
        (self.root / "traces" / "verdict" / "42.json").write_text(json.dumps(verdict_json("42")))
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "add", "-A"); git(self.root, "commit", "-q", "-m", "base")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _calls(self) -> list[str]:
        return self.gh_log.read_text().splitlines() if self.gh_log.exists() else []

    def _issue(self, number: int) -> dict:
        return next(i for i in json.loads(self.gh_data.read_text())["issues"] if i["number"] == number)

    def _tracked(self) -> str:
        return subprocess.run(["git", "ls-files"], cwd=self.root, capture_output=True, text=True).stdout

    def test_child_then_waive_then_ship_writes_the_issue_not_a_file(self):
        line = board.child(self.root, "42", "F1", who="reviewer")
        self.assertIn("child #", line); self.assertIn("created from 42 F1", line)
        cid = line.split("child ")[1].split(" created")[0]
        self.assertTrue(cid.startswith("#"))
        child_number = int(cid[1:])
        created = self._issue(child_number)
        self.assertIn("depends_on: [#42]", created["body"]); self.assertIn("parent: 9", created["body"])
        v = schemas.Verdict.load(self.root / "traces/verdict/42.json")
        self.assertEqual(next(f for f in v.findings if f["id"] == "F1")["home"], cid)
        self.assertTrue(any(f"— child {cid} from F1" in c["body"] for c in self._issue(42)["comments"]))
        self.assertNotIn("kanban/tickets", self._tracked())  # AC-5: no ticket file committed

        with self.assertRaises(board.BoardError):
            board.ship(self.root, "42", who="reviewer")  # C1 still an open block, no waiver
        line = board.waive(self.root, "42", "C1", "goes to the child", who="reviewer")
        self.assertIn("42 C1 waived", line)
        self.assertTrue(any("waive C1 by reviewer: goes to the child" in c["body"] for c in self._issue(42)["comments"]))
        self.assertNotIn("kanban/tickets", self._tracked())

        line = board.ship(self.root, "42", who="reviewer")
        self.assertIn("42 shipped", line)
        issue = self._issue(42)
        self.assertEqual(sorted(issue["labels"]), ["done", "ticket"])
        self.assertEqual(issue["state"], "CLOSED")
        self.assertTrue(any("ship by reviewer (gate 2, from the board)" in c["body"] for c in issue["comments"]))
        self.assertNotIn("kanban/tickets", self._tracked())
        self.assertIn("traces/verdict/42.html", self._tracked())  # the page still commits

        with self.assertRaises(RuntimeError) as cm:
            board.reject(self.root, "42", "too late", who="reviewer")
        self.assertIn("#42", str(cm.exception)); self.assertIn("closed", str(cm.exception))

    def test_reject_swaps_the_label_with_no_ticket_file_commit(self):
        before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout
        line = board.reject(self.root, "42", "not yet", who="reviewer")
        self.assertIn("42 rejected", line)
        self.assertEqual(sorted(self._issue(42)["labels"]), ["in_progress", "ticket"])
        self.assertTrue(any("reject by reviewer (gate 2, from the board): not yet" in c["body"] for c in self._issue(42)["comments"]))
        after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout
        self.assertEqual(before, after)  # nothing tracked changed: no commit at all

    def test_home_logs_the_issue_with_no_ticket_file_commit(self):
        (self.root / "kanban" / "tickets" / "9.other.md").write_text(
            "---\nid: 9\nparent: 9\nstatus: ready\ndepends_on: []\nwrites: []\n---\n\n## Log (append-only)\n### [grill] 2026-09-14 08:00 — created\n"
        )
        line = board.home(self.root, "42", "C1", "9", who="reviewer")
        self.assertIn("42 C1 homed to 9", line)
        v = schemas.Verdict.load(self.root / "traces/verdict/42.json")
        self.assertEqual(next(f for f in v.findings if f["id"] == "C1")["home"], "9")
        self.assertTrue(any("C1 home: 9 (by reviewer)" in c["body"] for c in self._issue(42)["comments"]))
        self.assertNotIn("kanban/tickets", self._tracked())

    def test_orbit_logs_the_issue_with_no_ticket_file_commit_even_when_mirror_already_tracked(self):
        mirror = "kanban/tickets/42.child-fixture.md"
        git(self.root, "add", "-f", mirror)
        git(self.root, "commit", "-q", "-m", "legacy: mirror tracked before issue-mode opt-in")
        before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout
        runner.log_orbit(self.root, "42", ("$ echo probe",))
        self.assertTrue(any("orbit after close-out: $ echo probe" in c["body"] for c in self._issue(42)["comments"]))
        after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout
        self.assertEqual(before, after)  # AC-5: no ticket file commit, even for a mirror already tracked


class KanbanOpsTicketWriteTest(unittest.TestCase):
    """kanban_ops.py log/status: a file write without the marker, an issue write with it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "kanban" / "tickets").mkdir(parents=True)
        self.ticket = self.tmp / "kanban" / "tickets" / "1.1.tracer-bullet.md"
        self.ticket.write_text("---\nid: 1.1\nparent: 1\nstatus: ready\ndepends_on: []\nwrites: []\n---\n\n## Log (append-only)\n### [grill] 2026-09-14 08:00 — created\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_file_mode_log_and_status_edit_the_ticket(self):
        rc = kanban_ops.main(["kanban_ops.py", "log", "1.1", "build", "start", "--cwd", str(self.tmp)])
        self.assertEqual(rc, 0)
        self.assertIn("### [build] ", self.ticket.read_text()); self.assertIn("— start", self.ticket.read_text())
        rc = kanban_ops.main(["kanban_ops.py", "status", "1.1", "in_progress", "--cwd", str(self.tmp)])
        self.assertEqual(rc, 0)
        self.assertIn("status: in_progress", self.ticket.read_text())

    def test_log_takes_lines_as_separate_trailing_args(self):
        rc = kanban_ops.main(["kanban_ops.py", "log", "1.1", "verdict", "reject", "- block C1 AC-1: x", "- warn W1 AC-2: y", "--cwd", str(self.tmp)])
        self.assertEqual(rc, 0)
        text = self.ticket.read_text()
        self.assertIn("### [verdict] ", text); self.assertIn("— reject\n- block C1 AC-1: x\n- warn W1 AC-2: y", text)

    def test_issue_mode_log_and_status_route_through_tickets_py(self):
        (self.tmp / "kanban" / ".issues").write_text("ludvignion/gates\n")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_GH} \"$@\"\n")
        gh.chmod(0o755)
        gh_data = self.tmp / "gh_data.json"
        gh_data.write_text(json.dumps({"issues": [dict(ISSUE_42)]}))
        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_DATA": str(gh_data)}):
            rc = kanban_ops.main(["kanban_ops.py", "log", "42", "build", "start", "--cwd", str(self.tmp)])
            self.assertEqual(rc, 0)
            self.assertTrue(any("— start" in c["body"] for c in json.loads(gh_data.read_text())["issues"][0]["comments"]))
            rc = kanban_ops.main(["kanban_ops.py", "status", "42", "in_progress", "--cwd", str(self.tmp)])
            self.assertEqual(rc, 0)
            self.assertEqual(sorted(json.loads(gh_data.read_text())["issues"][0]["labels"]), ["in_progress", "ticket"])

    def test_unknown_ticket_refuses_in_one_line(self):
        rc = kanban_ops.main(["kanban_ops.py", "status", "9.9", "done", "--cwd", str(self.tmp)])
        self.assertEqual(rc, 1)


class SkillNamesTheVerbsTest(unittest.TestCase):
    """AC-6: the build and verdict skills name `kanban_ops.py log` and `kanban_ops.py status`."""

    def test_build_skill_names_log_and_status(self):
        text = (REPO / "skills" / "build" / "SKILL.md").read_text(encoding="utf-8")
        seq = schemas.section(text, "Sequence")
        self.assertIn("kanban_ops.py log", seq); self.assertIn("kanban_ops.py status", seq)

    def test_verdict_skill_names_log_and_status(self):
        text = (REPO / "skills" / "verdict" / "SKILL.md").read_text(encoding="utf-8")
        flow = schemas.section(text, "Ticket id")
        self.assertIn("kanban_ops.py log", flow); self.assertIn("kanban_ops.py status", flow)


if __name__ == "__main__":
    unittest.main()
