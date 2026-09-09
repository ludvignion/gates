"""lint_kanban.py: closed tickets only grow their append-only sections; findings have homes;
no ship past an open block; approved plans carry a routing stamp; charter items say what they reach;
approved plans name every spec id their brief cites."""
import json
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

    def test_charter_item_without_applies_to_fails(self):
        """Rule 5 (E22): every charter item names the paths it reaches."""
        charter = self.tmp / "docs" / "domain-pack" / "charter.md"; charter.parent.mkdir(parents=True)
        charter.write_text("# Charter\n\n## 1. Unknown over guess\nApplies to: src/*\nPattern: x\n\n## 2. Evidence is openable\nPattern: y\n\n## 3. Docs say what is\ntext\n")
        self.assertEqual(lint_kanban.charter_reach(self.tmp), [
            "docs/domain-pack/charter.md: charter item 2 has no Applies to: line",
            "docs/domain-pack/charter.md: charter item 3 has no Applies to: line"])
        self.assertEqual(lint_kanban.lint(self.tmp), lint_kanban.charter_reach(self.tmp))
        charter.write_text(charter.read_text().replace("Pattern: y", "Applies to: docs/*\nPattern: y").replace("text\n", "Applies to: docs/*, tests/*\n"))
        self.assertEqual(lint_kanban.lint(self.tmp), [])
        charter.unlink(); self.assertEqual(lint_kanban.charter_reach(self.tmp), [])  # no charter, nothing to say

    def test_unborn_head_is_quiet_and_a_bad_named_base_is_reported(self):
        """0.8.1: init runs ci before its first commit; a CI base that does not resolve must not pass silently."""
        fresh = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, fresh, ignore_errors=True)
        shutil.copytree(FIXTURE, fresh / "kanban"); git(fresh, "init", "-q")
        self.assertEqual(lint_kanban.closed_tickets(fresh), [])
        self.assertEqual(lint_kanban.lint(self.tmp, "origin/nope"), ["lint_kanban: base origin/nope does not resolve; rule 1 (closed tickets) not checked"])

    def test_cli_exit_code(self):
        self.done.write_text(self.done.read_text().replace("One sentence.", "Two sentences."))
        res = subprocess.run([sys.executable, str(REPO / "scripts" / "lint_kanban.py")], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("## Outcome changed", res.stdout)


def _append_log(path: Path, entry: str) -> None:
    path.write_text(path.read_text().rstrip("\n") + "\n" + entry.rstrip("\n") + "\n")


class FindingHomesTest(unittest.TestCase):
    """Rule 2: every ``— finding:`` Log entry resolves to a ticket file, a waiver, or a close-out."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURE, self.tmp / "kanban")
        git(self.tmp, "init", "-q")
        git(self.tmp, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        git(self.tmp, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "fixture")
        self.ready = self.tmp / "kanban" / "tickets" / "1.4.merge-stage.md"
        self.done = self.tmp / "kanban" / "tickets" / "1.1.tracer-bullet.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_homed_to_existing_ticket_passes(self):
        _append_log(self.ready, "### [build] 2026-09-02 — finding: merge drops the count\nBelongs with 1.5 (home: 1.5).")
        self.assertEqual(lint_kanban.finding_homes(self.tmp), [])

    def test_homed_to_missing_ticket_fails(self):
        _append_log(self.ready, "### [build] 2026-09-02 — finding: merge drops the count\nhome: 1.9")
        v = lint_kanban.finding_homes(self.tmp)
        self.assertEqual(len(v), 1)
        self.assertIn("ticket 1.4 finding homed to ticket 1.9, which has no file: merge drops the count", v[0])

    def test_explicit_home_wins_over_other_ids(self):
        _append_log(self.ready, "### [build] 2026-09-02 — finding: 1.3 F8 applies here too\nhomed to 1.9")
        v = lint_kanban.finding_homes(self.tmp)
        self.assertEqual(len(v), 1); self.assertIn("homed to ticket 1.9", v[0]); self.assertNotIn("candidate", v[0])

    def test_no_home_at_all_fails(self):
        _append_log(self.ready, "### [build] 2026-09-02 — finding: off-by-one in merge, not touched")
        v = lint_kanban.finding_homes(self.tmp)
        self.assertEqual(len(v), 1)
        self.assertIn("ticket 1.4 finding has no home", v[0]); self.assertIn("off-by-one in merge", v[0])
        self.assertNotIn("candidate", v[0])

    def test_bare_ticket_ids_are_candidates_not_homes(self):
        _append_log(self.ready, "### [build] 2026-09-02 — finding: merge drops the count\nBelongs with 1.5, see also 1.3.")
        v = lint_kanban.finding_homes(self.tmp)
        self.assertEqual(len(v), 1)
        self.assertIn("ticket 1.4 finding has no home", v[0])
        self.assertIn("(candidate homes seen: 1.5, 1.3 — add 'home:' if intended)", v[0])

    def test_bare_id_of_done_ticket_does_not_bind_it(self):
        _append_log(self.ready, "### [build] 2026-09-02 — finding: stub outputs lack a header\nfrom 1.1")
        v = lint_kanban.finding_homes(self.tmp)
        self.assertEqual(len(v), 1); self.assertIn("ticket 1.4 finding has no home", v[0])

    def test_human_waiver_naming_it_passes(self):
        _append_log(self.ready, "### [build] 2026-09-02 — finding: off-by-one in merge, not touched\n"
                                "### [human] 2026-09-03 — waived: `off-by-one in merge, not touched` is by design")
        self.assertEqual(lint_kanban.finding_homes(self.tmp), [])

    def test_closeout_fix_passes(self):
        _append_log(self.ready, "### [build] 2026-09-02 — finding: off-by-one in merge, not touched\n"
                                "### [build] 2026-09-02 — close-out\n- fixed: off-by-one in merge, not touched → AC-1")
        self.assertEqual(lint_kanban.finding_homes(self.tmp), [])

    def test_done_ticket_with_unaddressed_homed_finding_fails(self):
        _append_log(self.ready, "### [build] 2026-09-02 — finding: stub outputs lack a header\nhome: 1.1")
        v = lint_kanban.finding_homes(self.tmp)
        self.assertEqual(len(v), 1)
        self.assertIn("ticket 1.1 is done but the finding homed here from 1.4 is unaddressed in its Log: stub outputs lack a header", v[0])
        _append_log(self.done, "### [human] 2026-09-03 — 1.4 finding: stub outputs lack a header — accepted, not a slice")
        self.assertEqual(lint_kanban.finding_homes(self.tmp), [])

    def test_open_ticket_with_homed_finding_passes(self):
        _append_log(self.done, "### [build] 2026-09-02 — finding: merge needs the count\nhome: 1.4")
        self.assertEqual(lint_kanban.finding_homes(self.tmp), [])


class OpenBlocksTest(unittest.TestCase):
    """Rule 3: no ``[verdict] — ship`` entry or ``status: done`` past an open block without a waiver."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURE, self.tmp / "kanban")
        self.review = self.tmp / "kanban" / "tickets" / "1.2.match-stage.md"
        self.done = self.tmp / "kanban" / "tickets" / "1.1.tracer-bullet.md"
        self.vd = self.tmp / "traces" / "verdict"; self.vd.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _verdict(self, tid: str, status: str = "open", severity: str = "block") -> None:
        (self.vd / f"{tid}.json").write_text(json.dumps({"ticket": tid, "decision": "reject", "findings": [
            {"id": "F1", "severity": severity, "status": status, "ac": "AC-1", "text": "ids drift"}]}))

    def test_ship_entry_past_open_block_fails(self):
        self._verdict("1.2")
        _append_log(self.review, "### [verdict] 2026-09-02 12:00 — ship")
        v = lint_kanban.open_blocks(self.tmp)
        self.assertEqual(len(v), 1)
        self.assertIn("ticket 1.2 has a [verdict] ship entry with open block F1 and no [human] waiver naming it: ids drift", v[0])

    def test_done_past_open_block_fails(self):
        self._verdict("1.1")
        v = lint_kanban.open_blocks(self.tmp)
        self.assertEqual(len(v), 1); self.assertIn("ticket 1.1 is done with open block F1", v[0])

    def test_waiver_naming_the_id_passes(self):
        self._verdict("1.1")
        _append_log(self.done, "### [human] 2026-09-02 11:03 — waive F1, ids are stable enough")
        self.assertEqual(lint_kanban.open_blocks(self.tmp), [])

    def test_waiver_naming_another_id_fails(self):
        self._verdict("1.1")
        _append_log(self.done, "### [human] 2026-09-02 11:03 — waive F2")
        self.assertEqual(len(lint_kanban.open_blocks(self.tmp)), 1)

    def test_reject_entry_or_resolved_block_passes(self):
        self._verdict("1.2")
        _append_log(self.review, "### [verdict] 2026-09-02 12:00 — reject\n- block F1 AC-1: ids drift")
        self.assertEqual(lint_kanban.open_blocks(self.tmp), [])
        self._verdict("1.1", status="resolved")
        self._verdict("1.2", severity="warn")
        self.assertEqual(lint_kanban.open_blocks(self.tmp), [])


class RoutingStampTest(unittest.TestCase):
    """Rule 4: an approved plan carries signals, scrutiny and backend."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURE, self.tmp / "kanban")
        self.plan = self.tmp / "kanban" / "plans" / "2.plan.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stamped_fixture_passes(self):
        self.assertEqual(lint_kanban.routing_stamps(self.tmp), [])

    def test_missing_signal_key_and_backend_fail(self):
        s = self.plan.read_text().replace("  tickets: 3\n", "").replace("backend: session\n", "")
        self.plan.write_text(s)
        v = lint_kanban.routing_stamps(self.tmp)
        self.assertEqual(v, ["kanban/plans/2.plan.md: plan 2 is approved but its frontmatter lacks signals.tickets, backend"])

    def test_missing_whole_stamp_fails(self):
        self.plan.write_text("---\nbrief: 2\nstatus: approved\napproved: reviewer 2026-09-01\n---\n# 2 x\n")
        v = lint_kanban.routing_stamps(self.tmp)
        self.assertEqual(v, ["kanban/plans/2.plan.md: plan 2 is approved but its frontmatter lacks signals, scrutiny, backend"])

    def test_unapproved_plan_is_exempt(self):
        self.plan.write_text("---\nbrief: 2\nstatus: draft\napproved:\n---\n# 2 x\n")
        self.assertEqual(lint_kanban.routing_stamps(self.tmp), [])


class LogSchemaTest(unittest.TestCase):
    def test_log_entries_findings_and_stamp(self):
        body = ("## Log (append-only)\n### [grill] 2026-09-01 — created\n"
                "### [build] 2026-09-02 — finding: `x` leaks\nsee 1.3 and 1.4\n"
                "### [human] 2026-09-02 — waive F1 and C2\n### [verdict] 2026-09-02 — ship, F4 waived\n")
        log = schemas.Log.parse(body)
        self.assertEqual([e.role for e in log.entries], ["grill", "build", "human", "verdict"])
        f = log.findings("1.4")
        self.assertEqual(len(f), 1); self.assertEqual(f[0].title, "`x` leaks")
        self.assertEqual(f[0].homes, ()); self.assertEqual(f[0].candidates, ("1.3",))
        g = schemas.Log.parse("## Log\n### [build] — finding: y\nsee 1.3; home: 1.4, also 1.5\n").findings("1.2")
        self.assertEqual(g[0].homes, ("1.4",)); self.assertEqual(g[0].candidates, ("1.3", "1.5"))
        self.assertEqual(log.waived_ids(), {"F1", "C2"}); self.assertTrue(log.shipped())
        self.assertEqual(schemas.RoutingStamp.parse("---\napproved: x\n---\n").missing(), ("signals", "scrutiny", "backend"))
        self.assertEqual(schemas.TICKET_ID_RE.findall("costs $1.50, 3.5x slower, v0.5.1, ticket 1.3.1 and 2.1."), ["1.3.1", "2.1"])


class ClosedTicketDiffTest(unittest.TestCase):
    def test_identical_is_legal(self):
        t = "---\nid: 1.1\nstatus: done\n---\n# x\n## Log (append-only)\na\n"
        self.assertEqual(schemas.ClosedTicketDiff.compare(t, t).violations, ())


if __name__ == "__main__":
    unittest.main()


class SpecIdsInAcsTest(unittest.TestCase):
    """Rule 6: an approved plan whose brief cites spec ids names each one as [id] on an AC line."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURE, self.tmp / "kanban")
        (self.tmp / "kanban/briefs").mkdir()
        git(self.tmp, "init", "-q")
        git(self.tmp, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        git(self.tmp, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "fixture")
        self.plan = self.tmp / "kanban" / "plans" / "2.plan.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_and_named_ids(self):
        self.assertEqual(lint_kanban.spec_ids_in_acs(self.tmp), [])  # no brief file, nothing to say
        (self.tmp / "kanban/briefs/2-models.md").write_text("---\nspec_refs: [contacts/call-log, crm-77]\nafter: []\n---\n# Brief\n")
        self.assertEqual(lint_kanban.spec_ids_in_acs(self.tmp), [
            "kanban/plans/2.plan.md: plan 2 is approved but no AC line names [contacts/call-log] from brief 2",
            "kanban/plans/2.plan.md: plan 2 is approved but no AC line names [crm-77] from brief 2"])
        self.assertEqual(lint_kanban.lint(self.tmp), lint_kanban.spec_ids_in_acs(self.tmp))
        s = self.plan.read_text().replace("Then it exports the record models.", "Then it exports the record models [contacts/call-log].")
        s = s.replace("Then it passes.", "Then it passes [crm-77].\n\nProse naming [contacts/call-log] outside an AC line does not count.")
        self.plan.write_text(s)
        self.assertEqual(lint_kanban.lint(self.tmp), [])
        self.plan.write_text(self.plan.read_text().replace("approved: reviewer 2026-09-01", "approved:").replace("[crm-77]", ""))
        self.assertEqual(lint_kanban.spec_ids_in_acs(self.tmp), [])  # unapproved: exempt
