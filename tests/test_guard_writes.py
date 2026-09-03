"""guard_writes.py: closed tickets are immutable; scope rule unchanged."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "kanban"
HOOK = REPO / "hooks" / "guard_writes.py"


def run_hook(cwd: Path, target: Path) -> subprocess.CompletedProcess:
    call = {"cwd": str(cwd), "tool_input": {"file_path": str(target)}}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(call), capture_output=True, text=True)


class GuardWritesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURE, self.tmp / "kanban")
        self.tickets = self.tmp / "kanban" / "tickets"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_done_ticket_is_blocked(self):
        res = run_hook(self.tmp, self.tickets / "1.1.tracer-bullet.md")
        self.assertEqual(res.returncode, 2)
        self.assertIn("ticket 1.1 is done", res.stderr)
        self.assertIn("depends_on: [1.1]", res.stderr)

    def test_superseded_ticket_is_blocked(self):
        res = run_hook(self.tmp, self.tickets / "2.2.input-validation.md")
        self.assertEqual(res.returncode, 2)

    def test_open_ticket_and_plan_are_allowed(self):
        self.assertEqual(run_hook(self.tmp, self.tickets / "1.2.match-stage.md").returncode, 0)
        self.assertEqual(run_hook(self.tmp, self.tmp / "kanban" / "plans" / "1.plan.md").returncode, 0)
        self.assertEqual(run_hook(self.tmp, self.tickets / "1.6.new-ticket.md").returncode, 0)

    def test_blocked_even_when_active_ticket_allows_kanban(self):
        (self.tmp / "kanban" / ".active").write_text("1.2")
        self.assertEqual(run_hook(self.tmp, self.tickets / "1.1.tracer-bullet.md").returncode, 2)
        self.assertEqual(run_hook(self.tmp, self.tickets / "1.2.match-stage.md").returncode, 0)

    def test_scope_rule_still_applies(self):
        (self.tmp / "kanban" / ".active").write_text("1.2")
        t = self.tickets / "1.2.match-stage.md"
        t.write_text(t.read_text().replace("writes: []", 'writes: ["src/match/"]'))
        self.assertEqual(run_hook(self.tmp, self.tmp / "src" / "match" / "a.py").returncode, 0)
        self.assertEqual(run_hook(self.tmp, self.tmp / "src" / "transform" / "a.py").returncode, 2)


if __name__ == "__main__":
    unittest.main()
