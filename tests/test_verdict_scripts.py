"""verdict_checks.py, verdict_prep.py (arms), render_verdict.py (stamp), and the verdict schemas."""
import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import render_verdict  # noqa: E402
import schemas  # noqa: E402
import verdict_checks  # noqa: E402
import verdict_eval  # noqa: E402
import verdict_prep  # noqa: E402

FIXTURE_PROJECT = REPO / "tests" / "fixtures" / "project"
CANNED = REPO / "scripts" / "verdict_canned.py"

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
        self.assertEqual(by["no test names AC-2"]["ac"], "AC-2")
        self.assertEqual(by["no test names AC-2"]["severity"], "block")
        self.assertNotIn("no test names AC-1", by)  # AC-1 is named by tests/test_run.py on the branch
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
        render_verdict.main(self.tmp, "1.1", summary_model="none")
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

    def test_prep_includes_prompt_and_stamp_fields(self):
        text = verdict_prep.build(self.tmp, "1.1", "main", ci=False)
        prompt = (REPO / "skills" / "verdict" / "verdict-prompt.md").read_text()
        packet = schemas.Packet.parse(text)
        self.assertEqual(packet.arm, "packet")
        self.assertEqual(packet.fm["prompt_sha"], schemas.sha256(prompt))
        self.assertEqual(packet.fm["plugin_version"], json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())["version"])
        self.assertEqual(packet.fm["output"], "traces/verdict/1.1.json")
        self.assertEqual(packet.fm["ticket_file"], "kanban/tickets/1.1.tracer-bullet.md")
        self.assertIn("Guardrail: fix nothing.", packet.section("Instructions"))
        self.assertIn(schemas.SEAT_LINE["packet"], packet.section("Seat"))
        self.assertEqual(tuple(t for t, _ in packet.sections), schemas.PACKET_SECTIONS)

    def test_prep_blind_arm_is_diff_and_prompt(self):
        vd = self.tmp / "traces" / "verdict"; vd.mkdir(parents=True)
        (vd / "1.1.prev.json").write_text(json.dumps({"findings": [
            {"id": "F1", "severity": "block", "status": "open", "ac": "AC-2", "text": "ids drift", "repro": "make test"}]}))
        text = verdict_prep.build(self.tmp, "1.1", "main", ci=False, arm="blind")
        packet = schemas.Packet.parse(text)
        self.assertEqual(packet.arm, "blind")
        self.assertEqual(tuple(t for t, _ in packet.sections), schemas.BLIND_SECTIONS)
        for gone in ("AC-1 (behavioral)", "real matching", "waive F1", "too expensive to repeat", "charter-1: Unknown",
                     "ids drift", '"id": "C1"', "tests/test_run.py: ", "writes: [", "ticket_file:", "status: in_review"):
            self.assertNotIn(gone, text, gone)
        for kept in ("+class Widget", "## Diff stat", "## CI", "Guardrail: fix nothing.", schemas.SEAT_LINE["blind"], "ticket: 1.1"):
            self.assertIn(kept, text, kept)

    def test_prep_repo_arm_adds_read_only_line(self):
        text = verdict_prep.build(self.tmp, "1.1", "main", ci=False, arm="repo")
        packet = schemas.Packet.parse(text)
        self.assertEqual(packet.arm, "repo")
        self.assertIn("read-only", packet.section("Seat"))
        self.assertEqual(tuple(t for t, _ in packet.sections), schemas.PACKET_SECTIONS)
        self.assertIn("- AC-1 (behavioral)", text)

    def test_packet_roundtrip_and_rearm(self):
        text = verdict_prep.build(self.tmp, "1.1", "main", ci=False)
        packet = schemas.Packet.parse(text)
        self.assertEqual(schemas.Packet.parse(packet.render()).sections, packet.sections)
        blind = packet.rearm("blind")
        self.assertEqual(schemas.Packet.parse(blind.render()).arm, "blind")
        with self.assertRaises(ValueError):
            blind.rearm("packet")
        with self.assertRaises(ValueError):
            packet.rearm("wide")

    def test_cli_prep_arm_flag(self):
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "verdict_prep.py"), "1.1", "--no-ci", "--arm", "blind"], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(schemas.Packet.load(self.tmp / "traces/verdict/1.1.input.md").arm, "blind")

    def _verdict_and_packet(self, arm: str, findings: list[dict], decision: str = "reject") -> Path:
        vd = self.tmp / "traces" / "verdict"; vd.mkdir(parents=True, exist_ok=True)
        (vd / "1.1.input.md").write_text(verdict_prep.build(self.tmp, "1.1", "main", ci=False, arm=arm))
        (vd / "1.1.json").write_text(json.dumps({"ticket": "1.1", "decision": decision, "held": [], "findings": findings,
                                                 "ci": {"green": True, "mutation_score": None}, "quality": {"a.py": {"srp": True}}}))
        return vd / "1.1.json"

    def test_stamp_meta_on_close_out(self):
        vpath = self._verdict_and_packet("repo", [{"id": "F1", "severity": "block", "status": "open", "ac": "AC-2", "text": "x"}])
        violations = render_verdict.main(self.tmp, "1.1", "codex", summary_model="none")
        self.assertEqual(violations, [])
        v = schemas.Verdict.load(vpath)
        self.assertEqual(v.meta.as_dict(), {
            "arm": "repo", "vendor": "codex",
            "plugin_version": json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())["version"],
            "prompt_sha": schemas.sha256((REPO / "skills" / "verdict" / "verdict-prompt.md").read_text()),
            "packet_sha": schemas.sha256((vpath.with_name("1.1.input.md")).read_bytes()),
            "cost_usd": None, "tokens": None, "seconds": None, "opik": None,
        })
        render_verdict.main(self.tmp, "1.1", "codex", cost_usd=0.5, tokens={"input_tokens": 10, "output_tokens": 3}, seconds=12.4,
                            opik="untraced (OPIK_URL_OVERRIDE unset)", summary_model="none")
        self.assertEqual(schemas.Verdict.load(vpath).meta.cost_usd, 0.5)
        render_verdict.main(self.tmp, "1.1", "codex", summary_model="none")  # a re-run without usage keeps it
        m = schemas.Verdict.load(vpath).meta
        self.assertEqual((m.tokens, m.seconds, m.opik), ({"input_tokens": 10, "output_tokens": 3}, 12.4, "untraced (OPIK_URL_OVERRIDE unset)"))
        self.assertIn("Seat: codex · repo seat · cost $0.5000 · tokens in/out 10/3 · 12 s · untraced (OPIK_URL_OVERRIDE unset)", vpath.with_suffix(".html").read_text())
        self.assertEqual(v.raw["quality"], {"a.py": {"srp": True}})  # unknown keys survive the round trip
        self.assertEqual(v.decision, "reject")
        html = vpath.with_suffix(".html").read_text()
        self.assertIn("Seat: codex · repo seat", html)
        self.assertNotIn("<h2>Notes", html)
        render_verdict.main(self.tmp, "1.1", "codex", summary_model="none")  # idempotent
        self.assertEqual(schemas.Verdict.load(vpath).meta.packet_sha, v.meta.packet_sha)

    def test_blind_downgrades_uncited_block(self):
        vpath = self._verdict_and_packet("blind", [
            {"id": "F1", "severity": "block", "status": "open", "ac": None, "charter": None, "text": "no citation"},
            {"id": "F2", "severity": "block", "status": "open", "ac": "AC-1", "charter": None, "text": "cited"}])
        self.assertEqual(render_verdict.main(self.tmp, "1.1", summary_model="none"), [])
        v = schemas.Verdict.load(vpath)
        self.assertEqual([(f["id"], f["severity"]) for f in v.findings], [("F1", "warn"), ("F2", "block")])
        self.assertEqual(v.decision, "reject")
        self.assertEqual(v.meta.vendor, "claude-session"); self.assertIsNone(v.meta.cost_usd)
        self.assertEqual(v.ticket, "1.1")

    def test_blind_downgrade_recomputes_decision(self):
        vpath = self._verdict_and_packet("blind", [{"id": "F1", "severity": "block", "status": "open", "text": "no citation"}])
        render_verdict.main(self.tmp, "1.1", summary_model="none")
        v = schemas.Verdict.load(vpath)
        self.assertEqual(v.open_blocks(), ()); self.assertEqual(v.decision, "ship")
        self.assertFalse([f for f in v.findings if "unaccounted" in f["text"]])  # blind shows no ACs: nothing to account for

    def test_packet_arm_reports_uncited_block(self):
        vpath = self._verdict_and_packet("packet", [{"id": "F1", "severity": "block", "status": "open", "text": "no citation"}])
        violations = render_verdict.main(self.tmp, "1.1", summary_model="none")
        self.assertEqual(violations, ["F1: block without an ac or charter citation (packet seat)"])
        v = schemas.Verdict.load(vpath)
        self.assertEqual(v.findings[0]["severity"], "block"); self.assertEqual(v.meta.arm, "packet")
        self.assertIn("Invalid: F1: block without", vpath.with_suffix(".html").read_text())

    def test_ticket_id_comes_from_the_packet(self):
        """E4: the /verdict session wrote ticket "1" for 1.1; close-out sets it from the packet."""
        vpath = self._verdict_and_packet("packet", [])
        vpath.write_text(json.dumps({"ticket": "1", "decision": "ship", "held": ["AC-1", "AC-2", "charter-1", "charter-2"], "findings": [], "ci": {"green": True}}))
        render_verdict.main(self.tmp, "1.1", summary_model="none")
        self.assertEqual(schemas.Verdict.load(vpath).ticket, "1.1")

    def test_unaccounted_items_become_c_warns(self):
        """E6: an AC or charter item in neither held nor a finding is a C-warn, not silence."""
        vpath = self._verdict_and_packet("packet", [
            {"id": "C1", "severity": "warn", "status": "open", "ac": "AC-2", "text": "no test names AC-2"},
            {"id": "F1", "severity": "warn", "status": "open", "charter": "charter-2", "text": "evidence"}], decision="ship")
        v = json.loads(vpath.read_text()); v["held"] = ["AC-1"]; vpath.write_text(json.dumps(v))
        render_verdict.main(self.tmp, "1.1", summary_model="none")
        v = schemas.Verdict.load(vpath)
        self.assertEqual([(f["id"], f["severity"], f["text"], f.get("ac"), f.get("charter")) for f in v.findings[2:]],
                         [("C2", "warn", "unaccounted: charter-1", None, "charter-1")])
        render_verdict.main(self.tmp, "1.1", summary_model="none")  # idempotent: no second C2
        self.assertEqual(len(schemas.Verdict.load(vpath).findings), 3)
        self.assertEqual(verdict_checks.unaccounted(v, schemas.Packet.load(vpath.with_name("1.1.input.md"))), [])

    def test_lonely_check_skips_new_files(self):
        """E5: a new module is all one-caller defs by construction; only defs added to existing files count."""
        found = verdict_checks.checks(self.tmp, "1.1", "main", ci_green=True)
        lonely = [f for f in found if "one caller" in f["text"]]
        self.assertEqual(len(lonely), 1)
        self.assertIn("lonely", lonely[0]["text"])  # added to the existing src/app/run.py
        (self.tmp / "src/app/fresh.py").write_text("def alone():\n    return 1\n\n\nclass Solo:\n    pass\n")
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "new module")
        found = verdict_checks.checks(self.tmp, "1.1", "main", ci_green=True)
        lonely = [f for f in found if "one caller" in f["text"]]
        self.assertEqual(len(lonely), 1); self.assertNotIn("alone", lonely[0]["text"]); self.assertNotIn("Solo", lonely[0]["text"])

    def test_prep_folds_fixtures_and_large_files(self):
        """E3: a fixture in the diff is one line, not 30K tokens of reviewer context."""
        (self.tmp / "tests/fixtures").mkdir(parents=True)
        (self.tmp / "tests/fixtures/big.txt").write_text("word\n" * 3000)
        (self.tmp / "src/app/large.py").write_text("\n".join(f"x{i} = {i}" for i in range(2500)) + "\n")
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "bulk")
        text = verdict_prep.build(self.tmp, "1.1", "main", ci=False)
        diff = schemas.Packet.parse(text).section("Diff")
        self.assertIn("# tests/fixtures/big.txt: ", diff); self.assertIn("omitted (fixture)", diff)
        self.assertIn("# src/app/large.py: ", diff); self.assertIn("omitted (over 2000 lines)", diff)
        self.assertNotIn("+word", diff); self.assertNotIn("+x2499", diff)
        self.assertIn("+class Widget", diff)  # ordinary files stay whole
        self.assertIn("tests/fixtures/big.txt", schemas.Packet.parse(text).section("Diff stat"))

    def test_prep_refuses_without_charter(self):
        (self.tmp / "docs/domain-pack/charter.md").write_text("# Charter\n\nno numbered items\n")
        with self.assertRaises(SystemExit) as cm:
            verdict_prep.build(self.tmp, "1.1", "main", ci=False)
        self.assertIn("no charter", str(cm.exception))
        (self.tmp / "docs/domain-pack/charter.md").unlink()
        with self.assertRaises(SystemExit):
            verdict_prep.build(self.tmp, "1.1", "main", ci=False)

    def test_recommendations_are_computed(self):
        tickets = [("1.1", "in_review", ["src/app/", "tests/"]), ("1.2", "ready", ["src/export/"]), ("1.3", "done", ["src/app/"]),
                   ("1.1.1", "ready", [])]
        v = schemas.Verdict.from_dict({"ticket": "1.1", "decision": "reject", "findings": [
            {"id": "F1", "severity": "block", "status": "open", "ac": "AC-2", "spawn_child": True, "text": "needs its own slice"},
            {"id": "F2", "severity": "block", "status": "open", "ac": "AC-1", "spawn_child": False, "text": "wrong"},
            {"id": "F3", "severity": "warn", "status": "open", "text": "export path unset, src/export/write.py:12"},
            {"id": "F4", "severity": "warn", "status": "open", "text": "naming in src/app/run.py:3"},
            {"id": "F5", "severity": "warn", "status": "open", "home": "2.1", "text": "already homed"},
            {"id": "F6", "severity": "block", "status": "resolved", "ac": "AC-1", "text": "fixed"}]})
        self.assertEqual(render_verdict.recommendations(v, tickets), [
            ("F1", "ship, create child 1.1.2 from F1"), ("F2", "rework in place"),
            ("F3", "home to 1.2"), ("F4", "waive"), ("F5", "home to 2.1")])
        self.assertEqual(render_verdict.recommendations(schemas.Verdict.from_dict({"ticket": "1.1", "decision": "ship", "findings": []}), tickets), [])

    def test_summary_call_is_cached_and_shown(self):
        vpath = self._verdict_and_packet("packet", [{"id": "F1", "severity": "warn", "status": "open", "ac": "AC-2", "text": "thin test"}], decision="ship")
        v = json.loads(vpath.read_text()); v["held"] = ["AC-1", "charter-1", "charter-2"]; vpath.write_text(json.dumps(v))
        bin_dir = self.tmp / "bin"; bin_dir.mkdir()
        log = self.tmp / "calls.log"
        (bin_dir / "claude").write_text(
            "#!/bin/sh\necho \"$*\" >> \"$FAKE_LOG\"\ncat > \"$FAKE_LOG.prompt\"\n"
            "printf '%s' '{\"type\":\"result\",\"total_cost_usd\":0.002,\"usage\":{\"output_tokens\":40},"
            "\"result\":\"{\\\"built\\\": \\\"Stages now write three files.\\\", \\\"review\\\": \\\"Ship. One thin test.\\\"}\"}'\n")
        (bin_dir / "claude").chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(log)}):
            render_verdict.main(self.tmp, "1.1", "codex", summary_model="haiku")
            summary = json.loads(render_verdict.summary_path(self.tmp, "1.1").read_text())
            self.assertEqual((summary["built"], summary["review"], summary["cost_usd"], summary["model"]),
                             ("Stages now write three files.", "Ship. One thin test.", 0.002, "haiku"))
            self.assertIn("-p --model haiku --output-format json --tools", log.read_text())
            prompt = (self.tmp / "calls.log.prompt").read_text()
            self.assertIn("close-out", prompt); self.assertIn("Diff stat", prompt); self.assertIn('"decision": "ship"', prompt)
            self.assertNotIn("packet_sha", prompt)
            render_verdict.main(self.tmp, "1.1", "codex", summary_model="haiku")  # cached: no second call
            self.assertEqual(len(log.read_text().splitlines()), 1)
        html = vpath.with_suffix(".html").read_text()
        self.assertIn("<h2>What was built</h2><p>Stages now write three files.</p>", html)
        self.assertIn("Ship. One thin test.", html); self.assertIn("<b>Ship.</b>", html)
        self.assertIn("<h2>Recommended action</h2><ul><li><b>F1</b> waive</li></ul>", html)
        render_verdict.main(self.tmp, "1.1", "codex", summary_model="none")
        self.assertIn("summary unavailable: summary skipped", vpath.with_suffix(".html").read_text())

    def test_legacy_verdict_without_packet_renders_unstamped(self):
        vd = self.tmp / "traces" / "verdict"; vd.mkdir(parents=True)
        (vd / "1.1.json").write_text(json.dumps({"ticket": "1.1", "decision": "ship", "held": [], "findings": [], "ci": {"green": True}}))
        render_verdict.main(self.tmp, "1.1", summary_model="none")
        self.assertIsNone(schemas.Verdict.load(vd / "1.1.json").meta)
        self.assertNotIn("meta", json.loads((vd / "1.1.json").read_text()))
        self.assertIn("Seat: unstamped", (vd / "1.1.html").read_text())

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

    def test_verdict_meta_optional(self):
        v = schemas.Verdict.from_dict({"ticket": "1.1", "decision": "ship", "findings": [{"id": "F1", "severity": "block"}]})
        self.assertIsNone(v.meta)
        self.assertEqual(v.open_blocks(), ({"id": "F1", "severity": "block"},))  # no status = open, as runner reads it
        self.assertEqual(v.uncited_blocks(), v.blocks())
        meta = schemas.VerdictMeta.from_dict({"arm": "blind", "vendor": "codex", "plugin_version": "0.6.2", "prompt_sha": "a", "packet_sha": "b"})
        self.assertEqual(v.with_meta(meta).as_dict()["meta"], meta.as_dict())
        self.assertEqual(sorted(meta.as_dict()), sorted(schemas.META_FIELDS + schemas.META_OPTIONAL))
        self.assertIsNone(meta.cost_usd); self.assertIsNone(meta.tokens); self.assertIsNone(meta.seconds); self.assertIsNone(meta.opik)
        self.assertEqual(schemas.VerdictMeta.from_dict({**meta.as_dict(), "seconds": 3, "opik": "tracing to x"}).seconds, 3.0)
        rich = schemas.VerdictMeta.from_dict({**meta.as_dict(), "cost_usd": 0.25, "tokens": {"output_tokens": 3}})
        self.assertEqual((rich.cost_usd, rich.tokens), (0.25, {"output_tokens": 3}))
        self.assertIsNone(schemas.VerdictMeta.from_dict({**meta.as_dict(), "cost_usd": "n/a"}).cost_usd)
        with self.assertRaises(ValueError):
            schemas.Verdict.from_dict(["not", "an", "object"])

    def test_validate_by_arm(self):
        v = schemas.Verdict.from_dict({"decision": "reject", "ci": {"green": True}, "findings": [
            {"id": "F1", "severity": "block", "status": "open"}, {"id": "F2", "severity": "warn", "status": "open"}]})
        kept, violations = verdict_checks.validate(v, "packet")
        self.assertEqual(kept, v); self.assertEqual(len(violations), 1)
        down, violations = verdict_checks.validate(v, "blind")
        self.assertEqual(violations, []); self.assertEqual([f["severity"] for f in down.findings], ["warn", "warn"])
        self.assertEqual(down.decision, "ship")
        red, _ = verdict_checks.validate(v.with_findings(v.findings), "blind")
        self.assertEqual(verdict_checks.validate(schemas.Verdict.from_dict({"decision": "reject", "ci": {"green": False}, "findings": [
            {"id": "F1", "severity": "block"}]}), "blind")[0].decision, "reject")


