"""Every skill ends with exactly one "Next: <command>" line and no menu (G4, contract J)."""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = sorted((REPO / "skills").glob("*/SKILL.md"))


class NextLineTest(unittest.TestCase):
    def test_every_skill_ends_with_one_next_line(self):
        self.assertGreaterEqual(len(SKILLS), 6, SKILLS)
        for skill in SKILLS:
            lines = [l for l in skill.read_text(encoding="utf-8").splitlines() if l.strip()]
            nexts = [l for l in lines if l.startswith("Next:")]
            self.assertEqual(len(nexts), 1, (skill, nexts))
            self.assertIs(lines[-1], nexts[0], (skill, lines[-1]))
            self.assertGreater(len(nexts[0]), len("Next: "), skill)


if __name__ == "__main__":
    unittest.main()
