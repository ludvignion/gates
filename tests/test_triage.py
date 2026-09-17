"""scripts/triage.py: the four checks that route a brief `light` or `full` (plan 5 AC-1, AC-2)."""
import contextlib
import io
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
import schemas  # noqa: E402
import triage  # noqa: E402

CHARTER = "# Charter\n\n## 1. Refuse in one line\nApplies to: scripts/*, hooks/*\nPattern: x\n"
INTERFACES = "# Interfaces\n\n## 1. Public schema\nApplies to: docs/schema/*\n"
EMPTY = schemas.Charter()


def brief(body: str, frontmatter: str = "") -> str:
    head = f"---\n{frontmatter}\n---\n\n" if frontmatter else ""
    return head + "# Brief: x\n\n## What I want\n" + body + "\n"


class NamedPathsTest(unittest.TestCase):
    def test_backtick_slash_paths_are_named_bare_filenames_are_not(self):
        text = "Touches `scripts/triage.py` and `render_verdict.py` and `docs/domain-pack/interfaces.md`."
        self.assertEqual(triage.named_paths(text), ("scripts/triage.py", "docs/domain-pack/interfaces.md"))

    def test_dedupes_and_strips_trailing_punctuation(self):
        text = "See `scripts/triage.py`, then `scripts/triage.py`."
        self.assertEqual(triage.named_paths(text), ("scripts/triage.py",))


class RouteTest(unittest.TestCase):
    def test_no_named_path_at_all_routes_full(self):
        t = triage.route(brief("A brief with no path in it."), EMPTY, EMPTY)
        self.assertEqual(t.route, "full")
        self.assertEqual(t.paths, ())

    def test_ordinary_path_no_check_fires_routes_light(self):
        t = triage.route(brief("Edit `src/widgets.py` to add a field."), EMPTY, EMPTY)
        self.assertEqual(t.route, "light")
        self.assertEqual(len(t.checks), 4)
        self.assertTrue(all(not c.fired for c in t.checks))

    def test_charter_glob_reach_routes_full_check_1(self):
        charter = schemas.Charter.parse(CHARTER)
        t = triage.route(brief("Edit `scripts/foo.py`."), charter, EMPTY)
        self.assertEqual(t.route, "full")
        self.assertTrue(t.checks[0].fired)
        self.assertFalse(any(c.fired for c in t.checks[1:]))

    def test_interface_glob_reach_routes_full_check_2(self):
        interfaces = schemas.Charter.parse(INTERFACES)
        t = triage.route(brief("Edit `docs/schema/call.graphql`."), EMPTY, interfaces)
        self.assertEqual(t.route, "full")
        self.assertTrue(t.checks[1].fired)
        self.assertFalse(t.checks[0].fired)

    def test_package_manifest_named_routes_full_check_3(self):
        t = triage.route(brief("Bump a dependency in `package.json`."), EMPTY, EMPTY)
        self.assertEqual(t.route, "full")
        self.assertTrue(t.checks[2].fired)

    def test_url_named_routes_full_check_3(self):
        t = triage.route(brief("Point `src/client.py` at https://api.example.com/v1."), EMPTY, EMPTY)
        self.assertEqual(t.route, "full")
        self.assertTrue(t.checks[2].fired)

    def test_env_var_named_routes_full_check_3(self):
        t = triage.route(brief("Read `src/client.py` and the API_ACCESS_TOKEN env var."), EMPTY, EMPTY)
        self.assertEqual(t.route, "full")
        self.assertTrue(t.checks[2].fired)

    def test_partner_facing_flag_routes_full_check_4(self):
        t = triage.route(brief("Edit `src/widgets.py`.", "partner_facing: true"), EMPTY, EMPTY)
        self.assertEqual(t.route, "full")
        self.assertTrue(t.checks[3].fired)

    def test_partner_facing_false_does_not_fire(self):
        t = triage.route(brief("Edit `src/widgets.py`.", "partner_facing: false"), EMPTY, EMPTY)
        self.assertEqual(t.route, "light")
        self.assertFalse(t.checks[3].fired)

    def test_missing_charter_fires_check_1_instead_of_silently_passing(self):
        t = triage.route(brief("Edit `src/widgets.py`."), schemas.Charter.missing(), EMPTY)
        self.assertEqual(t.route, "full")
        self.assertTrue(t.checks[0].fired)
        self.assertIn("charter.md missing", t.checks[0].detail)

    def test_missing_interfaces_fires_check_2_instead_of_silently_passing(self):
        t = triage.route(brief("Edit `src/widgets.py`."), EMPTY, schemas.Charter.missing())
        self.assertEqual(t.route, "full")
        self.assertTrue(t.checks[1].fired)
        self.assertIn("interfaces.md missing", t.checks[1].detail)

    def test_missing_charter_with_no_named_path_still_routes_full_for_the_no_path_reason(self):
        t = triage.route(brief("Nothing named here."), schemas.Charter.missing(), schemas.Charter.missing())
        self.assertEqual(t.route, "full")
        self.assertFalse(any(c.fired for c in t.checks))


