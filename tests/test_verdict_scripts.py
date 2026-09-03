"""verdict_checks.py, verdict_prep.py, render_verdict.py archive, and the verdict schemas."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import render_verdict  # noqa: E402
import schemas  # noqa: E402
import verdict_checks  # noqa: E402
import verdict_prep  # noqa: E402

PLAN = """---
brief: 1
status: approved
approved: reviewer 2026-09-01
signals:
  spend: false
  partner_facing: false
  parallel_ready: 0
  tickets: 1
scrutiny: light
backend: session
---
# 1 Widget pipeline
## Acceptance criteria
- AC-1 (behavioral): Given a file, When run, Then output exists.
## Verdict must attack
- The run is too expensive to repeat monthly
- A rename counted as a move
## Slices
1. `1.1` — tracer bullet
"""
TICKET = """---
id: 1.1
parent: 1
status: in_review
depends_on: []
writes: ["src/app/", "tests/"]
---
# 1.1 tracer bullet
## Outcome
One sentence.
## Acceptance criteria
- AC-1 (behavioral): Given a file, When run, Then output exists. (plan 1 AC-1)
- AC-2 (property): For all rows, id is verbatim.
## Out of scope
- real matching
## Findings (append-only)
## Log (append-only)
### [grill] 2026-09-01 10:00 — created
### [human] 2026-09-02 11:00 — waive F1
The lockfile is mechanical output.
### [build] 2026-09-02 12:00 — close-out
done
"""
CHARTER = "# Charter\n\n## 1. Unknown over guess\ntext\n\n## 2. Evidence is openable\ntext\n"


def git(root, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True, capture_output=True)


class VerdictScriptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for rel, text in {
            "kanban/plans/1.plan.md": PLAN, "kanban/tickets/1.1.tracer-bullet.md": TICKET,
            "docs/domain-pack/charter.md": CHARTER, "docs/glossary.md": "# Glossary\n",
            "src/app/__init__.py": "", "src/app/run.py": "def run(x):\n    return x\n",
            "tests/test_run.py": "def test_run():\n    assert True\n",
        }.items():
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).write_text(text)
        git(self.tmp, "init", "-q", "-b", "main")
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "base")
        git(self.tmp, "checkout", "-q", "-b", "ticket/1.1")
        (self.tmp / "src/app/run.py").write_text("def run(x):\n    return Widget(x)\n\n\nclass Widget:\n    def __init__(self, x):\n        self.x = x\n\n\ndef lonely():\n    return 1\n")
        (self.tmp / "src/other").mkdir(); (self.tmp / "src/other/y.py").write_text("X = 1\n")
        (self.tmp / "tests/test_run.py").write_text('def test_run():\n    """AC-1: output exists."""\n    assert True\n')
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "feat(1.1)")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_checks(self):
        found = verdict_checks.checks(self.tmp, "1.1", "main", ci_green=False)
        by = {f["text"].split(":")[0]: f for f in found}
        self.assertEqual([f["id"] for f in found], [f"C{i}" for i in range(1, len(found) + 1)])
        self.assertEqual(by["Written outside writes"]["severity"], "block")
        self.assertIn("src/other/y.py", by["Written outside writes"]["text"])
        self.assertEqual(by["make ci is red"]["severity"], "block")
        self.assertEqual(by["No test added on this branch names AC-2"]["ac"], "AC-2")
        self.assertIn("lonely", by["New defs with one caller or none"]["text"])
        self.assertIn("Widget", by["New names absent from docs/glossary.md"]["text"])
        self.assertTrue(all(f["status"] == "open" for f in found))

    def test_checks_clean_when_in_scope(self):
        (self.tmp / "src/other/y.py").unlink(); (self.tmp / "src/other").rmdir()
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "fix")
        texts = [f["text"] for f in verdict_checks.checks(self.tmp, "1.1", "main", ci_green=True)]
        self.assertFalse(any("outside writes" in t or "ci is red" in t for t in texts))

    def test_prep_input(self):
        text = verdict_prep.build(self.tmp, "1.1", "main", ci=False)
        for needle in (
            "- AC-1 (behavioral)", "- AC-2 (property)", "- real matching",
            "### [human] 2026-09-02 11:00 — waive F1", "- The run is too expensive to repeat monthly",
            "- charter-1: Unknown over guess", '"id": "C1"', "tests/test_run.py: ", "## Diff\n```diff",
            "+class Widget", "writes: ['src/app/', 'tests/']",
        ):
            self.assertIn(needle, text)
        self.assertNotIn("- - AC-1", text)
        self.assertNotIn("close-out", text)  # Log stays out except human waivers

    def test_prep_lists_previous_blocks(self):
        vd = self.tmp / "traces" / "verdict"; vd.mkdir(parents=True)
        (vd / "1.1.prev.json").write_text(json.dumps({"findings": [
            {"id": "F1", "severity": "block", "status": "open", "ac": "AC-2", "text": "ids drift", "repro": "make test"},
            {"id": "F2", "severity": "warn", "status": "open", "text": "meh"}]}))
        text = verdict_prep.build(self.tmp, "1.1", "main", ci=False)
        self.assertIn("F1 open (AC-2): ids drift — repro: `make test`", text)
        self.assertNotIn("meh", text)

    def test_prep_archives_existing_verdict_first(self):
        vd = self.tmp / "traces" / "verdict"; vd.mkdir(parents=True)
        (vd / "1.1.json").write_text(json.dumps({"findings": [
            {"id": "F1", "severity": "block", "status": "open", "ac": "AC-2", "text": "ids drift", "repro": "make test"}]}))
        text = verdict_prep.build(self.tmp, "1.1", "main", ci=False)
        self.assertTrue((vd / "1.1.prev.json").exists())
        self.assertIn("F1 open (AC-2): ids drift", text)

    def test_render_shows_held(self):
        vd = self.tmp / "traces" / "verdict"; vd.mkdir(parents=True)
        (vd / "1.1.json").write_text(json.dumps({"ticket": "1.1", "decision": "ship", "held": ["AC-1"], "findings": [], "ci": {"green": True}}))
        render_verdict.main(self.tmp, "1.1")
        self.assertIn("Held: AC-1", (vd / "1.1.html").read_text())

    def test_live_call_check_blocks(self):
        (self.tmp / ".env.example").write_text("SOME_API_KEY=\n")
        (self.tmp / "tests/test_net.py").write_text(
            "import os, socket\n"
            "def test_calls_out():\n"
            "    if os.environ.get('SOME_API_KEY'):\n"
            "        try: socket.create_connection(('example.invalid', 443), timeout=1)\n"
            "        except OSError: pass\n"
            "    assert True\n")
        try:
            import pytest  # noqa: F401
        except ImportError:
            self.skipTest("pytest not installed")
        found = verdict_checks.checks(self.tmp, "1.1", "main", ci_green=True)
        live = [f for f in found if "live network call" in f["text"]]
        self.assertEqual(len(live), 1); self.assertEqual(live[0]["severity"], "block")
        self.assertIn("example.invalid", live[0]["text"])

    def test_cli_prep_writes_input(self):
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "verdict_prep.py"), "1.1", "--no-ci"], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "traces/verdict/1.1.input.md")
        self.assertTrue((self.tmp / "traces/verdict/1.1.input.md").exists())


class VerdictSchemaTest(unittest.TestCase):
    def test_attacks_charter_ticket(self):
        self.assertEqual(schemas.Attacks.parse(PLAN).items, ("The run is too expensive to repeat monthly", "A rename counted as a move"))
        self.assertEqual(schemas.Attacks.parse("## Verdict must attack\n- <failure modes carried over>\n").items, ())
        self.assertEqual(schemas.Charter.parse(CHARTER).items, (("1", "Unknown over guess"), ("2", "Evidence is openable")))
        t = schemas.Ticket.parse(TICKET)
        self.assertEqual(len(t.acs), 2); self.assertTrue(t.acs[0].startswith("AC-1 "))
        self.assertEqual(t.out_of_scope, ("real matching",))
        self.assertEqual(len(t.waivers), 1); self.assertIn("mechanical output", t.waivers[0])


if __name__ == "__main__":
    unittest.main()
