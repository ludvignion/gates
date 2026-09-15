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
        for shape in ("Next: /gates:runner plan <n>", "Next: /gates:runner <id>",
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

    def test_decisions_carry_kind_and_only_functional_is_asked(self):
        """0.11.0: the reader test decides `kind`; technical is closed by evidence and invariants, never asked;
        the trace record carries kind; a technical node resolved by a human is a grill miss."""
        body = SKILL.read_text(encoding="utf-8")
        rules = schemas.section(body, "Rules")
        flat = " ".join(rules.split())
        self.assertIn("`kind: functional|technical`", rules)
        self.assertIn("would the reader notice the difference in behaviour?", flat)
        self.assertIn("Ask the human only functional decisions", rules)
        self.assertIn("A question exists only to close a `functional` decision node.", rules)
        self.assertIn("technical decisions table", flat)
        self.assertIn('"kind": "functional|technical|null"', rules)
        self.assertIn('`"kind": "technical"` and `"resolved_by": "human"` is a grill miss', flat)
        self.assertIn("Ask a technical question.", schemas.section(body, "Do not"))

    def test_gate_1_notes_are_informational(self):
        """Dependency, licence, paid service, data leaving the repo: shown at Gate 1, never asked."""
        rules = " ".join(schemas.section(SKILL.read_text(encoding="utf-8"), "Rules").split())
        self.assertIn("## Gate 1 notes", rules)
        for word in ("runtime dependency", "a licence", "a paid service", "data leaving the repo"):
            self.assertIn(word, rules, word)
        self.assertIn("informational only, never a question", rules)
        self.assertIn("the Outcome line, the human ACs, the Gate 1 notes, the Options entries to pick", rules)
        self.assertIn("`Next: approve plan <n>, or say what to change`", rules)
        self.assertIn("do not print them", rules)
        self.assertIn("| Capability | Provided by | Provider evidence | Access evidence |", (REPO / "templates" / "plan.md").read_text(encoding="utf-8"))
        template = (REPO / "templates" / "plan.md").read_text(encoding="utf-8")
        self.assertIn("## Technical decisions I made", template)
        self.assertIn("## Gate 1 notes", template)
        build = schemas.section((REPO / "skills" / "build" / "SKILL.md").read_text(encoding="utf-8"), "Rules") or (REPO / "skills" / "build" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("A technical ambiguity (how to build it)", " ".join(build.split()))
        self.assertIn("is never NEEDS_CONTEXT", " ".join(build.split()))

    def test_rule_9_names_kanban_ops_new_for_opted_in_projects(self):
        """AC-4 (plan 4 AC-0): opted-in projects create slices through kanban_ops.py new; a
        project without the marker still writes the ticket file directly."""
        rules = schemas.section(SKILL.read_text(encoding="utf-8"), "Rules")
        self.assertIn("kanban_ops.py new <n> <slug> --body-file <path>", rules)
        self.assertIn("kanban/.issues", rules)
        self.assertIn("kanban/tickets/<n>.<m>.<slug>.md", rules)

    def test_routing_default_is_runner(self):
        routing = schemas.section(SKILL.read_text(encoding="utf-8"), "Routing")
        self.assertIn("backend: runner | workflow | session", routing)
        self.assertIn("session only when the human overrides to it", routing)


if __name__ == "__main__":
    unittest.main()