class RenderTest(unittest.TestCase):
    def test_light_prints_the_word_and_all_four_checks(self):
        t = triage.route(brief("Edit `src/widgets.py`."), EMPTY, EMPTY)
        out = t.render()
        lines = out.splitlines()
        self.assertEqual(lines[0], "light")
        for n in "1234":
            self.assertTrue(any(l.startswith(f"{n}.") for l in lines), out)

    def test_full_prints_the_word_and_names_the_fired_check(self):
        charter = schemas.Charter.parse(CHARTER)
        t = triage.route(brief("Edit `scripts/foo.py`."), charter, EMPTY)
        out = t.render()
        lines = out.splitlines()
        self.assertEqual(lines[0], "full")
        self.assertTrue(any(l.startswith("1.") and "yes" in l for l in lines), out)

    def test_no_path_prints_full_and_says_so(self):
        t = triage.route(brief("Nothing named here."), EMPTY, EMPTY)
        out = t.render()
        self.assertEqual(out.splitlines()[0], "full")
        self.assertIn("no named path", out)


class PurityTest(unittest.TestCase):
    """AC-2: two runs over the same inputs agree; no model call, no network call."""

    def test_two_runs_over_the_same_brief_charter_interfaces_agree(self):
        charter = schemas.Charter.parse(CHARTER)
        interfaces = schemas.Charter.parse(INTERFACES)
        text = brief("Edit `scripts/foo.py` and `docs/schema/x.graphql`.")
        first = triage.route(text, charter, interfaces)
        second = triage.route(text, charter, interfaces)
        self.assertEqual(first, second)

    def test_many_brief_shapes_are_deterministic(self):
        charter = schemas.Charter.parse(CHARTER)
        interfaces = schemas.Charter.parse(INTERFACES)
        bodies = [
            "no path here",
            "touches `src/a.py`",
            "touches `scripts/a.py`",
            "touches `docs/schema/a.graphql`",
            "touches `package.json`",
            "mentions https://x.example.com",
            "reads DATABASE_URL",
        ]
        for body in bodies:
            text = brief(body)
            with self.subTest(body=body):
                self.assertEqual(triage.route(text, charter, interfaces), triage.route(text, charter, interfaces))

    def test_makes_no_network_call(self):
        charter = schemas.Charter.parse(CHARTER)
        with mock.patch.object(socket, "socket", side_effect=AssertionError("triage must not touch the network")):
            triage.route(brief("Edit `scripts/foo.py`."), charter, EMPTY)  # raises if it tries

    def test_makes_no_subprocess_call(self):
        charter = schemas.Charter.parse(CHARTER)
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("triage must not spawn a model call")):
            triage.route(brief("Edit `scripts/foo.py`."), charter, EMPTY)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "docs" / "domain-pack").mkdir(parents=True)
        (self.tmp / "docs" / "domain-pack" / "charter.md").write_text(CHARTER)
        (self.tmp / "docs" / "domain-pack" / "interfaces.md").write_text(INTERFACES)
        (self.tmp / "brief.md").write_text(brief("Edit `src/widgets.py`."))

    def test_main_prints_light_and_exits_0(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = triage.main(["triage.py", "brief.md", "--cwd", str(self.tmp)])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().splitlines()[0], "light")

    def test_main_missing_brief_refuses_in_one_line_not_a_traceback(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = triage.main(["triage.py", "does-not-exist.md", "--cwd", str(self.tmp)])
        self.assertEqual(rc, 1)
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1, err.getvalue())
        self.assertIn("does-not-exist.md", lines[0])

    def test_main_missing_domain_pack_routes_full_not_silently_light(self):
        empty_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty_root, ignore_errors=True)
        (empty_root / "brief.md").write_text(brief("Edit `src/widgets.py`."))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = triage.main(["triage.py", "brief.md", "--cwd", str(empty_root)])
        self.assertEqual(rc, 0)
        out_lines = out.getvalue().splitlines()
        self.assertEqual(out_lines[0], "full")
        self.assertIn("charter.md missing", out.getvalue())


if __name__ == "__main__":
    unittest.main()
