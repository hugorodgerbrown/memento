"""
The Memento skill (M6, 0013). It is behaviour, measured by evals; these tests
keep it loadable and keep the evals honest.
"""

import json
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skill" / "memento"
CASES = ROOT / "docs" / "evals" / "capture-policy.json"
# The build plan asks for the padel message as the skill's first worked example, and
# "don't log that" is the product's own undo phrase (Principle 9, every receipt).
MAY_QUOTE = {"log-multi", "undo"}


def frontmatter(text: str) -> dict[str, str]:
    block = re.match(r"---\n(.*?)\n---\n", text, re.S)
    return dict(line.split(": ", 1) for line in block.group(1).splitlines()) if block else {}


def squash(text: str) -> str:
    return " ".join(text.lower().split())


class SkillTests(SimpleTestCase):
    def test_skill_is_a_valid_agent_skill(self):
        """Agent Skills format: name matches the folder; description within 1024 characters."""
        meta = frontmatter((SKILL / "SKILL.md").read_text())
        self.assertEqual(meta.get("name"), SKILL.name)
        self.assertTrue(0 < len(meta.get("description", "")) <= 1024)

    def test_skill_can_be_uploaded_to_claude_desktop(self):
        """Claude Desktop's skill upload takes a description of at most 200 characters."""
        meta = frontmatter((SKILL / "SKILL.md").read_text())
        self.assertLessEqual(len(meta["description"]), 200)

    def test_skill_links_only_to_files_that_exist(self):
        for ref in re.findall(r"`(references/[\w.-]+)`", (SKILL / "SKILL.md").read_text()):
            self.assertTrue((SKILL / ref).is_file(), ref)

    def test_skill_does_not_quote_the_eval_cases(self):
        """A skill that contains the test's messages measures memory, not behaviour."""
        text = squash(" ".join(p.read_text() for p in SKILL.rglob("*.md")))
        for case in json.loads(CASES.read_text())["cases"]:
            if case["id"] not in MAY_QUOTE:
                with self.subTest(case=case["id"]):
                    self.assertFalse(squash(case["message"]) in text, "quoted in skill/")
