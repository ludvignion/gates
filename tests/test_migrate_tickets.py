"""scripts/migrate_tickets.py: every file ticket under kanban/ becomes one issue (plan 4 AC-14 /
ticket 4.6 AC-2), `gh` stubbed by tests/fixtures/fake_gh.py as in test_tickets_sync.py."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
FAKE_GH = REPO / "tests" / "fixtures" / "fake_gh.py"
sys.path.insert(0, str(REPO / "scripts"))
import migrate_tickets  # noqa: E402

TICKET_1 = """---
id: 4.1
parent: 4
status: ready
depends_on: []
writes: [a.py]
---

# 4.1 First slice

## Outcome
Do a thing.

## Acceptance criteria
- AC-1 (behavioral): Given X, When Y, Then Z

## Out of scope
- nothing

## Findings (append-only)

## Log (append-only)
### [grill] 2026-09-14 08:08 — created
"""

TICKET_2 = """---
id: 4.2
parent: 4
status: done
depends_on: [4.1]
writes: [b.py]
---

# 4.2 Second slice

## Outcome
Do another thing.

## Acceptance criteria
- AC-1 (behavioral): Given A, When B, Then C

## Out of scope
- nothing

## Findings (append-only)
- finding: a pre-existing note worth keeping.

## Log (append-only)
### [grill] 2026-09-14 08:08 — created
### [human] 2026-09-14 09:00 — approved by ludvignion
"""

PLAN = """---
brief: 4
status: approved
approved: ludvignion 2026-09-14
---

# 4 Tickets live in GitHub Issues

## Slices
1. `4.1` — first slice.
2. `4.2` — second slice.
"""


class MigrateTicketsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "kanban" / "tickets").mkdir(parents=True)
        (self.tmp / "kanban" / "plans").mkdir(parents=True)
        (self.tmp / "kanban" / "tickets" / "4.1.first-slice.md").write_text(TICKET_1, encoding="utf-8")
        (self.tmp / "kanban" / "tickets" / "4.2.second-slice.md").write_text(TICKET_2, encoding="utf-8")
        (self.tmp / "kanban" / "plans" / "4.plan.md").write_text(PLAN, encoding="utf-8")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_GH} \"$@\"\n")
        gh.chmod(0o755)
        self.gh_log = self.tmp / "gh_calls.log"
        self.gh_data = self.tmp / "gh_data.json"
        self.gh_data.write_text(json.dumps({"issues": []}))
        self.env = mock.patch.dict(
            os.environ,
            {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_LOG": str(self.gh_log), "FAKE_GH_DATA": str(self.gh_data)},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _data(self) -> dict:
        return json.loads(self.gh_data.read_text())

    def test_migrates_in_dependency_order_with_remapped_depends_on(self):
        mapping, changed_plans = migrate_tickets.migrate(self.tmp, "ludvignion/gates")
        self.assertEqual([old for old, _ in mapping], ["4.1", "4.2"])
        data = self._data()
        first, second = data["issues"]
        self.assertEqual(sorted(first["labels"]), ["ready", "ticket"])
        self.assertIn(f"depends_on: [#{first['number']}]", second["body"])
        self.assertIn("## Outcome\nDo another thing.", second["body"])
        self.assertNotIn("status:", second["body"])

    def test_done_ticket_is_created_then_closed_after_its_comments(self):
        migrate_tickets.migrate(self.tmp, "ludvignion/gates")
        data = self._data()
        second = data["issues"][1]
        self.assertEqual(second["state"], "CLOSED")
        self.assertEqual(sorted(second["labels"]), ["done", "ticket"])
        self.assertEqual(len(second["comments"]), 2)
        self.assertIn("created", second["comments"][0]["body"])
        self.assertIn("approved by ludvignion", second["comments"][1]["body"])

    def test_log_entries_become_comments_in_order_reproducing_the_text(self):
        migrate_tickets.migrate(self.tmp, "ludvignion/gates")
        first = self._data()["issues"][0]
        self.assertEqual(first["comments"][0]["body"], "### [grill] 2026-09-14 08:08 — created")

    def test_findings_content_is_kept_in_the_issue_body(self):
        migrate_tickets.migrate(self.tmp, "ludvignion/gates")
        second = self._data()["issues"][1]
        self.assertIn("## Findings (append-only)", second["body"])
        self.assertIn("a pre-existing note worth keeping", second["body"])

    def test_empty_findings_section_is_dropped(self):
        migrate_tickets.migrate(self.tmp, "ludvignion/gates")
        first = self._data()["issues"][0]
        self.assertNotIn("Findings", first["body"])

    def test_plan_slices_cite_the_new_numbers(self):
        mapping, changed_plans = migrate_tickets.migrate(self.tmp, "ludvignion/gates")
        by_old = dict(mapping)
        text = (self.tmp / "kanban" / "plans" / "4.plan.md").read_text(encoding="utf-8")
        self.assertIn(f"`#{by_old['4.1']}` — first slice.", text)
        self.assertIn(f"`#{by_old['4.2']}` — second slice.", text)
        self.assertEqual(changed_plans, [self.tmp / "kanban" / "plans" / "4.plan.md"])

    def test_original_ticket_files_are_removed(self):
        migrate_tickets.migrate(self.tmp, "ludvignion/gates")
        self.assertEqual(list((self.tmp / "kanban" / "tickets").iterdir()), [])

    def test_no_file_tickets_is_a_no_op(self):
        (self.tmp / "kanban" / "tickets" / "4.1.first-slice.md").unlink()
        (self.tmp / "kanban" / "tickets" / "4.2.second-slice.md").unlink()
        mapping, changed_plans = migrate_tickets.migrate(self.tmp, "ludvignion/gates")
        self.assertEqual(mapping, [])
        self.assertEqual(changed_plans, [])
        self.assertEqual(self.gh_log.exists(), False)

    def test_depends_on_cycle_raises_before_any_gh_call(self):
        (self.tmp / "kanban" / "tickets" / "4.1.first-slice.md").write_text(
            TICKET_1.replace("depends_on: []", "depends_on: [4.2]"), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            migrate_tickets.migrate(self.tmp, "ludvignion/gates")
        self.assertIn("cycle", str(ctx.exception))
        self.assertFalse(self.gh_log.exists())


if __name__ == "__main__":
    unittest.main()