class TicketFileTest(unittest.TestCase):
    def test_find_ticket_ignores_children(self):
        import kanban_ops
        self.assertTrue(kanban_ops.is_ticket_file("1.2.match-stage.md", "1.2"))
        self.assertFalse(kanban_ops.is_ticket_file("1.2.1.ordered-lists.md", "1.2"))
        self.assertTrue(kanban_ops.is_ticket_file("1.2.1.ordered-lists.md", "1.2.1"))
        self.assertFalse(kanban_ops.is_ticket_file("1.plan.md", "1")); self.assertFalse(kanban_ops.is_ticket_file("1.21.x.md", "1.2"))
        self.assertEqual(kanban_ops.find_ticket(REPO / "tests" / "fixtures" / "project", "1.2").name, "1.2.match-stage.md")


class VerdictSkillTest(unittest.TestCase):
    """skills/verdict/SKILL.md: the packet-path flow ends at the verdict JSON."""

    def test_packet_path_flow_has_no_close_out(self):
        body = (REPO / "skills" / "verdict" / "SKILL.md").read_text()
        flow = schemas.section(body, "Packet path")
        steps = schemas._items(flow)  # the numbered steps are what the session does; prose only says what it does not
        self.assertEqual(len(steps), 2, steps)
        for step in ("verdict_prep", "render_verdict", "Log", "status", "summary", "options"):
            self.assertFalse(any(step in it for it in steps), step)
        self.assertIn("`output`", steps[1])
        self.assertIn("Read nothing else", steps[0])
        self.assertEqual(len(schemas._items(schemas.section(body, "Ticket id"))), 3)
        self.assertIn("Session ends at the verdict.", schemas.section(body, "Never"))
        self.assertIn("never invoke `/verdict`", body)
        self.assertIn("render_verdict.py", schemas.section(body, "Ticket id"))


