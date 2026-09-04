"""board.py: the actions a human used to do by hand, each through the code runner and lint use."""
import contextlib
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import board  # noqa: E402
import lint_kanban  # noqa: E402
import schemas  # noqa: E402
from test_verdict_scripts import git  # noqa: E402

FIXTURE_PROJECT = REPO / "tests" / "fixtures" / "project"


class BoardActionsTest(unittest.TestCase):
    """A git copy of tests/fixtures/project: plan 1 approved, 1.1 rejected (C1 block, no child),
    1.2 in_review rejected with F1 spawn_child."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "proj"
        shutil.copytree(FIXTURE_PROJECT, self.root)
        (self.root / "docs" / "domain-pack").mkdir(parents=True)
        (self.root / "docs" / "domain-pack" / "charter.md").write_text("# Charter\n\n## 1. Unknown over guess\nx\n\n## 2. Evidence is openable\nx\n")
        (self.root / "src" / "pipeline").mkdir(parents=True); (self.root / "src" / "pipeline" / "run.py").write_text("x = 1\n")
        (self.root / ".gitignore").write_text("traces/board.html\n")
        git(self.root, "init", "-q", "-b", "main"); git(self.root, "add", "-A"); git(self.root, "commit", "-q", "-m", "base")
        # the runner's result for 1.2: a branch with the work
        git(self.root, "checkout", "-q", "-b", "ticket/1.2")
        (self.root / "src" / "pipeline" / "match.py").write_text("def match():\n    return None\n")
        git(self.root, "add", "-A"); git(self.root, "commit", "-q", "-m", "feat(1.2): match")
        git(self.root, "checkout", "-q", "main")
        self.env = {k: "" for k in ("OPIK_URL_OVERRIDE",)}
        for k in self.env:
            os.environ.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _status(self, tid: str) -> str:
        return board.ticket_status(self.root, tid)

    def _log(self, tid: str) -> schemas.Log:
        import _fm
        return schemas.Log.parse(_fm.read(board.kanban_ops.find_ticket(self.root, tid))[1])

    def _subjects(self, ref: str = "HEAD") -> str:
        return subprocess.run(["git", "log", "--format=%s", ref], cwd=self.root, capture_output=True, text=True).stdout

    def test_approve_needs_stamp_and_writes_log(self):
        with self.assertRaises(board.BoardError):
            board.approve(self.root, "1", "reviewer")  # already approved
        draft = self.root / "kanban" / "plans" / "2.plan.md"
        draft.write_text("---\nbrief: 2\nstatus: draft\napproved:\n---\n# 2 second\n## Acceptance criteria\n- AC-1 (behavioral): x\n## Slices\n1. `2.1` — y\n")
        git(self.root, "add", "-A"); git(self.root, "commit", "-q", "-m", "plan 2 draft")
        with self.assertRaises(board.BoardError) as cm:
            board.approve(self.root, "2", "reviewer")
        self.assertIn("routing stamp", str(cm.exception))
        draft.write_text(draft.read_text().replace("approved:\n", "approved:\nsignals:\n  spend: false\n  partner_facing: false\n  parallel_ready: 0\n  tickets: 1\nscrutiny: light   # full iff spend or partner_facing\nbackend: session\n"))
        git(self.root, "add", "-A"); git(self.root, "commit", "-q", "-m", "stamped")
        line = board.approve(self.root, "2", "reviewer")
        self.assertIn("plan 2 approved by reviewer", line)
        text = draft.read_text()
        self.assertIn("status: approved", text); self.assertIn("approved: reviewer 20", text); self.assertIn("### [human] ", text); self.assertIn("— approved by reviewer", text)
        self.assertIn("docs(plan 2): approved by reviewer", self._subjects())
        self.assertEqual(lint_kanban.lint(self.root), [])

    def test_override_is_the_runner_path(self):
        line = board.override(self.root, "1", "backend", "runner", who="reviewer")
        self.assertIn("plan 1 backend session → runner", line)
        plan = (self.root / "kanban/plans/1.plan.md").read_text()
        self.assertIn("backend: runner", plan); self.assertIn("— router miss: backend session → runner", plan)
        self.assertIn('"field": "backend"', (self.root / "traces/grill-misses.jsonl").read_text())
        self.assertIn("docs(plan 1): router miss — backend session → runner", self._subjects())

    def test_ship_merges_and_records(self):
        with self.assertRaises(board.BoardError) as cm:
            board.ship(self.root, "1.2", who="reviewer")  # C1 is an open block without a child
        self.assertIn("C1", str(cm.exception))
        board.waive(self.root, "1.2", "C1", "AC-2 goes to the child", who="reviewer")
        line = board.ship(self.root, "1.2", who="reviewer")  # F1 spawns a child: shippable
        self.assertIn("1.2 shipped", line); self.assertIn("merged ticket/1.2 --no-ff into main", line)
        self.assertIn("no remote: not pushed", line); self.assertIn("deleted ticket/1.2", line)
        self.assertIn("dataset: skipped", line)
        self.assertEqual(self._status("1.2"), "done")
        self.assertEqual(subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.root, capture_output=True, text=True).stdout.strip(), "main")
        subjects = self._subjects()
        self.assertIn("merge(1.2): 1.2 match stage", subjects); self.assertIn("docs(1.2): ship — gate 2 by reviewer", subjects); self.assertIn("feat(1.2): match", subjects)
        self.assertEqual(subprocess.run(["git", "branch", "--list", "ticket/1.2"], cwd=self.root, capture_output=True, text=True).stdout.strip(), "")
        entry = self._log("1.2").entries[-1]
        self.assertEqual(entry.role, "human"); self.assertIn("ship by reviewer (gate 2, from the board)", entry.head); self.assertIn("- block F1 AC-2:", entry.text)
        self.assertTrue((self.root / "src/pipeline/match.py").exists())  # the merge brought the work

    def test_ship_refused_past_open_block_then_waive_then_ship(self):
        with self.assertRaises(board.BoardError) as cm:
            board.ship(self.root, "1.1")
        self.assertIn("C1", str(cm.exception))
        line = board.waive(self.root, "1.1", "C1", "AC-2 is covered by 1.2's tests", who="reviewer")
        self.assertIn("1.1 C1 waived", line)
        v = schemas.Verdict.load(self.root / "traces/verdict/1.1.json")
        self.assertTrue(v.findings[0]["waived_by"].startswith("reviewer 20"))
        self.assertIn("C1", self._log("1.1").waived_ids())
        self.assertIn("waived reviewer", (self.root / "traces/verdict/1.1.html").read_text())
        line = board.ship(self.root, "1.1")
        self.assertIn("1.1 shipped", line)
        self.assertEqual(lint_kanban.lint(self.root), [])  # no ship past an open block: the waiver names it

    def test_reject_with_reason(self):
        with self.assertRaises(board.BoardError):
            board.reject(self.root, "1.2", "  ")
        line = board.reject(self.root, "1.2", "unmatched path must be in this slice", who="reviewer")
        self.assertIn("1.2 rejected", line)
        self.assertEqual(subprocess.run(["git", "show", "ticket/1.2:kanban/tickets/1.2.match-stage.md"], cwd=self.root, capture_output=True, text=True).stdout.count("status: in_progress"), 1)
        self.assertIn("docs(1.2): reject — gate 2 by reviewer", self._subjects("ticket/1.2"))
        self.assertEqual(self._status("1.2"), "in_review")  # main is untouched until a merge

    def test_child_from_finding_end_to_end(self):
        line = board.child(self.root, "1.2", "F1", who="reviewer")
        self.assertIn("child 1.2.1 created from 1.2 F1", line)
        git(self.root, "checkout", "-q", "ticket/1.2")
        try:
            cpath = next((self.root / "kanban/tickets").glob("1.2.1.*.md"))
            text = cpath.read_text()
            fm, body = board._fm.parse(text)
            self.assertEqual((fm["id"], fm["parent"], fm["status"], fm["depends_on"], fm["writes"]), ("1.2.1", "1", "ready", ["1.2"], ["src/pipeline/match/", "tests/"]))
            self.assertIn("Resolve F1 from 1.2: AC-2 unmet", text)
            self.assertIn("- AC-1 (critical): AC-2 unmet: a record with no candidate returns None and is dropped, src/pipeline/match/run.py:6; its own slice — from 1.2 F1 (AC-2); repro: `python3 -c", text)
            self.assertIn("- everything else in 1.2", text)
            self.assertIn("### [human] ", text); self.assertIn("— created by reviewer from 1.2 F1", text)
            v = schemas.Verdict.load(self.root / "traces/verdict/1.2.json")
            self.assertEqual((v.findings[1]["spawn_child"], v.findings[1]["home"]), (True, "1.2.1"))
            self.assertIn("— child 1.2.1 from F1 by reviewer", self._log("1.2").entries[-1].head)
            self.assertIn("docs(1.2): child 1.2.1 from F1", self._subjects())
            self.assertIn("belongs to 1.2.1", (self.root / "traces/verdict/1.2.html").read_text())
            self.assertEqual(lint_kanban.lint(self.root), [])
            self.assertEqual(board.plan_order(self.root, "1"), ["1.1", "1.2", "1.2.1"])  # the child waits for its parent
            with self.assertRaises(board.BoardError):
                board.child(self.root, "1.2", "F9")
        finally:
            git(self.root, "checkout", "-q", "main")

    def test_home_warn(self):
        v = json.loads((self.root / "traces/verdict/1.1.json").read_text())
        line = board.home(self.root, "1.1", "F2", "1.2", who="reviewer")
        self.assertIn("1.1 F2 homed to 1.2", line)
        v = schemas.Verdict.load(self.root / "traces/verdict/1.1.json")
        self.assertEqual(v.findings[1]["home"], "1.2")
        entry = self._log("1.1").entries[-1]
        self.assertIn("— finding: ", entry.head); self.assertEqual(entry.role, "human")
        self.assertEqual(schemas.Finding.from_entry(entry, "1.1").homes, ("1.2",))
        self.assertEqual(lint_kanban.lint(self.root), [])
        with self.assertRaises(board.BoardError):
            board.home(self.root, "1.1", "F2", "9.9")

    def test_plan_order_and_dirty_refusal(self):
        self.assertEqual(board.plan_order(self.root, "1"), ["1.1", "1.2"])
        (self.root / "kanban/tickets/1.1.tracer-bullet.md").write_text("x")
        with self.assertRaises(board.BoardError):
            board.serve(self.root, 0)

    def test_http_round_trip(self):
        server = board.serve(self.root, 0)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("GET", "/"); r = c.getresponse(); page = r.read().decode()
            self.assertEqual(r.status, 200)
            self.assertIn("Plan 1", page); self.assertIn("gate 2 page", page); self.assertIn("ship, create child 1.2.1 from F1", page)
            self.assertIn('name=action value=ship', page); self.assertIn("scrutiny: light", page)
            c.request("GET", "/verdict/1.2"); r = c.getresponse(); self.assertEqual(r.status, 200); self.assertIn("REJECT — 1.2", r.read().decode())
            c.request("GET", "/verdict/9.9"); r = c.getresponse(); self.assertEqual(r.status, 404); r.read()
            body = urlencode({"action": "reject", "ticket": "1.2", "reason": "not yet", "who": "reviewer"})
            c.request("POST", "/action", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            r = c.getresponse(); self.assertEqual(r.status, 303); self.assertIn("1.2+rejected", r.getheader("Location")); r.read()
            self.assertIn("docs(1.2): reject — gate 2 by reviewer", self._subjects("ticket/1.2"))
            body = urlencode({"action": "ship", "ticket": "1.1"})
            c.request("POST", "/action", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            r = c.getresponse(); self.assertIn("refused", r.getheader("Location")); r.read()
            c.request("GET", "/progress?ticket=1.2"); r = c.getresponse(); self.assertEqual(r.status, 200); self.assertEqual(r.read().decode(), "")
        finally:
            server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
