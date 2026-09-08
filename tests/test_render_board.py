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


class RunsTest(unittest.TestCase):
    """The Runs section reads traces/runs/<id>.state (contract A) and the builder tail of the .log."""

    STATE = "10:42:07 +0:00 branch\n10:42:08 +0:01 ci-pre\n10:42:09 +0:02 build 1\n"
    LOG = ("[runner] opik: untraced (no OPIK_API_KEY)\n[runner 10:42:07 +0:00] branch\n[runner 10:42:09 +0:02] build 1\n"
           "  reading the ticket\n  wrote src/pipeline/match.py\n  ran tests: 3 passed\n  wrote tests/test_match.py\n"
           "  ran tests: 4 passed\n  committing\n")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURE, self.tmp / "kanban")
        self.runs = self.tmp / "traces" / "runs"
        self.runs.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render(self) -> str:
        render_board.main(self.tmp)
        return (self.tmp / "traces" / "board.html").read_text()

    def test_running_ticket_shows_phase_elapsed_tail_and_refresh(self):
        (self.runs / "1.2.state").write_text(self.STATE)
        (self.runs / "1.2.log").write_text(self.LOG)
        (self.runs / "plan-1.state").write_text("10:42:07 +0:00 ticket 1.2\n" + self.STATE)  # plan walks are not rows
        recs = render_board.runs(self.tmp)
        self.assertEqual(len(recs), 1)
        self.assertEqual({k: recs[0][k] for k in ("id", "phase", "elapsed", "running")}, {"id": "1.2", "phase": "build 1", "elapsed": "+0:02", "running": True})
        self.assertEqual(recs[0]["lines"], ["wrote src/pipeline/match.py", "ran tests: 3 passed", "wrote tests/test_match.py", "ran tests: 4 passed", "committing"])
        page = self._render()
        self.assertIn('<meta charset=utf-8><meta http-equiv="refresh" content="5">', page)
        self.assertIn("<h2>Runs</h2>", page)
        self.assertIn("<b>1.2</b> · build 1 · +0:02", page)
        self.assertIn("wrote src/pipeline/match.py\nran tests: 3 passed\nwrote tests/test_match.py\nran tests: 4 passed\ncommitting\n", page)
        self.assertNotIn("reading the ticket", page)  # only the last 5 builder lines
        self.assertLess(page.index("<h2>Progress</h2>"), page.index("<h2>Runs</h2>")); self.assertLess(page.index("<h2>Runs</h2>"), page.index("<h2>Tickets</h2>"))

    def test_phase_detail_and_log_fallbacks(self):
        (self.runs / "1.3.state").write_text(self.STATE + "10:44:31 +2:24 build 1 close-out\n")
        (self.runs / "1.3.log").write_text("[runner] opik: untraced (no OPIK_API_KEY)\n[runner 10:42:07 +0:00] branch\n")
        (self.runs / "1.4.state").write_text("10:50:00 +0:00 branch\n")
        recs = {r["id"]: r for r in render_board.runs(self.tmp)}
        self.assertEqual((recs["1.3"]["phase"], recs["1.3"]["elapsed"]), ("build 1 close-out", "+2:24"))
        self.assertEqual(recs["1.3"]["lines"], ["[runner] opik: untraced (no OPIK_API_KEY)", "[runner 10:42:07 +0:00] branch"])  # no builder lines: the log's tail
        self.assertEqual(recs["1.4"]["lines"], [])  # no log yet
        self.assertIn("<b>1.3</b> · build 1 close-out · +2:24", self._render())

    def test_heartbeat_line_is_the_phase(self):
        """Contract B: the runner's heartbeat is a state line like any other; its phase text is
        everything after the elapsed field, and the run counts as running."""
        (self.runs / "1.2.state").write_text(self.STATE + "10:42:39 +0:32 build 1 · running · last 10:42:07 · $ pytest -q\n")
        recs = render_board.runs(self.tmp)
        self.assertEqual({k: recs[0][k] for k in ("phase", "elapsed", "running")}, {"phase": "build 1 · running · last 10:42:07 · $ pytest -q", "elapsed": "+0:32", "running": True})
        page = self._render()
        self.assertIn("<b>1.2</b> · build 1 · running · last 10:42:07 · $ pytest -q · +0:32", page)
        self.assertIn('http-equiv="refresh"', page)

    def test_finished_run_is_not_a_row_and_no_refresh(self):
        (self.runs / "1.2.state").write_text(self.STATE + "10:51:00 +8:53 done ship\n")
        (self.runs / "1.2.log").write_text(self.LOG)
        recs = render_board.runs(self.tmp)
        self.assertEqual((recs[0]["running"], recs[0]["phase"]), (False, "done ship"))
        page = self._render()
        self.assertNotIn("http-equiv", page)
        self.assertIn("<h2>Runs</h2><p>nothing running</p>", page)
        self.assertNotIn("<b>1.2</b> · ", page)

    def test_no_runs_directory(self):
        shutil.rmtree(self.runs)
        self.assertEqual(render_board.runs(self.tmp), [])
        page = self._render()
        self.assertNotIn("http-equiv", page); self.assertIn("nothing running", page)


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


class StampTest(unittest.TestCase):
    def test_gate_1_shows_stamp_and_rule(self):
        text = (FIXTURE / "plans" / "1.plan.md").read_text()
        st = schemas.RoutingStamp.parse(text)
        self.assertEqual((st.scrutiny, st.backend), ("light", "session"))
        self.assertEqual(st.rule("scrutiny"), "full iff spend or partner_facing")
        self.assertEqual(dict(st.values)["spend"], "false")
        line = render_board.stamp_line(text)
        self.assertIn("scrutiny: light (full iff spend or partner_facing)", line)
        self.assertIn("signals spend=false", line)
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(FIXTURE, tmp / "kanban")
            render_board.main(tmp, tmp / "elsewhere" / "board.html")
            self.assertIn("scrutiny: light (full iff spend or partner_facing)", (tmp / "elsewhere" / "board.html").read_text())
            self.assertFalse((tmp / "traces" / "board.html").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
