"""scripts/coverage.py: every spec id in exactly one brief or deferred, cited ids exist, no drift
since the brief's commit, after briefs exist without a cycle; silent without an index."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import _fm  # noqa: E402
import coverage  # noqa: E402
import spec_intake  # noqa: E402

SCRIPT = REPO / "scripts" / "coverage.py"
SPEC = "## Contacts\n\n### Call log\nLog every call.\n\n### Notes\nFree text.\n\n## Deals\n\n### Pipeline\nStages.\n"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def brief(refs: str, after: str = "[]") -> str:
    return f"---\nspec_refs: {refs}\nafter: {after}\n---\n\n# Brief: x\n\n## What I want\ny\n"


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "docs/spec").mkdir(parents=True); (self.tmp / "kanban/briefs").mkdir(parents=True)
        git(self.tmp, "init", "-q")

    def intake(self, text: str = SPEC) -> None:
        (self.tmp / "docs/spec/crm.md").write_text(text)
        r = subprocess.run([sys.executable, str(spec_intake.__file__), "docs/spec/crm.md"], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def write(self, rel: str, text: str) -> None:
        (self.tmp / rel).write_text(text)

    def run_cli(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), "."], cwd=self.tmp, capture_output=True, text=True)

    def test_no_index_prints_nothing_and_exits_0(self):
        self.write("kanban/briefs/1-a.md", "# Brief\n")
        proc = self.run_cli()
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))
        self.assertIsNone(coverage.lint(self.tmp))

    def test_partition_green(self):
        self.intake()
        self.write("kanban/briefs/1-a.md", brief("[contacts/call-log, contacts/call-log, contacts/notes]"))  # cited twice counts once
        self.write("docs/spec/deferred.md", "# Deferred\n\n- deals/pipeline — after the tracer bullet\n")
        self.assertEqual(coverage.lint(self.tmp), [])
        proc = self.run_cli()
        self.assertEqual((proc.returncode, proc.stdout), (0, "coverage: 0 violation(s) over 3 units\n"))

    def test_partition_violations(self):
        self.intake()
        self.write("kanban/briefs/1-a.md", brief("[contacts/call-log]"))
        self.write("kanban/briefs/2-b.md", brief("[contacts/call-log, contacts/notes]"))
        self.write("kanban/briefs/3-c.md", brief("[]"))
        self.write("kanban/briefs/0-example.md", "# Brief: <replace>\n")  # a 0.7.0 example brief: exempt
        self.write("docs/spec/deferred.md", "- contacts/notes — later\n- deals/pipeline\n")
        self.assertEqual(coverage.lint(self.tmp), [
            "kanban/briefs/2-b.md: [contacts/call-log] is also cited by brief 1; one brief per id",
            "kanban/briefs/3-c.md: brief 3 has no spec_refs while docs/spec/index.md exists",
            "kanban/briefs/2-b.md: [contacts/notes] is cited by brief 2 and deferred (docs/spec/deferred.md: line 1)",
            "docs/spec/deferred.md: line 2 has no reason: [deals/pipeline]",
        ])
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.splitlines()[-1], "coverage: 4 violation(s) over 3 units")

    def test_renamed_heading_names_the_brief_that_cited_the_old_id(self):
        self.intake()
        self.write("kanban/briefs/1-a.md", brief("[contacts/call-log, contacts/notes, deals/pipeline]"))
        self.assertEqual(coverage.lint(self.tmp), [])
        self.intake(SPEC.replace("### Call log", "### Call history"))
        self.assertEqual(coverage.lint(self.tmp), [
            "kanban/briefs/1-a.md: [contacts/call-log] is not in the index (renamed heading? re-run intake and update brief 1)",
            "docs/spec/index.md: [contacts/call-history] is in no brief and not deferred",
        ])

    def test_drift_after_the_brief_commit_but_not_in_it(self):
        self.intake()
        self.write("kanban/briefs/7-a.md", brief("[contacts/call-log, contacts/notes, deals/pipeline]"))
        self.assertEqual(coverage.lint(self.tmp), [])  # uncommitted: not judged
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "brief 7 with the index")
        self.assertEqual(coverage.lint(self.tmp), [])
        self.intake(SPEC.replace("Log every call.", "Log every call and its outcome."))
        self.assertEqual(coverage.lint(self.tmp), ["kanban/briefs/7-a.md: drift: [contacts/call-log] changed since brief 7"])
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 1); self.assertIn("drift: [contacts/call-log] changed since brief 7", proc.stdout)
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "spec edit")
        self.assertEqual(len(coverage.lint(self.tmp)), 1)  # committing the index alone does not clear it
        self.write("kanban/briefs/8-b.md", brief("[contacts/notes]"))  # a new brief committed with a changed unit: not drift for 8
        self.intake(SPEC.replace("Log every call.", "Log every call and its outcome.").replace("Free text.", "Free text, dated."))
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "brief 8 with its unit")
        self.assertEqual(coverage.lint(self.tmp), [
            "kanban/briefs/8-b.md: [contacts/notes] is also cited by brief 7; one brief per id",
            "kanban/briefs/7-a.md: drift: [contacts/call-log] changed since brief 7",
            "kanban/briefs/7-a.md: drift: [contacts/notes] changed since brief 7"])  # brief 7 was not touched in that commit

    def test_drift_is_acknowledged_by_editing_the_brief_with_the_index(self):
        """0.8.1 (item 4): the record is the last commit touching the brief; a reviewed brief committed
        with the new index is green, a brief edit committed without the index is not."""
        self.intake()
        self.write("kanban/briefs/7-a.md", brief("[contacts/call-log, contacts/notes, deals/pipeline]"))
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "brief 7")
        self.intake(SPEC.replace("Log every call.", "Log every call and its outcome."))
        self.assertEqual(coverage.lint(self.tmp), ["kanban/briefs/7-a.md: drift: [contacts/call-log] changed since brief 7"])
        self.write("kanban/briefs/7-a.md", brief("[contacts/call-log, contacts/notes, deals/pipeline]") + "\nReviewed against the outcome line.\n")
        self.assertEqual(len(coverage.lint(self.tmp)), 1)  # uncommitted edit: the record is still the old commit
        git(self.tmp, "add", "kanban"); git(self.tmp, "commit", "-q", "-m", "brief 7 reviewed, index left out")
        self.assertEqual(len(coverage.lint(self.tmp)), 1)  # the index at that commit is still the old one
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "index alone")
        self.assertEqual(coverage.lint(self.tmp), ["kanban/briefs/7-a.md: drift: [contacts/call-log] changed since brief 7"])
        self.write("kanban/briefs/7-a.md", (self.tmp / "kanban/briefs/7-a.md").read_text() + "Reviewed again.\n")
        git(self.tmp, "add", "-A"); git(self.tmp, "commit", "-q", "-m", "brief 7 reviewed with the index")
        self.assertEqual(coverage.lint(self.tmp), [])

    def test_many_small_units_warn_past_the_word_budget(self):
        """0.8.1 (item 1): 120 CSV rows in one brief is a warning on stderr, never a violation."""
        (self.tmp / "docs/spec/crm.csv").write_text("id,requirement\n" + "".join(f"CRM-{i},{'word ' * 40}\n" for i in range(120)))
        r = subprocess.run([sys.executable, str(spec_intake.__file__), "docs/spec/crm.csv"], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.write("kanban/briefs/1-all.md", brief("[" + ", ".join(f"CRM-{i}" for i in range(120)) + "]"))
        self.assertEqual(coverage.lint(self.tmp), [])
        proc = self.run_cli()
        self.assertEqual((proc.returncode, proc.stdout), (0, "coverage: 0 violation(s) over 120 units\n"))
        self.assertEqual(proc.stderr.strip(), "warn: kanban/briefs/1-all.md: brief 1 cites 4920 words over 120 units (over 4000); the verdict packet carries them all")

    def test_after_missing_and_cycle(self):
        self.intake()
        self.write("kanban/briefs/1-a.md", brief("[contacts/call-log]", "[]"))
        self.write("kanban/briefs/2-b.md", brief("[contacts/notes]", "[3]"))
        self.write("kanban/briefs/3-c.md", brief("[deals/pipeline]", "[2, 4]"))
        self.assertEqual(coverage.lint(self.tmp), [
            "kanban/briefs/3-c.md: after names brief 4, which has no file",
            "kanban/briefs/2-b.md: after cycle: 2 after 3 after 2",
        ])
        self.write("kanban/briefs/3-c.md", brief("[deals/pipeline]", "[1]"))
        self.assertEqual(coverage.lint(self.tmp), [])

    def test_fm_parses_spec_refs_and_after(self):
        fm, _ = _fm.parse("---\nspec_refs: [contacts/call-log, crm-77]\nafter: [3]\n---\nbody\n")
        self.assertEqual(fm["spec_refs"], ["contacts/call-log", "crm-77"]); self.assertEqual(fm["after"], ["3"])
        fm, _ = _fm.parse("---\nspec_refs: []  # none yet\nafter: []\n---\n")
        self.assertEqual((fm["spec_refs"], fm["after"]), ([], []))
        fm, _ = _fm.parse((REPO / "templates/brief.md").read_text())
        self.assertEqual((fm["spec_refs"], fm["after"]), ([], []))


if __name__ == "__main__":
    unittest.main()
