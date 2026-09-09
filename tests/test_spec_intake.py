"""scripts/spec_intake.py: markdown headings or a CSV id column → docs/spec/index.md, and nothing else."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import schemas  # noqa: E402
import spec_intake  # noqa: E402

SCRIPT = REPO / "scripts" / "spec_intake.py"
SPEC = ("# CRM\n\nPreamble, not a unit.\n\n## Contacts\n\n### Call log\nLog every call with its duration.\n\n"
        "```\n# a comment in a fence, not a heading\n```\n\n### Notes\nFree text per contact.\n\n"
        "## Deals\n\n### Notes\nFree text per deal.\n")


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=root, capture_output=True, text=True)


class SpecIntakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "docs/spec").mkdir(parents=True)

    def write(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        p = self.tmp / "docs/spec" / name
        p.write_bytes(text.encode(encoding))
        return p

    def test_markdown_units_ids_and_fence(self):
        """Heading path ids; a fenced # is text; the same title under two parents differs; the lone # is the title."""
        self.write("crm.md", SPEC)
        proc = run(self.tmp, "docs/spec/crm.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "docs/spec/index.md: 3 units from docs/spec/crm.md")
        index = schemas.SpecIndex.load(self.tmp / "docs/spec/index.md")
        self.assertEqual(index.spec, "docs/spec/crm.md")
        self.assertEqual(index.ids, ("contacts/call-log", "contacts/notes", "deals/notes"))
        self.assertEqual([u.title for u in index.units], ["Call log", "Notes", "Notes"])
        self.assertEqual(index.units[0].words, 17)  # the fence and its # line count as text, never as a heading
        self.assertNotEqual(index.sha_of("contacts/notes"), index.sha_of("deals/notes"))
        text = (self.tmp / "docs/spec/index.md").read_text()
        self.assertTrue(text.startswith("---\nspec: docs/spec/crm.md\nunits: 3\n---\n"), text[:60])
        self.assertEqual((self.tmp / "docs/spec/crm.md").read_text(), SPEC)  # the spec is never modified

    def test_crlf_gives_the_same_shas_and_rerun_rewrites(self):
        self.write("crm.md", SPEC)
        run(self.tmp, "docs/spec/crm.md")
        lf = schemas.SpecIndex.load(self.tmp / "docs/spec/index.md")
        self.write("crm.md", SPEC.replace("\n", "\r\n").replace("duration.", "duration.   "))
        (self.tmp / "docs/spec/index.md").write_text("stale\n")
        self.assertEqual(run(self.tmp, "docs/spec/crm.md").returncode, 0)
        crlf = schemas.SpecIndex.load(self.tmp / "docs/spec/index.md")
        self.assertEqual([u.sha for u in lf.units], [u.sha for u in crlf.units])
        self.assertNotIn("stale", (self.tmp / "docs/spec/index.md").read_text())

    def test_same_path_twice_is_refused(self):
        self.write("crm.md", "## A\n### B\nx\n## A\n### B\ny\n")
        proc = run(self.tmp, "docs/spec/crm.md")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stderr.strip(), "refused: duplicate id a/b: the same heading path twice")
        self.assertFalse((self.tmp / "docs/spec/index.md").exists())

    def test_refusals(self):
        for name, text, needle in (
            ("s.md", "just prose\n", "no headings and no id column"),
            ("s.md", "## 联系人\ntext\n", "slugs to nothing: add an id column"),
            ("s.csv", "name,text\na,b\n", "no id column"),
            ("s.csv", "id,text\ncrm-1,a\n,b\n", "row 3 has an empty id cell"),
            ("s.csv", "id,text\ncrm-1,a\ncrm-1,b\n", "duplicate id crm-1"),
        ):
            for old in (self.tmp / "docs/spec").iterdir():
                old.unlink()
            self.write(name, text)
            proc = run(self.tmp, f"docs/spec/{name}")
            self.assertEqual(proc.returncode, 1, (text, proc.stdout))
            self.assertEqual(len(proc.stderr.strip().splitlines()), 1, proc.stderr)
            self.assertIn(needle, proc.stderr, text)
        (self.tmp / "other.md").write_text("## A\nx\n")
        proc = run(self.tmp, "other.md")
        self.assertEqual(proc.returncode, 1); self.assertIn("outside docs/spec/", proc.stderr)
        self.write("a.md", "## A\nx\n"); self.write("b.md", "## B\ny\n")
        proc = run(self.tmp, "docs/spec/a.md")
        self.assertEqual(proc.returncode, 1); self.assertIn("more than one spec file", proc.stderr); self.assertIn("b.md", proc.stderr)

    def test_csv_with_bom_keeps_ids_verbatim(self):
        self.write("crm.csv", "id,title,requirement\nCRM-77,Call log,Log every call\nCRM-78,Notes,Free text\n", encoding="utf-8-sig")
        proc = run(self.tmp, "docs/spec/crm.csv")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        index = schemas.SpecIndex.load(self.tmp / "docs/spec/index.md")
        self.assertEqual(index.ids, ("CRM-77", "CRM-78"))
        self.assertEqual(index.units[0].title, "Call log")
        self.assertEqual(spec_intake.unit_texts(self.tmp, index)["CRM-77"], ("Call log", "title: Call log\nrequirement: Log every call"))

    def test_long_unit_warns_but_indexes(self):
        self.write("crm.md", "## Big\n" + "word " * 401 + "\n")
        proc = run(self.tmp, "docs/spec/crm.md")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr.strip(), "warn: big has 401 words (over 400)")

    def test_container_headings_stay_in_the_path(self):
        units = spec_intake.markdown_units("## Outer\n\n### Inner\ntext\n\n## Other\nbody\n### Deep\n")
        self.assertEqual([u.id for u in units], ["outer/inner", "other"])


if __name__ == "__main__":
    unittest.main()
