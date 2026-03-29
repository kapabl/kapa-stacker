"""Tests for Python AST import parser."""

import unittest
from src.infrastructure.parsers.python_ast_parser import parse_python_imports


class TestPythonAstParser(unittest.TestCase):
    def test_import(self):
        r = parse_python_imports("import os\nimport sys")
        modules = {x.module for x in r}
        self.assertIn("os", modules)
        self.assertIn("sys", modules)

    def test_from_import(self):
        r = parse_python_imports("from pathlib import Path\nfrom os.path import join")
        modules = {x.module for x in r}
        self.assertIn("pathlib", modules)
        self.assertIn("os.path", modules)

    def test_relative_import(self):
        r = parse_python_imports("from .models import User")
        self.assertEqual(r[0].module, "models")

    def test_syntax_error_falls_back(self):
        r = parse_python_imports("import os\nthis is not valid python {{{{")
        # Should fall back to regex and still find "import os"
        modules = {x.module for x in r}
        self.assertIn("os", modules)