class VerdictEvalFixtureTest(unittest.TestCase):
    """tests/fixtures/project: a neutral project verdict_eval.py runs over without a model or a server."""

    def test_fixture_is_a_0_6_2_packet_with_a_matching_stamp(self):
        ppath = FIXTURE_PROJECT / "traces" / "verdict" / "1.1.input.md"
        packet = schemas.Packet.load(ppath)
        self.assertEqual(packet.render(), ppath.read_text(encoding="utf-8"))  # rendered via Packet, round-trips
        self.assertEqual(tuple(t for t, _ in packet.sections), schemas.PACKET_SECTIONS)
        self.assertEqual(packet.arm, "packet"); self.assertEqual(packet.fm["output"], "traces/verdict/1.1.json")
        self.assertIn("green", packet.section("CI"))
        self.assertLessEqual(packet.section("Diff").count("\n"), 34)
        v = schemas.Verdict.load(FIXTURE_PROJECT / "traces" / "verdict" / "1.1.json")
        self.assertEqual(v.decision, "reject")
        self.assertEqual([(f["id"], f["severity"], f.get("ac") or f.get("charter")) for f in v.findings],
                         [("C1", "block", "AC-2"), ("F2", "warn", "charter-2")])
        self.assertEqual(v.findings[0]["text"], "no test names AC-2")
        self.assertIn('"id": "C1"', packet.section("Mechanical findings (copy verbatim into findings)"))
        self.assertEqual(packet.fm["prompt_sha"], schemas.sha256((REPO / "skills" / "verdict" / "verdict-prompt.md").read_text()))
        self.assertTrue(all(v.cited(f) for f in v.findings))
        self.assertEqual(v.meta.packet_sha, schemas.sha256(ppath.read_bytes()))
        rows = verdict_eval.items(FIXTURE_PROJECT)
        self.assertEqual([(r["ticket"], r["expected"]) for r in rows], [("1.1", "reject"), ("1.2", "reject")])
        v2 = schemas.Verdict.load(FIXTURE_PROJECT / "traces" / "verdict" / "1.2.json")
        self.assertTrue(any(f.get("spawn_child") for f in v2.open_blocks()))
        self.assertEqual(render_verdict.recommendations(v2, render_verdict.tickets_of(FIXTURE_PROJECT)),
                         [("C1", "rework in place"), ("F1", "ship, create child 1.2.1 from F1")])

    def test_canned_cmd_copies_verdict(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            r = subprocess.run([sys.executable, str(CANNED), str(FIXTURE_PROJECT / "traces/verdict/1.1.json"), str(tmp / "out/v.json")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads((tmp / "out/v.json").read_text())["decision"], "reject")
            self.assertEqual(subprocess.run([sys.executable, str(CANNED)], capture_output=True).returncode, 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_eval_smoke_on_fixture_without_opik_or_network(self):
        cmd = f"{sys.executable} {CANNED} {FIXTURE_PROJECT / 'traces/verdict/1.1.json'} {{output}}"
        attempts = []

        def refuse(self_, addr, *a, **k):
            attempts.append(addr)
            raise OSError("network blocked by test")

        env = {k: "" for k in ("OPIK_URL_OVERRIDE", "OPIK_API_KEY")}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(socket.socket, "connect", refuse), \
                mock.patch.object(socket.socket, "connect_ex", refuse), contextlib.redirect_stdout(out):
            for k in env:
                os.environ.pop(k, None)
            rc = verdict_eval.main(["verdict_eval.py", str(FIXTURE_PROJECT), "--arm", "packet", "--verdict-cmd", cmd])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(attempts, [])
        rows = verdict_eval.score_local(verdict_eval.items(FIXTURE_PROJECT), "packet", cmd)
        self.assertEqual(len(rows), 2)  # 1.1 and 1.2; the canned verdict answers both
        self.assertEqual(sorted(k for k in rows[0] if k not in ("ticket", "error")), sorted(verdict_eval.METRICS))
        self.assertIsNone(rows[0]["error"])
        self.assertEqual({k: rows[0][k] for k in ("block_count", "finding_count", "citation_compliance", "decision_agreement")},
                         {"block_count": 1.0, "finding_count": 2.0, "citation_compliance": 1.0, "decision_agreement": 1.0})
        self.assertGreater(rows[0]["wall_seconds"], 0.0)
        text = out.getvalue()
        self.assertIn("2 packets, 2 with an expected decision", text)
        for name in verdict_eval.METRICS:
            self.assertIn(f"{name}=", text)
        self.assertIn("decision_agreement=1.0", text)
        self.assertIn("[eval] 2 items, 0 errors, averages over 2: block_count=1.0 finding_count=2.0 citation_compliance=1.0 decision_agreement=1.0 wall_seconds=", text)
        blind = verdict_eval.score_local(verdict_eval.items(FIXTURE_PROJECT), "blind", cmd)[0]
        self.assertEqual(blind["decision_agreement"], 1.0)  # a cited block survives the blind close-out


if __name__ == "__main__":
    unittest.main()
