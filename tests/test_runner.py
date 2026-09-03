"""runner.py verdict seat (--verdict-cmd, --arm, stamp, opik-absent path) and verdict_eval.py."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import runner  # noqa: E402
import schemas  # noqa: E402
import verdict_eval  # noqa: E402
import verdict_prep  # noqa: E402
from test_verdict_scripts import CHARTER, PLAN, TICKET, git  # noqa: E402

# A stand-in vendor: reads the packet, writes a verdict at the output path the runner hands it.
FAKE_VENDOR = """import json, sys
packet, out = sys.argv[1], sys.argv[2]
text = open(packet).read()
assert text.startswith("---\\nticket: 1.1\\n"), text[:40]
json.dump({"ticket": "1.1", "decision": "reject", "held": ["AC-1"], "ci": {"green": True},
           "findings": [{"id": "F1", "severity": "block", "status": "open", "ac": None, "charter": None,
                         "text": "uncited", "spawn_child": False}]}, open(out, "w"))
"""


class RunnerSeatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for rel, text in {
            "kanban/plans/1.plan.md": PLAN, "kanban/tickets/1.1.tracer-bullet.md": TICKET,
            "docs/domain-pack/charter.md": CHARTER, "src/app/run.py": "def run(x):\n    return x\n",
        }.items():
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).write_text(text)
        git(self.tmp, "init", "-q", "-b", "main"); git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "base")
        git(self.tmp, "checkout", "-q", "-b", "ticket/1.1")
        (self.tmp / "src/app/run.py").write_text("def run(x):\n    return x + 1\n")
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "feat(1.1)")
        (self.tmp / "fake_vendor.py").write_text(FAKE_VENDOR)
        self.cmd = f"{sys.executable} fake_vendor.py {{packet}} {{output}}"
        self.env = mock.patch.dict(os.environ, {k: "" for k in runner.OPIK_ENV})
        self.env.start()
        for k in runner.OPIK_ENV:
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_cmd_and_placeholders(self):
        self.assertEqual(runner.DEFAULT_VERDICT_CMD, "claude -p '/verdict {packet}' --model {model} --permission-mode acceptEdits")
        cmd = runner.verdict_cmd(runner.DEFAULT_VERDICT_CMD, packet="traces/verdict/1.1.input.md", output="traces/verdict/1.1.json", model="opus", ticket="1.1")
        self.assertEqual(cmd, "claude -p '/verdict traces/verdict/1.1.input.md' --model opus --permission-mode acceptEdits")
        self.assertEqual(runner.vendor_of(runner.DEFAULT_VERDICT_CMD), "claude")
        self.assertEqual(runner.vendor_of("/usr/local/bin/codex exec --json {packet} > {output}"), "codex")
        for ph in ("{packet}", "{output}", "{model}", "{ticket}"):
            self.assertIn(ph, runner.VERDICT_CMD_HELP)

    def test_verdict_runs_cmd_stamps_and_reads_decision(self):
        decision, retryable, progressed = runner.verdict(self.tmp, "1.1", "opus", arm="packet", template=self.cmd)
        self.assertEqual((decision, retryable, progressed), ("reject", True, True))
        v = schemas.Verdict.load(self.tmp / "traces/verdict/1.1.json")
        self.assertEqual(v.meta.arm, "packet"); self.assertEqual(v.meta.vendor, Path(sys.executable).stem)
        self.assertEqual(v.meta.packet_sha, schemas.sha256((self.tmp / "traces/verdict/1.1.input.md").read_bytes()))
        self.assertTrue((self.tmp / "traces/verdict/1.1.html").exists())
        self.assertEqual(schemas.Packet.load(self.tmp / "traces/verdict/1.1.input.md").arm, "packet")

    def test_blind_arm_downgrades_and_ships(self):
        decision, retryable, progressed = runner.verdict(self.tmp, "1.1", "opus", arm="blind", template=self.cmd)
        self.assertEqual(decision, "ship")
        self.assertEqual(schemas.Verdict.load(self.tmp / "traces/verdict/1.1.json").meta.arm, "blind")
        self.assertEqual(schemas.Packet.load(self.tmp / "traces/verdict/1.1.input.md").arm, "blind")

    def test_missing_output_is_reject(self):
        self.assertEqual(runner.verdict(self.tmp, "1.1", "opus", template="true"), ("reject", True, True))

    def test_previous_verdict_archived_and_progress_tracked(self):
        vd = self.tmp / "traces/verdict"; vd.mkdir(parents=True)
        (vd / "1.1.json").write_text(json.dumps({"decision": "reject", "findings": [{"id": "F1", "severity": "block", "status": "open", "ac": "AC-1", "text": "same"}]}))
        decision, retryable, progressed = runner.verdict(self.tmp, "1.1", "opus", template=self.cmd)
        self.assertEqual((decision, progressed), ("reject", False))
        self.assertTrue((vd / "1.1.prev.json").exists())
        self.assertIn("F1 open (AC-1): same", (vd / "1.1.input.md").read_text())

    def test_opik_absent_or_unconfigured_traces_nothing(self):
        self.assertIsNone(runner.opik_client())  # env unset, whatever is installed
        self.assertEqual(runner.OPIK_ENV, ("OPIK_URL_OVERRIDE",))
        fake = types.ModuleType("opik"); fake.Opik = lambda: self.fail("client built on OPIK_API_KEY alone")
        with mock.patch.dict(os.environ, {"OPIK_API_KEY": "k"}), mock.patch.dict(sys.modules, {"opik": fake}):
            self.assertIsNone(runner.opik_client())  # api key alone is not a signal
        with mock.patch.dict(os.environ, {"OPIK_URL_OVERRIDE": "http://localhost:5173/api"}), mock.patch.dict(sys.modules, {"opik": None}):
            self.assertIsNone(runner.opik_client())  # env set, package missing
        runner.verdict(self.tmp, "1.1", "opus", template=self.cmd)
        self.assertTrue((self.tmp / "traces/verdict/1.1.json").exists())

    def test_opik_configured_wraps_one_trace(self):
        calls = {}

        class Client:
            def trace(self, **kw): calls["trace"] = kw; calls["n"] = calls.get("n", 0) + 1
            def flush(self): calls["flushed"] = True

        fake = types.ModuleType("opik"); fake.Opik = Client
        with mock.patch.dict(os.environ, {"OPIK_URL_OVERRIDE": "http://localhost:5173/api"}), mock.patch.dict(sys.modules, {"opik": fake}):
            runner.verdict(self.tmp, "1.1", "opus", arm="blind", template=self.cmd)
        packet = (self.tmp / "traces/verdict/1.1.input.md").read_text()
        self.assertEqual(calls["trace"]["name"], "verdict")
        self.assertEqual(calls["trace"]["input"], {"packet": packet})
        md = calls["trace"]["metadata"]
        self.assertEqual(md["ticket"], "1.1"); self.assertEqual(md["arm"], "blind"); self.assertIn("wall_seconds", md)
        self.assertEqual(sorted(k for k in md if k in schemas.META_FIELDS), sorted(schemas.META_FIELDS))
        self.assertEqual(calls["trace"]["output"], json.loads((self.tmp / "traces/verdict/1.1.json").read_text()))
        self.assertGreaterEqual(calls["trace"]["end_time"], calls["trace"]["start_time"])
        self.assertEqual(calls["n"], 1); self.assertTrue(calls["flushed"])

    def test_cli_help_documents_placeholders(self):
        r = subprocess.run([sys.executable, str(REPO / "scripts/runner.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        for s in ("--verdict-cmd", "--arm", "{packet}", "{output}", "blind", "repo"):
            self.assertIn(s, r.stdout)


class VerdictEvalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for rel, text in {
            "kanban/plans/1.plan.md": PLAN, "kanban/tickets/1.1.tracer-bullet.md": TICKET,
            "docs/domain-pack/charter.md": CHARTER, "src/app/run.py": "def run(x):\n    return x\n",
        }.items():
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).write_text(text)
        git(self.tmp, "init", "-q", "-b", "main"); git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "base")
        git(self.tmp, "checkout", "-q", "-b", "ticket/1.1")
        (self.tmp / "src/app/run.py").write_text("def run(x):\n    return x + 1\n")
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "feat(1.1)")
        self.vd = self.tmp / "traces/verdict"; self.vd.mkdir(parents=True)
        self.packet = verdict_prep.build(self.tmp, "1.1", "main", ci=False)
        (self.vd / "1.1.input.md").write_text(self.packet)
        (self.tmp / "fake_vendor.py").write_text(FAKE_VENDOR)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stamped(self, decision="ship", sha=None):
        sha = sha or schemas.sha256((self.vd / "1.1.input.md").read_bytes())
        (self.vd / "1.1.json").write_text(json.dumps({"ticket": "1.1", "decision": decision, "findings": [], "meta": {
            "arm": "packet", "vendor": "claude", "plugin_version": "0.6.2", "prompt_sha": "p", "packet_sha": sha}}))

    def test_expected_from_stamped_verdict(self):
        self._stamped("ship")
        rows = verdict_eval.items(self.tmp)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["ticket"], rows[0]["packet_arm"], rows[0]["expected"]), ("1.1", "packet", "ship"))
        self.assertEqual(rows[0]["packet"], self.packet)

    def test_expected_falls_back_to_ticket_log(self):
        self._stamped("ship", sha="stale")  # a verdict of some other packet: not this one's decision
        t = self.tmp / "kanban/tickets/1.1.tracer-bullet.md"
        t.write_text(t.read_text() + "### [verdict] 2026-09-02 13:00 — reject\n- block F1 AC-2: ids drift\n")
        self.assertEqual(verdict_eval.items(self.tmp)[0]["expected"], "reject")

    def test_expected_null_when_unrecoverable(self):
        self.assertIsNone(verdict_eval.items(self.tmp)[0]["expected"])
        (self.vd / "1.1.json").write_text(json.dumps({"decision": "ship", "findings": []}))  # unstamped, unlogged
        self.assertIsNone(verdict_eval.items(self.tmp)[0]["expected"])

    def test_run_one_replays_under_arm(self):
        item = verdict_eval.items(self.tmp)[0]
        cmd = f"{sys.executable} {self.tmp / 'fake_vendor.py'} {{packet}} {{output}}"
        out = verdict_eval.run_one(item, "blind", cmd)
        self.assertEqual(out["output"]["decision"], "ship")  # blind: uncited block downgraded at close-out
        self.assertEqual(out["output"]["meta"]["arm"], "blind"); self.assertGreater(out["wall_seconds"], 0)
        out = verdict_eval.run_one(item, "packet", cmd)
        self.assertEqual(out["output"]["decision"], "reject")
        self.assertEqual(verdict_eval.run_one(item, "packet", "true")["output"], {})
        with self.assertRaises(ValueError):
            verdict_eval.run_one({**item, "packet": schemas.Packet.parse(item["packet"]).rearm("blind").render()}, "packet", cmd)

    def test_metrics_are_code(self):
        out = {"decision": "reject", "findings": [
            {"id": "C1", "severity": "block", "status": "open", "ac": "writes:"},
            {"id": "F1", "severity": "block", "status": "open"},
            {"id": "F2", "severity": "warn", "status": "open"},
            {"id": "F3", "severity": "block", "status": "resolved", "charter": "charter-1"}]}
        self.assertEqual(verdict_eval.block_count(out), 2.0)
        self.assertEqual(verdict_eval.finding_count(out), 4.0)
        self.assertAlmostEqual(verdict_eval.citation_compliance(out), 2 / 3)
        self.assertEqual(verdict_eval.citation_compliance({"findings": []}), 1.0)
        self.assertEqual(verdict_eval.decision_agreement(out, "reject"), 1.0)
        self.assertEqual(verdict_eval.decision_agreement(out, "ship"), 0.0)
        self.assertIsNone(verdict_eval.decision_agreement(out, None))
        self.assertEqual(verdict_eval.block_count({}), 0.0)

    def test_cli_without_opik_exits_2_after_counting(self):
        with mock.patch.dict(os.environ, {k: "" for k in runner.OPIK_ENV}):
            for k in runner.OPIK_ENV:
                os.environ.pop(k, None)
            r = subprocess.run([sys.executable, str(REPO / "scripts/verdict_eval.py"), str(self.tmp)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("1 packets, 0 with an expected decision", r.stdout)


if __name__ == "__main__":
    unittest.main()
