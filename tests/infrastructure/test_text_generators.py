"""Tests for text generators."""

import unittest

from src.infrastructure.llm.rule_based_generator import RuleBasedGenerator


class TestRuleBasedGenerator(unittest.TestCase):

    def test_generate_title_from_paths(self):
        generator = RuleBasedGenerator()
        title = generator.generate_title(
            diff_text="+class AuthMiddleware:\n+    pass",
            file_paths=["src/auth/middleware.py"],
            symbols=["AuthMiddleware"],
        )
        self.assertIsInstance(title, str)
        self.assertTrue(len(title) > 0)

    def test_generate_summary(self):
        generator = RuleBasedGenerator()
        summary = generator.generate_summary(
            diff_text="+def validate():\n+    pass",
            file_paths=["src/auth.py", "tests/test_auth.py"],
            depends_on=[1],
        )
        self.assertIn("-", summary)
        self.assertIn("2 file(s)", summary)

    def test_generate_commit_message(self):
        generator = RuleBasedGenerator()
        message = generator.generate_commit_message(
            diff_text="+new code",
            title="Add auth middleware",
        )
        self.assertEqual(message, "Add auth middleware")

    def test_empty_diff(self):
        generator = RuleBasedGenerator()
        title = generator.generate_title("", ["src/foo.py"], [])
        self.assertIsInstance(title, str)

    def test_summary_no_deps(self):
        generator = RuleBasedGenerator()
        summary = generator.generate_summary("", ["src/foo.py"], [])
        self.assertNotIn("Depends on", summary)


if __name__ == "__main__":
    unittest.main()
