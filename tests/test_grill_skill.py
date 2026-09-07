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

    def test_routing_default_is_runner(self):
        routing = schemas.section(SKILL.read_text(encoding="utf-8"), "Routing")
        self.assertIn("backend: runner | workflow | session", routing)
        self.assertIn("session only when the human overrides to it", routing)


if __name__ == "__main__":
    unittest.main()
