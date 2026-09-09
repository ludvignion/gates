"""skills/decompose/SKILL.md: a thin wrapper over templates/decompose.md that ends on make coverage."""
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "decompose" / "SKILL.md"
TEMPLATE = REPO / "templates" / "decompose.md"


class DecomposeSkillTest(unittest.TestCase):
    def test_thin_wrapper_over_the_template(self):
        text = SKILL.read_text(encoding="utf-8")
        body = text.split("---", 2)[2]
        lines = [l for l in body.splitlines() if l.strip()]
        self.assertLessEqual(len(lines), 20, lines)
        for needle in ("templates/decompose.md", "docs/spec/index.md", "make coverage", "0 violation(s)",
                       "`go`", "go, order:", "keep their numbers", "never edited", "make intake F=docs/spec/<file>"):
            self.assertIn(needle, body, needle)
        self.assertEqual(lines[-1], "Next: /harness-plugin:grill <first brief number>")
        self.assertIn("disable-model-invocation: true", text)

    def test_template_serves_session_and_chat(self):
        text = " ".join(TEMPLATE.read_text(encoding="utf-8").split())
        for needle in ("/harness-plugin:decompose", "make coverage", "exactly once", "Refuse a brief whose Outcome is a code property",
                       "letter the rows", "never renumbered", "- <id> — <reason>"):
            self.assertIn(needle, text, needle)

    def test_manifest_lists_eight_skills(self):
        manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        self.assertIn("./skills/decompose", manifest["skills"])
        self.assertEqual(len(manifest["skills"]), 8)
        self.assertIn("Eight skills", manifest["description"])
