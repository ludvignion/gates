"""render_board.py: Progress section counts, superseded column, and the Stop-hook no-op.

Fixture: tests/fixtures/kanban — two plans, eight tickets (done / in_review / ready /
superseded, plus one missing slice per plan). Generic names only. Run: python3 -m unittest
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "kanban"
sys.path.insert(0, str(REPO / "scripts"))
import render_board  # noqa: E402
import schemas  # noqa: E402


def _stop_hook_commands() -> list[str]:
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text())
    return [h["command"] for entry in hooks["hooks"]["Stop"] for h in entry["hooks"]]


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURE, self.tmp / "kanban")
        render_board.main(self.tmp)
        self.html = (self.tmp / "traces" / "board.html").read_text()
        rows = render_board._fm.tickets(self.tmp / "kanban")
        plans = {p.stem.split(".")[0]: render_board._fm.read(p) for p in (self.tmp / "kanban").rglob("*.plan.md")}
        self.records = {r["plan"]: r for r in render_board.progress(plans, rows)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plan_1_row_matches_hand_count(self):
        # Acceptance check from the originating project, hand-counted 2026-09-02.
        row = render_board.progress_row(self.records["1"])
        self.assertEqual(
            row,
            "Plan 1 — ACs reached 6/10 · slices 3/7 · tickets done 1, in_review 2, ready 3, missing 1",
        )
        self.assertIn(row, self.html)
        self.assertIn("missing: 1.6", self.html)

    def test_plan_1_two_shades(self):
        rec = self.records["1"]
        self.assertEqual(rec["acs_done"], 2)  # AC-1, AC-8 via 1.1 (done)
        self.assertEqual(rec["acs_review"], 4)  # AC-3, AC-4, AC-7, AC-10 via in_review tickets

    def test_plan_2_row_shows_superseded(self):
        rec = self.records["2"]
        self.assertEqual(
            render_board.progress_row(rec),
            "Plan 2 — ACs reached 2/3 · slices 1/3 · tickets done 1, in_review 0, ready 0, missing 1, superseded 1",
        )
        self.assertEqual(rec["missing"], ["2.3"])

    def test_ac_lines_list_tickets_and_best_status(self):
        acs = {ac: (ts, best) for ac, ts, best in self.records["1"]["acs"]}
        self.assertEqual(acs["AC-8"], ([("1.1", "done"), ("1.3", "in_review")], "done"))
        self.assertEqual(acs["AC-7"], ([("1.2", "in_review"), ("1.3", "in_review")], "in_review"))
        self.assertEqual(acs["AC-9"], ([("1.4", "ready")], "ready"))  # findings-section mention ignored
        self.assertEqual(len(self.records["1"]["acs"]), 10)
        self.assertIn("<b>AC-8</b> → 1.1 (done), 1.3 (in_review) · best: done", self.html)

    def test_superseded_column_visible(self):
        self.assertIn("superseded", render_board.STATUSES)
        self.assertIn("superseded (1)", self.html)
        self.assertIn("<b>2.2</b>", self.html)


class SchemaTest(unittest.TestCase):
    def test_citation_groups_parse_as_written(self):
        body = (
            "## Acceptance criteria\n"
            "- AC-1 (behavioral): x (plan 1 AC-3, AC-7 as amended by R-2)\n"
            "- AC-2 (critical): y (ADR-0002 §2, plan 1 AC-5) (unblocks plan 2 AC-2, AC-6)\n"
            "## Findings (append-only)\n- prose (plan 1 AC-9)\n"
        )
        c = schemas.TicketCitations.parse(body)
        self.assertEqual(c.acs_for("1"), {"AC-3", "AC-7", "AC-5"})
        self.assertEqual(c.acs_for("2"), {"AC-2", "AC-6"})

    def test_plan_slices_and_acs(self):
        plan = schemas.Plan.parse((FIXTURE / "plans" / "1.plan.md").read_text())
        self.assertEqual(plan.slices, ("1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"))
        self.assertEqual(len(plan.acs), 10)


class StopHookTest(unittest.TestCase):
    def test_hook_registered_after_trace_stop(self):
        cmds = _stop_hook_commands()
        self.assertTrue(cmds[0].endswith("hooks/trace_stop.py"))
        self.assertEqual(
            cmds[1], "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_board.py . >/dev/null 2>&1 || true"
        )

    def test_hook_is_noop_without_kanban(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO)}
            res = subprocess.run(["bash", "-c", _stop_hook_commands()[1]], cwd=tmp, env=env)
            self.assertEqual(res.returncode, 0)
            # and the script itself, without the `|| true` safety net
            res = subprocess.run([sys.executable, str(REPO / "scripts" / "render_board.py"), str(tmp)], capture_output=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(sorted(p.name for p in tmp.iterdir()), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_hook_renders_with_kanban(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(FIXTURE, tmp / "kanban")
            env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO)}
            res = subprocess.run(["bash", "-c", _stop_hook_commands()[1]], cwd=tmp, env=env)
            self.assertEqual(res.returncode, 0)
            self.assertTrue((tmp / "traces" / "board.html").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
