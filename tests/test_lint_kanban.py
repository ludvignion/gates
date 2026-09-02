"""lint_kanban.py: a closed ticket may only grow its append-only sections."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "kanban"
sys.path.insert(0, str(REPO / "scripts"))
import lint_kanban  # noqa: E402
import schemas  # noqa: E402


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class LintKanbanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURE, self.tmp / "kanban")
        git(self.tmp, "init", "-q")
        git(self.tmp, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        git(self.tmp, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "fixture")
        self.done = self.tmp / "kanban" / "tickets" / "1.1.tracer-bullet.md"
        self.ready = self.tmp / "kanban" / "tickets" / "1.4.merge-stage.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean_tree_passes(self):
        self.assertEqual(lint_kanban.lint(self.tmp), [])

    def test_ac_edit_on_done_ticket_fails(self):
        self.done.write_text(self.done.read_text().replace("(plan 1 AC-1)", "(plan 1 AC-1, AC-2)"))
        v = lint_kanban.lint(self.tmp)
        self.assertEqual(len(v), 1)
        self.assertIn("ticket 1.1 is done; ## Acceptance criteria changed", v[0])

    def test_status_change_on_done_ticket_fails(self):
        self.done.write_text(self.done.read_text().replace("status: done", "status: ready"))
        self.assertTrue(any("frontmatter changed" in v for v in lint_kanban.lint(self.tmp)))

    def test_append_to_log_passes_but_rewrite_fails(self):
        self.done.write_text(self.done.read_text() + "### [human] 2026-09-02 — note\nappended\n")
        self.assertEqual(lint_kanban.lint(self.tmp), [])
        self.done.write_text(self.done.read_text().replace("— created", "— rewritten"))
        self.assertTrue(any("append-only" in v for v in lint_kanban.lint(self.tmp)))

    def test_open_ticket_edits_pass(self):
        self.ready.write_text(self.ready.read_text().replace("(plan 1 AC-9)", "(plan 1 AC-9, AC-4)"))
        self.assertEqual(lint_kanban.lint(self.tmp), [])

    def test_deleting_done_ticket_fails(self):
        self.done.unlink()
        self.assertTrue(any("was deleted" in v for v in lint_kanban.lint(self.tmp)))

    def test_cli_exit_code(self):
        self.done.write_text(self.done.read_text().replace("One sentence.", "Two sentences."))
        res = subprocess.run([sys.executable, str(REPO / "scripts" / "lint_kanban.py")], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("## Outcome changed", res.stdout)


class ClosedTicketDiffTest(unittest.TestCase):
    def test_identical_is_legal(self):
        t = "---\nid: 1.1\nstatus: done\n---\n# x\n## Log (append-only)\na\n"
        self.assertEqual(schemas.ClosedTicketDiff.compare(t, t).violations, ())


if __name__ == "__main__":
    unittest.main()
