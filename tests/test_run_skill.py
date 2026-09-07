"""skills/runner/SKILL.md is a wrapper: one background runner.py start, the state file relayed, the result
block printed, kanban_ops.py at Gate 2; the session edits nothing (E13, E14, E19).
kanban_ops.py's command line is board.act with the board's form."""
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
import board  # noqa: E402
import kanban_ops  # noqa: E402
import schemas  # noqa: E402
FIXTURE_PROJECT = REPO / "tests" / "fixtures" / "project"
from test_verdict_scripts import git  # noqa: E402

SKILL = REPO / "skills" / "runner" / "SKILL.md"
EDITING = re.compile(r"\b(edit|write|append|modify|rewrite|patch|sed)\b", re.I)


class RunSkillTextTest(unittest.TestCase):
    def test_names_the_two_scripts_and_no_editing_instruction(self):
        body = SKILL.read_text(encoding="utf-8")
        self.assertIn("runner.py", body)
        self.assertIn("kanban_ops.py", body)
        for tool in ("Edit(", "Write(", "MultiEdit", "append_log", "set_status", "## Log"):
            self.assertNotIn(tool, body, tool)
        offending = [l for l in body.splitlines() if EDITING.search(l)]
        self.assertEqual(offending, [], offending)
        for word in ("ship", "reject:", "child from F#", "home F# to", "waive F#:"):
            self.assertIn(word, body, word)

    def test_one_runner_invocation_and_no_other_command(self):
        """E13: the session never explores, builds or branches; one background start, Gate 2 through kanban_ops.py."""
        lines = SKILL.read_text(encoding="utf-8").splitlines()
        starts = [l for l in lines if "runner.py" in l]
        self.assertEqual(len(starts), 1, starts)
        self.assertIn("traces/runs/", starts[0])
        self.assertIn("&", starts[0])
        self.assertIn("--plan <n>", starts[0])
        self.assertIn("--override backend=runner", starts[0])
        for l in lines:
            if "python3 " in l and l is not starts[0]:
                self.assertIn("kanban_ops.py", l, l)
        body = "\n".join(lines)
        for banned in ("/build", "/verdict", "worktree", "git checkout"):
            self.assertNotIn(banned, body, banned)

    def test_result_format_and_refusals(self):
        """E19: the end of a run is the decision plus one line per finding with its action, all from the runner's result file."""
        body = SKILL.read_text(encoding="utf-8")
        for fmt in ("verdict: SHIP|REJECT", "Recommended:", "\u2192 child", "\u2192 home", "\u2192 waive", ".result", ".state"):
            self.assertIn(fmt, body, fmt)
        never = schemas.section(body, "Never")
        self.assertIn("session stamp", never)
        self.assertIn("dirty tree", never)
        self.assertIn("/harness-plugin:runner", body)

    def test_manifest_lists_the_skill(self):
        import json
        manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        self.assertIn("./skills/runner", manifest["skills"])
        self.assertNotIn("./skills/run", manifest["skills"])
        self.assertTrue(SKILL.exists())
        self.assertFalse((REPO / "skills" / "run").exists())


class KanbanOpsCliTest(unittest.TestCase):
    """A git copy of tests/fixtures/project, as test_board sets it up."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "proj"
        shutil.copytree(FIXTURE_PROJECT, self.root)
        (self.root / "docs" / "domain-pack").mkdir(parents=True)
        (self.root / "docs" / "domain-pack" / "charter.md").write_text("# Charter\n\n## 1. Unknown over guess\nx\n")
        (self.root / ".gitignore").write_text("traces/board.html\n")
        git(self.root, "init", "-q", "-b", "main"); git(self.root, "add", "-A"); git(self.root, "commit", "-q", "-m", "base")
        for k in ("OPIK_URL_OVERRIDE",):
            os.environ.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(REPO / "scripts" / "kanban_ops.py"), *args, "--cwd", str(self.root)],
                              capture_output=True, text=True)

    def test_order_and_usage(self):
        r = self._cli("order", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), board.plan_order(self.root, "1"))
        r = self._cli("waive", "1.2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("<ticket> <finding> <reason>", r.stderr)

    def test_reject_and_waive_are_the_board_path(self):
        r = self._cli("reject", "1.2", "unmatched", "path", "untested", "--who", "ada")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1.2 rejected", r.stdout)
        self.assertEqual(board.ticket_status(self.root, "1.2"), "in_progress")
        import _fm
        log = _fm.read(kanban_ops.find_ticket(self.root, "1.2"))[1]
        self.assertIn("### [human] ", log)
        self.assertIn("reject by ada (gate 2, from the board): unmatched path untested", log)
        r = self._cli("waive", "1.2", "F1", "known, next plan", "--who", "ada")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("F1 waived", r.stdout)
        self.assertIn("waive F1 by ada: known, next plan", _fm.read(kanban_ops.find_ticket(self.root, "1.2"))[1])
        r = self._cli("home", "1.2", "F1", "9.9")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no ticket 9.9", r.stderr)
