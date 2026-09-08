"""skills/finding/SKILL.md: one Bash line into traces/harness-findings.md, the human invokes it (item 14)."""
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "finding" / "SKILL.md"


def body_lines(text: str) -> list[str]:
    """Non-empty lines after the frontmatter."""
    parts = text.split("---", 2)
    return [l for l in parts[2].splitlines() if l.strip()]


class FindingSkillTest(unittest.TestCase):
    def test_ten_lines_at_most_and_one_bash_line(self):
        text = SKILL.read_text(encoding="utf-8")
        lines = body_lines(text)
        self.assertLessEqual(len(lines), 10, lines)
        self.assertIn("traces/harness-findings.md", text)
        self.assertIn("plugin.json", text)
        self.assertIn("kanban/.active", text)
        self.assertEqual(len([l for l in lines if "printf" in l]), 1)
        self.assertEqual(lines[-1], "Next: continue.")
        self.assertIn("disable-model-invocation: false", text)
        self.assertIn('argument-hint: "<text>"', text)

    def test_manifest_lists_six_skills(self):
        manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(len(manifest["skills"]), 6, manifest["skills"])
        self.assertIn("./skills/finding", manifest["skills"])
        self.assertIn("Six skills", manifest["description"])
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(manifest["version"], market["metadata"]["version"])
        self.assertEqual(manifest["version"], market["plugins"][0]["version"])
        for entry in manifest["skills"]:
            self.assertTrue((REPO / entry / "SKILL.md").exists(), entry)


if __name__ == "__main__":
    unittest.main()
