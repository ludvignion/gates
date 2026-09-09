"""skills/grill/SKILL.md: the sentences the harness relies on are present, in their section."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import schemas  # noqa: E402

SKILL = REPO / "skills" / "grill" / "SKILL.md"


class GrillSkillTextTest(unittest.TestCase):
    def test_capability_set_closes_only_on_a_full_probe(self):
        evidence = schemas.section(SKILL.read_text(encoding="utf-8"), "Evidence discipline")
        self.assertIn(
            "A `capability` node claiming a provider keeps or supports a set of things is closed only by a "
            "probe that exercises every member named in the ACs.",
            " ".join(evidence.split()),
        )

    def test_rule_7_approves_through_kanban_ops(self):
        rules = schemas.section(SKILL.read_text(encoding="utf-8"), "Rules")
        self.assertIn("kanban_ops.py approve <n>", rules)
        self.assertIn("Never write those fields yourself", rules)

    def test_rule_4_ranks_by_invariant_before_cost(self):
        """G1: the recommendation names the invariant it preserves; cost is second."""
        rules = schemas.section(SKILL.read_text(encoding="utf-8"), "Rules")
        self.assertIn("invariant", rules)
        self.assertIn("cost second", rules)

    def test_human_acs_and_the_approval_remarks(self):
        """E30: the (human) tag; G3: approval-turn remarks land in grill-misses.jsonl before the approve."""
        rules = schemas.section(SKILL.read_text(encoding="utf-8"), "Rules")
        self.assertIn("(human)", rules)
        self.assertIn("- AC-7 (human):", rules)
        self.assertIn("traces/grill-misses.jsonl", rules)
        self.assertIn('"source": "approval"', rules)
        self.assertLess(rules.index('"source": "approval"'), rules.index("kanban_ops.py approve <n>"))

    def test_hand_off_shapes_and_the_next_line(self):
        """G2/J: the hand-off is computed from the stamp; the skill ends with one Next line."""
        body = SKILL.read_text(encoding="utf-8")
        rules = schemas.section(body, "Rules")
        for shape in ("Next: /harness-plugin:runner plan <n>", "Next: /harness-plugin:runner <id>",
                      "Next: build from branch <base> (session backend by override)"):
            self.assertIn(shape, rules, shape)
        nonempty = [l for l in body.splitlines() if l.strip()]
        self.assertTrue(nonempty[-1].startswith("Next:"), nonempty[-1])

    def test_spec_refs_are_evidence_and_acs_name_ids(self):
        """0.8.0: cited units are opened as evidence; every id gets a bracketed AC; unshipped after briefs are refused."""
        body = SKILL.read_text(encoding="utf-8")
        rules = schemas.section(body, "Rules")
        self.assertIn("`docs/spec/<file>#<id>`", rules)
        self.assertIn("before closing any `fact` node", rules)
        self.assertIn("`[contacts/call-log]`", rules)
        self.assertIn("one\n   AC may name several ids", rules)  # 0.8.1 item 1
        self.assertIn("No `docs/spec/index.md`", rules)
        shape = schemas.section(body, "Brief shape")
        self.assertIn("`after` names a brief that is not shipped", shape)
        self.assertIn("`done` or `superseded`", shape)
        self.assertIn("Refuse a brief whose Outcome is a code property", shape)
        self.assertIn("2 rounds", rules)

    def test_routing_default_is_runner(self):
        routing = schemas.section(SKILL.read_text(encoding="utf-8"), "Routing")
        self.assertIn("backend: runner | workflow | session", routing)
        self.assertIn("session only when the human overrides to it", routing)


if __name__ == "__main__":
    unittest.main()
