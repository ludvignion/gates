"""runner.py verdict seat (--verdict-cmd, --arm, stamp, opik-absent path) and verdict_eval.py."""
import contextlib
import io
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
        self.verdict = lambda **kw: runner.verdict(self.tmp, "1.1", "opus", **{"template": self.cmd, "summary_model": "none", **kw})
        self.env = mock.patch.dict(os.environ, {k: "" for k in runner.OPIK_ENV})
        self.env.start()
        for k in runner.OPIK_ENV:
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_cmd_and_placeholders(self):
        self.assertEqual(runner.DEFAULT_VERDICT_CMD, 'claude -p --model {model} --output-format json --tools "" < {packet}')
        cmd = runner.verdict_cmd(runner.DEFAULT_VERDICT_CMD, packet="traces/verdict/1.1.input.md", output="traces/verdict/1.1.json", model="opus", ticket="1.1")
        self.assertEqual(cmd, 'claude -p --model opus --output-format json --tools "" < traces/verdict/1.1.input.md')
        self.assertNotIn("-p '/verdict", cmd)  # the skill is not invoked; the path merely contains the word
        self.assertNotIn("$(cat", cmd)  # the packet is stdin, never an argument
        self.assertEqual(runner.vendor_of(runner.DEFAULT_VERDICT_CMD), "claude")
        self.assertEqual(runner.vendor_of("/usr/local/bin/codex exec --json {packet} > {output}"), "codex")
        for ph in ("{packet}", "{output}", "{model}", "{ticket}"):
            self.assertIn(ph, runner.VERDICT_CMD_HELP)

    def test_vendor_usage_from_claude_json_output(self):
        claude_out = json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.4321, "duration_ms": 9000,
                                 "usage": {"input_tokens": 12, "output_tokens": 34, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 600}, "result": "done"})
        self.assertEqual(runner.vendor_usage(claude_out), (0.4321, {"input_tokens": 12, "output_tokens": 34, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 600}))
        self.assertEqual(runner.vendor_usage("plain text from another vendor"), (None, None))
        self.assertEqual(runner.vendor_usage(""), (None, None))
        self.assertEqual(runner.vendor_usage('{"result": "no usage"}'), (None, None))
        self.assertEqual(runner.vendor_usage('[1, 2]'), (None, None))

    def test_verdict_from_result(self):
        verdict = {"ticket": "1.1", "decision": "ship", "held": [], "findings": [], "ci": {"green": True}}
        fenced = "Here it is:\n```json\n" + json.dumps(verdict, indent=1) + "\n```\n"
        self.assertEqual(runner.verdict_from_result(json.dumps({"type": "result", "result": fenced})), verdict)
        self.assertEqual(runner.verdict_from_result(json.dumps({"result": json.dumps(verdict)})), verdict)
        self.assertEqual(runner.verdict_from_result(json.dumps({"result": '{"held": []} then ' + json.dumps(verdict)})), verdict)
        self.assertIsNone(runner.verdict_from_result(json.dumps({"result": "no json here {oops"})))
        self.assertIsNone(runner.verdict_from_result(json.dumps({"result": {"decision": "ship"}})))  # result is text
        self.assertIsNone(runner.verdict_from_result("not a report"))
        self.assertIsNone(runner.verdict_from_result(json.dumps({"type": "result", "is_error": True})))

    def test_reply_only_vendor_on_stdin_gets_its_output_written(self):
        """The default seat: the packet arrives on stdin (it starts with ---, which no argv parser
        survives), the model replies with the verdict, writes nothing; the runner writes {output}."""
        reply = "```json\n" + json.dumps({"ticket": "1.1", "decision": "reject", "held": [], "ci": {"green": True},
                                           "findings": [{"id": "F1", "severity": "block", "status": "open", "ac": "AC-1", "text": "x"}]}) + "\n```"
        (self.tmp / "reply_vendor.py").write_text(
            "import json, sys\nassert sys.argv[1:] == ['--model', 'opus'], sys.argv\n"
            "text = sys.stdin.read()\nassert text.startswith('---\\nticket: 1.1'), text[:30]\n"
            "print(json.dumps({'type': 'result', 'result': " + repr(reply) + ", 'total_cost_usd': 0.01, 'usage': {'output_tokens': 9}}))\n")
        template = f"{sys.executable} reply_vendor.py --model {{model}} < {{packet}}"
        decision, retryable, progressed = self.verdict(template=template)
        self.assertEqual((decision, retryable, progressed), ("reject", True, True))
        v = schemas.Verdict.load(self.tmp / "traces/verdict/1.1.json")
        self.assertEqual(v.findings[0]["id"], "F1"); self.assertEqual(v.meta.cost_usd, 0.01)
        self.assertTrue((self.tmp / "traces/verdict/1.1.input.md").read_text().startswith("---\n"))

    def test_vendor_errors_are_rejects_and_traced(self):
        """Non-zero exit, empty result, and JSON that fails the Verdict schema: each an error."""
        (self.tmp / "bad_json.py").write_text("import json, sys\njson.dump({'decision': 'maybe', 'findings': [{'severity': 'block'}]}, open(sys.argv[1], 'w'))\n")
        (self.tmp / "not_json.py").write_text("import sys\nopen(sys.argv[1], 'w').write('not json')\n")
        cases = {
            "exit 3": "exit 3",
            "empty result": "true",
            "invalid verdict: decision 'maybe' not in ('ship', 'reject'); findings[0] has no id": f"{sys.executable} bad_json.py {{output}}",
            "invalid verdict: Expecting value": f"{sys.executable} not_json.py {{output}}",
        }
        for expected_error, template in cases.items():
            with self.subTest(expected_error):
                calls = {}

                class Client:
                    def trace(self, **kw): calls["trace"] = kw
                    def flush(self): pass

                fake = types.ModuleType("opik"); fake.Opik = Client
                out = self.tmp / "traces/verdict/1.1.json"
                out.unlink(missing_ok=True)
                with mock.patch.dict(os.environ, {"OPIK_URL_OVERRIDE": "http://localhost:5173/api"}), mock.patch.dict(sys.modules, {"opik": fake}), \
                        contextlib.redirect_stdout(io.StringIO()) as printed:
                    self.assertEqual(self.verdict(template=template), ("reject", True, True))
                self.assertIn(f"[runner] verdict error: {expected_error}", printed.getvalue())
                err = calls["trace"]["output"]["error"]
                self.assertTrue(err["reason"].startswith(expected_error))
                self.assertEqual(sorted(err), sorted(("reason", "stderr_tail") + runner.ENVELOPE_FIELDS))
                self.assertFalse((self.tmp / "traces/verdict/1.1.html").exists())  # no close-out on an error

    def test_error_envelope_carries_cli_fields_and_stderr(self):
        envelope = {"type": "result", "subtype": "error_max_turns", "is_error": True, "stop_reason": "max_turns",
                    "num_turns": 3, "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "ls"}}],
                    "result": "gave up", "total_cost_usd": 0.02, "usage": {"output_tokens": 4}}
        (self.tmp / "failing_vendor.py").write_text(
            "import json, sys\nprint(json.dumps(" + repr(envelope) + "))\n"
            "sys.stderr.write('line1\\nline2\\nboom: rate limited\\n')\nsys.exit(1)\n")
        calls = {}

        class Client:
            def trace(self, **kw): calls["trace"] = kw
            def flush(self): pass

        fake = types.ModuleType("opik"); fake.Opik = Client
        with mock.patch.dict(os.environ, {"OPIK_URL_OVERRIDE": "http://localhost:5173/api"}), mock.patch.dict(sys.modules, {"opik": fake}), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.verdict(template=f"{sys.executable} failing_vendor.py < {{packet}}"), ("reject", True, True))
        err = calls["trace"]["output"]["error"]
        self.assertEqual(err, {"reason": "exit 1", "stop_reason": "max_turns", "num_turns": 3, "is_error": True,
                               "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "ls"}}],
                               "stderr_tail": "line1\nline2\nboom: rate limited"})
        self.assertEqual(runner.error_envelope("empty result", "not json", "")["stop_reason"], None)
        self.assertEqual(runner.error_envelope("exit 2", "", "\n".join(str(i) for i in range(50)))["stderr_tail"], "\n".join(str(i) for i in range(30, 50)))

    def test_call_error(self):
        out = self.tmp / "v.json"
        self.assertEqual(runner.call_error(1, out), "exit 1")
        self.assertEqual(runner.call_error(0, out), "empty result")
        out.write_text(json.dumps({"decision": "ship", "findings": [{"id": "F1", "severity": "warn", "status": "resolved"}]}))
        self.assertIsNone(runner.call_error(0, out))
        out.write_text(json.dumps({"decision": "ship", "findings": [{"id": "F1", "severity": "warn", "status": "gone"}]}))
        self.assertEqual(runner.call_error(0, out), "invalid verdict: F1: status 'gone' not in ('open', 'resolved')")
        out.write_text("[]")
        self.assertEqual(runner.call_error(0, out), "invalid verdict: not a verdict JSON object")

    def test_claude_usage_lands_in_meta_and_trace(self):
        (self.tmp / "fake_vendor.py").write_text(FAKE_VENDOR + 'print(json.dumps({"total_cost_usd": 0.05, "usage": {"input_tokens": 7, "output_tokens": 8}}))\n')
        calls = {}

        class Client:
            def trace(self, **kw): calls["trace"] = kw
            def flush(self): pass

        fake = types.ModuleType("opik"); fake.Opik = Client
        with mock.patch.dict(os.environ, {"OPIK_URL_OVERRIDE": "http://localhost:5173/api"}), mock.patch.dict(sys.modules, {"opik": fake}):
            self.verdict()
        meta = schemas.Verdict.load(self.tmp / "traces/verdict/1.1.json").meta
        self.assertEqual((meta.cost_usd, meta.tokens), (0.05, {"input_tokens": 7, "output_tokens": 8}))
        self.assertEqual(calls["trace"]["metadata"]["cost_usd"], 0.05)
        self.assertEqual(calls["trace"]["metadata"]["tokens"], {"input_tokens": 7, "output_tokens": 8})

    def test_other_vendor_leaves_usage_null(self):
        self.verdict()
        meta = schemas.Verdict.load(self.tmp / "traces/verdict/1.1.json").meta
        self.assertEqual((meta.cost_usd, meta.tokens), (None, None))
        self.assertIn('"cost_usd": null', (self.tmp / "traces/verdict/1.1.json").read_text())

    def test_verdict_runs_cmd_stamps_and_reads_decision(self):
        decision, retryable, progressed = self.verdict(arm="packet")
        self.assertEqual((decision, retryable, progressed), ("reject", True, True))
        v = schemas.Verdict.load(self.tmp / "traces/verdict/1.1.json")
        self.assertEqual(v.meta.arm, "packet"); self.assertEqual(v.meta.vendor, Path(sys.executable).stem)
        self.assertEqual(v.meta.packet_sha, schemas.sha256((self.tmp / "traces/verdict/1.1.input.md").read_bytes()))
        self.assertTrue((self.tmp / "traces/verdict/1.1.html").exists())
        self.assertEqual(schemas.Packet.load(self.tmp / "traces/verdict/1.1.input.md").arm, "packet")

    def test_blind_arm_downgrades_and_ships(self):
        decision, retryable, progressed = self.verdict(arm="blind")
        self.assertEqual(decision, "ship")
        self.assertEqual(schemas.Verdict.load(self.tmp / "traces/verdict/1.1.json").meta.arm, "blind")
        self.assertEqual(schemas.Packet.load(self.tmp / "traces/verdict/1.1.input.md").arm, "blind")

    def test_missing_output_is_reject(self):
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(self.verdict(template="true"), ("reject", True, True))
        self.assertIn("[runner] verdict error: empty result", printed.getvalue())

    def test_previous_verdict_archived_and_progress_tracked(self):
        vd = self.tmp / "traces/verdict"; vd.mkdir(parents=True)
        (vd / "1.1.json").write_text(json.dumps({"decision": "reject", "findings": [{"id": "F1", "severity": "block", "status": "open", "ac": "AC-1", "text": "same"}]}))
        decision, retryable, progressed = self.verdict()
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
        self.verdict()
        self.assertTrue((self.tmp / "traces/verdict/1.1.json").exists())

    def test_opik_configured_wraps_one_trace(self):
        calls = {}

        class Client:
            def trace(self, **kw): calls["trace"] = kw; calls["n"] = calls.get("n", 0) + 1
            def flush(self): calls["flushed"] = True

        fake = types.ModuleType("opik"); fake.Opik = Client
        with mock.patch.dict(os.environ, {"OPIK_URL_OVERRIDE": "http://localhost:5173/api"}), mock.patch.dict(sys.modules, {"opik": fake}):
            self.verdict(arm="blind")
        packet = (self.tmp / "traces/verdict/1.1.input.md").read_text()
        self.assertEqual(calls["trace"]["name"], "verdict")
        self.assertEqual(calls["trace"]["input"], {"packet": packet})
        md = calls["trace"]["metadata"]
        self.assertEqual(md["ticket"], "1.1"); self.assertEqual(md["arm"], "blind"); self.assertIn("wall_seconds", md)
        self.assertEqual(sorted(k for k in md if k in schemas.META_FIELDS), sorted(schemas.META_FIELDS))
        expected = json.loads((self.tmp / "traces/verdict/1.1.json").read_text())
        expected["summary"] = json.loads((self.tmp / "traces/verdict/1.1.summary.json").read_text())
        self.assertEqual(calls["trace"]["output"], expected)
        self.assertIn("summary skipped", expected["summary"]["error"])
        self.assertGreaterEqual(calls["trace"]["end_time"], calls["trace"]["start_time"])
        self.assertEqual(calls["n"], 1); self.assertTrue(calls["flushed"])

    def test_verdict_artifacts_committed_on_the_branch(self):
        """E11: the four verdict files are committed on ticket/1.1 right after the verdict."""
        (self.tmp / ".gitignore").write_text("traces/verdict/*.html\n")
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "ignore html")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.verdict()
        self.assertIn("[runner] verdict artifacts committed ", out.getvalue())
        log = subprocess.run(["git", "log", "--format=%s %an", "-1"], cwd=self.tmp, capture_output=True, text=True).stdout
        self.assertEqual(log.strip(), "docs(1.1): verdict reject harness-runner")
        tracked = subprocess.run(["git", "ls-files", "traces/verdict"], cwd=self.tmp, capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(tracked), ["traces/verdict/1.1.html", "traces/verdict/1.1.input.md", "traces/verdict/1.1.json", "traces/verdict/1.1.summary.json"])
        self.assertEqual(subprocess.run(["git", "status", "--short"], cwd=self.tmp, capture_output=True, text=True).stdout.strip(), "")

    def test_opik_line_printed_and_stamped(self):
        """E12: the runner says whether it traces, at start and in the seat line."""
        self.assertEqual(runner.opik_status()[1], "untraced (OPIK_URL_OVERRIDE unset)")
        with mock.patch.dict(os.environ, {"OPIK_URL_OVERRIDE": "http://localhost:5173/api"}), mock.patch.dict(sys.modules, {"opik": None}):
            self.assertEqual(runner.opik_status()[1], "untraced (opik package not importable)")
        fake = types.ModuleType("opik"); fake.Opik = lambda: object()
        with mock.patch.dict(os.environ, {"OPIK_URL_OVERRIDE": "http://localhost:5173/api"}), mock.patch.dict(sys.modules, {"opik": fake}):
            self.assertEqual(runner.opik_status()[1], "tracing to http://localhost:5173/api")
        with contextlib.redirect_stdout(io.StringIO()):
            self.verdict()
        meta = schemas.Verdict.load(self.tmp / "traces/verdict/1.1.json").meta
        self.assertEqual(meta.opik, "untraced (OPIK_URL_OVERRIDE unset)"); self.assertGreater(meta.seconds, 0)
        self.assertIn("untraced (OPIK_URL_OVERRIDE unset)", (self.tmp / "traces/verdict/1.1.html").read_text())

    def test_cli_help_documents_placeholders(self):
        r = subprocess.run([sys.executable, str(REPO / "scripts/runner.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        for s in ("--verdict-cmd", "--arm", "{packet}", "{output}", "blind", "repo"):
            self.assertIn(s, r.stdout)


FAKE_CLAUDE = """#!/bin/sh
# stand-in claude: records the call, prints the envelope from $FAKE_CLAUDE_ENVELOPE
echo "$*" >> "$FAKE_CLAUDE_LOG"
[ -n "$FAKE_CLAUDE_BREAK" ] && rm -f green   # a build that leaves ci red
cat "$FAKE_CLAUDE_ENVELOPE"
"""


class RunnerBuildSeatTest(unittest.TestCase):
    """The headless build session: allowlisted shell, prompts denied not hung, denial stops the run."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "proj"
        for rel, text in {
            "kanban/plans/1.plan.md": PLAN, "kanban/tickets/1.1.tracer-bullet.md": TICKET,
            "Makefile": "ci:\n\t@test -f green\n",
        }.items():
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(text)
        (self.repo / "green").write_text("")  # baseline ci green; the fake build turns it red
        git(self.repo, "init", "-q", "-b", "main"); git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "base")
        bin_dir = self.tmp / "bin"; bin_dir.mkdir()
        (bin_dir / "claude").write_text(FAKE_CLAUDE); (bin_dir / "claude").chmod(0o755)
        self.log = self.tmp / "calls.log"; self.envelope = self.tmp / "envelope.json"
        self.env = mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                                                "FAKE_CLAUDE_LOG": str(self.log), "FAKE_CLAUDE_ENVELOPE": str(self.envelope)})
        self.env.start()
        for k in runner.OPIK_ENV:
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo, capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _main(self, *extra: str) -> tuple[int, str]:
        with mock.patch.object(sys, "argv", ["runner.py", "1.1", "--cwd", str(self.repo), "--max-retries", "2", "--summary-model", "none", *extra]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            rc = runner.main()
        return rc, out.getvalue()

    def test_build_cmd_is_headless_with_shell_allowlist(self):
        cmd = runner.build_cmd("1.1", "sonnet")
        self.assertEqual(cmd[:3], ["claude", "-p", "/build 1.1"])
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(cmd[cmd.index("--permission-prompts") + 1], "none")
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "json")
        allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
        self.assertEqual(tuple(allowed), runner.BUILD_ALLOWED_TOOLS)
        for tool in ("Bash(make *)", "Bash(uv *)", "Bash(git *)", "Bash(pytest *)"):
            self.assertIn(tool, allowed)
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_permission_denial_stops_without_retry(self):
        self.envelope.write_text(json.dumps({
            "type": "result", "is_error": False, "num_turns": 4, "result": "Blocked on git add approval",
            "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "git add -A"}}]}))
        (self.repo / "green").unlink()  # ci would be red too: the denial must win
        rc, out = self._main()
        self.assertEqual(rc, 4, out)
        self.assertTrue(out.startswith("[runner] opik: untraced (OPIK_URL_OVERRIDE unset)\n"), out[:80])
        self.assertIn("[runner] permission denied: Bash(git add -A)", out)
        self.assertNotIn("ci red", out)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)  # one build call, no retry
        self.assertIn("--permission-prompts none", self.log.read_text())

    def test_clean_build_with_red_ci_still_retries(self):
        self.envelope.write_text(json.dumps({"type": "result", "is_error": False, "num_turns": 2, "result": "done", "permission_denials": []}))
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_BREAK": "1"}):
            rc, out = self._main()
        self.assertEqual(rc, 1, out)  # retry cap: the fake build never turns ci green
        self.assertEqual(out.count("[runner] ci red"), 2)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)
        self.assertNotIn("permission denied", out)

    def test_build_call_parses_envelope(self):
        self.envelope.write_text(json.dumps({"result": "ok", "permission_denials": [{"tool_name": "WebFetch", "tool_input": {"url": "x"}}, "junk"]}))
        with contextlib.redirect_stdout(io.StringIO()):
            call = runner.build(self.repo, "1.1", "sonnet")
        self.assertEqual((call.returncode, call.result, call.denied), (0, "ok", "WebFetch"))
        self.envelope.write_text("not an envelope")
        with contextlib.redirect_stdout(io.StringIO()):
            call = runner.build(self.repo, "1.1", "sonnet")
        self.assertEqual((call.denials, call.result), ((), "not an envelope"))


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

    def test_items_keyed_on_packet_sha(self):
        import uuid
        rows = verdict_eval.items(self.tmp)
        self.assertEqual(rows[0]["id"], verdict_eval.item_id(rows[0]["packet_sha"]))
        self.assertEqual(verdict_eval.items(self.tmp)[0]["id"], rows[0]["id"])  # a rerun maps to the same item
        u = uuid.UUID(rows[0]["id"]); self.assertEqual((u.version, u.variant), (7, uuid.RFC_4122))
        self.assertNotEqual(verdict_eval.item_id("a" * 64), verdict_eval.item_id("b" * 64))

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
        self.assertIsNone(out["error"])
        with self.assertRaises(ValueError):
            verdict_eval.run_one({**item, "packet": schemas.Packet.parse(item["packet"]).rearm("blind").render()}, "packet", cmd)

    def test_run_one_marks_errors(self):
        item = verdict_eval.items(self.tmp)[0]
        (self.tmp / "bad_json.py").write_text("import json, sys\njson.dump({'decision': 'maybe'}, open(sys.argv[1], 'w'))\n")
        for expected_error, template in {
            "exit 2": "exit 2",
            "empty result": "true",
            "invalid verdict: decision 'maybe' not in ('ship', 'reject')": f"{sys.executable} {self.tmp / 'bad_json.py'} {{output}}",
        }.items():
            with self.subTest(expected_error):
                r = verdict_eval.run_one(item, "packet", template)
                self.assertEqual(r["error"], expected_error)
                self.assertEqual(r["output"]["error"]["reason"], expected_error)
                self.assertIn("stderr_tail", r["output"]["error"])
                self.assertEqual(verdict_eval.block_count(r["output"]), 0.0)  # an envelope is not a verdict
                self.assertEqual(set(verdict_eval.score(r["output"], "ship", r["wall_seconds"], r["error"]).values()), {None})

    def test_summary_excludes_errors_and_counts_them(self):
        rows = [
            {"ticket": "1.1", "error": None, "block_count": 1.0, "finding_count": 2.0, "citation_compliance": 1.0, "decision_agreement": 1.0, "wall_seconds": 2.0},
            {"ticket": "1.2", "error": None, "block_count": 3.0, "finding_count": 4.0, "citation_compliance": 0.5, "decision_agreement": None, "wall_seconds": 4.0},
            {"ticket": "1.3", "error": "exit 1", "block_count": None, "finding_count": None, "citation_compliance": None, "decision_agreement": None, "wall_seconds": None},
        ]
        self.assertEqual(verdict_eval.summary(rows),
                         "[eval] 3 items, 1 errors, averages over 2: block_count=2.0 finding_count=3.0 citation_compliance=0.75 decision_agreement=1.0 wall_seconds=3.0")
        self.assertEqual(verdict_eval.summary([]), "[eval] 0 items, 0 errors, averages over 0: block_count=None finding_count=None citation_compliance=None decision_agreement=None wall_seconds=None")

    def test_sync_dataset_replaces_and_deletes_stale(self):
        rows = verdict_eval.items(self.tmp)
        stale_item = {"id": verdict_eval.item_id("f" * 64), "ticket": "9.9", "packet_sha": "f" * 64}

        class Dataset:
            def __init__(self): self.items = {stale_item["id"]: stale_item}; self.deleted = []
            def insert(self, items): self.items.update({it["id"]: it for it in items})
            def get_items(self): return list(self.items.values())
            def delete(self, ids): self.deleted += ids; [self.items.pop(i) for i in ids]

        ds = Dataset()
        self.assertEqual(verdict_eval.sync_dataset(ds, rows), [stale_item["id"]])
        self.assertEqual(set(ds.items), {rows[0]["id"]})
        self.assertEqual(verdict_eval.sync_dataset(ds, rows), [])  # a rerun: same id, nothing stale, nothing duplicated
        self.assertEqual(len(ds.items), 1)

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

    def test_cli_without_opik_scores_locally(self):
        with mock.patch.dict(os.environ, {k: "" for k in runner.OPIK_ENV}):
            for k in runner.OPIK_ENV:
                os.environ.pop(k, None)
            r = subprocess.run([sys.executable, str(REPO / "scripts/verdict_eval.py"), str(self.tmp), "--verdict-cmd", "true"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 packets, 0 with an expected decision", r.stdout)
        self.assertIn("scoring locally, nothing uploaded", r.stdout)
        self.assertIn("ticket=1.1 error=empty result block_count=None finding_count=None citation_compliance=None decision_agreement=None wall_seconds=None", r.stdout)
        self.assertIn("[eval] 1 items, 1 errors, averages over 0:", r.stdout)


if __name__ == "__main__":
    unittest.main()
