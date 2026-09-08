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
WATCH = "sleep 20; tail -n +<K> traces/runs/<id>.state"  # the skill's watch command, verbatim
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
        for fmt in ("SHIP|REJECT", "\u2192 child from", "\u2192 home", "waive F", ".result", ".state"):
            self.assertIn(fmt, body, fmt)
        never = schemas.section(body, "Never")
        self.assertIn("session stamp", never)
        self.assertIn("dirty tree", never)
        self.assertIn("/harness-plugin:runner", body)

    def test_watch_is_a_repeated_short_tail_and_the_result_shape_is_named(self):
        """E26: the Bash tool returns output at exit, so the watch is `sleep 20; tail -n +<K>` repeated;
        the result block's lines and the refusals are named; one Next line (J)."""
        body = SKILL.read_text(encoding="utf-8")
        lines = body.splitlines()
        start = next(l for l in lines if "runner.py" in l)
        self.assertIn("rm -f traces/runs/<id>.state", start)
        tails = [l for l in lines if l.strip().startswith("tail ") or "; tail " in l]
        self.assertEqual(len(tails), 1, tails)
        self.assertEqual(tails[0].strip(), WATCH)
        self.assertNotIn("tail -f", body); self.assertNotIn("--pid", body); self.assertNotIn("sleep 30", body)
        for word in ("num_turns", "nothing to judge", "packet too big", "Built:", "Findings (", "Charter:", "Human:", "Changed:", "Page:",
                     "reachable, not judged", "then ship", "heartbeat", "gate2"):
            self.assertIn(word, body, word)
        self.assertNotIn("Recommended:", body); self.assertNotIn("$cost", body)
        nonempty = [l for l in lines if l.strip()]
        self.assertTrue(nonempty[-1].startswith("Next:"), nonempty[-1])

    def test_watch_prints_lines_while_the_runner_stand_in_is_still_writing(self):
        """E26, verified: against a stand-in runner that appends a state line every 0.3 s, the skill's
        watch (sleep shortened to 0.5 s) prints at least three lines before `done`, over more
        than one call."""
        root = Path(tempfile.mkdtemp())
        try:
            (root / "traces/runs").mkdir(parents=True)
            state = root / "traces/runs/1.1.state"
            stand_in = subprocess.Popen([sys.executable, "-c", (
                "import sys,time\n"
                "p=sys.argv[1]\n"
                "for i,ph in enumerate(['branch','ci-pre','build 1','build 1 · running · last 10:00:03 · $ pytest -q','tests-commit','feat-commit','ci','verdict','close-out','done ship']):\n"
                "    open(p,'a').write(f'10:00:{i:02d} +0:{i:02d} {ph}\\n'); time.sleep(0.3)\n"), str(state)], cwd=root)
            printed: list[str] = []
            calls = 0
            seen_before_done = 0
            while True:
                cmd = WATCH.replace("<K>", str(len(printed) + 1)).replace("<id>", "1.1").replace("sleep 20", "sleep 0.5")
                if calls == 0:
                    cmd = cmd.split("; ", 1)[1]  # the first call skips the sleep
                out = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True).stdout.splitlines()
                calls += 1
                printed += out
                if any(l.split(" ", 2)[2].split(" ")[0] in ("done", "gate2") for l in out if len(l.split(" ", 2)) == 3):
                    break
                seen_before_done = len(printed)
                self.assertLess(calls, 40)
            self.assertGreaterEqual(seen_before_done, 3, printed)
            self.assertGreaterEqual(calls, 2)
            self.assertEqual(printed[-1].split(" ", 2)[2], "done ship")
            self.assertEqual(len(printed), 10, printed)  # nothing replayed, nothing lost
            stand_in.wait(timeout=10)
        finally:
            shutil.rmtree(root, ignore_errors=True)

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
        (self.root / "docs" / "domain-pack" / "charter.md").write_text("# Charter\n\n## 1. Unknown over guess\nApplies to: src/*, tests/*\nx\n")
        (self.root / ".gitignore").write_text("traces/board.html\n")
        git(self.root, "init", "-q", "-b", "main"); git(self.root, "add", "-A"); git(self.root, "commit", "-q", "-m", "base")
        for k in ("OPIK_URL_OVERRIDE",):
            os.environ.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(REPO / "scripts" / "kanban_ops.py"), *args, "--cwd", str(self.root)],
                              capture_output=True, text=True)

    def test_gate1_approve_and_override_are_the_board_path(self):
        r = self._cli("approve", "1", "--who", "ada")
        self.assertEqual(r.returncode, 1); self.assertIn("already approved", r.stderr)
        r = self._cli("override", "1", "backend", "runner", "--who", "ada")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("plan 1 backend session → runner", r.stdout)
        plan = (self.root / "kanban/plans/1.plan.md").read_text()
        self.assertIn("backend: runner", plan); self.assertIn("### [ada] ", plan); self.assertIn("— router miss: backend session → runner", plan)
        self.assertIn('"by": "ada"', (self.root / "traces/grill-misses.jsonl").read_text())
        r = self._cli("override", "1", "tickets", "9")
        self.assertEqual(r.returncode, 1); self.assertIn("override field", r.stderr)
        draft = self.root / "kanban/plans/3.plan.md"
        draft.write_text("---\nbrief: 3\nstatus: draft\napproved:\nsignals:\n  spend: false\n  partner_facing: false\n  parallel_ready: 0\n  tickets: 1\nscrutiny: light   # full iff spend or partner_facing\nbackend: runner   # runner: default; session only by override\n---\n# 3 third\n## Acceptance criteria\n- AC-1 (behavioral): x\n## Slices\n1. `3.1` — y\n")
        git(self.root, "add", "-A"); git(self.root, "commit", "-q", "-m", "plan 3 draft")
        r = self._cli("approve", "3", "--who", "ada")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("plan 3 approved by ada", r.stdout)
        text = draft.read_text()
        self.assertIn("status: approved", text); self.assertIn("approved: ada 20", text); self.assertIn("— approved by ada", text)
        self.assertIn("docs(plan 3): approved by ada", subprocess.run(["git", "log", "--format=%s"], cwd=self.root, capture_output=True, text=True).stdout)
        r = self._cli("approve")
        self.assertEqual(r.returncode, 2); self.assertIn("<plan>", r.stderr)

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
