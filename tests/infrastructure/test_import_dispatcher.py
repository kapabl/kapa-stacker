"""Tests for the import dispatch layer."""

import unittest
from src.infrastructure.parsers.import_dispatcher import dispatch_parse_imports


class TestImportDispatcher(unittest.TestCase):
    def test_python(self):
        r = dispatch_parse_imports("main.py", "import os")
        self.assertEqual(r[0].module, "os")

    def test_go(self):
        r = dispatch_parse_imports("main.go", 'import "fmt"')
        self.assertEqual(r[0].module, "fmt")

    def test_java(self):
        r = dispatch_parse_imports("Main.java", "import com.example.Foo;")
        self.assertEqual(r[0].module, "com.example.Foo")

    def test_rust(self):
        r = dispatch_parse_imports("lib.rs", "use std::io;")
        self.assertTrue(any("std" in x.module for x in r))

    def test_cmake(self):
        r = dispatch_parse_imports("CMakeLists.txt", "find_package(Boost)")
        self.assertEqual(r[0].module, "Boost")

    def test_unknown_language(self):
        r = dispatch_parse_imports("data.csv", "nothing here")
        self.assertEqual(r, [])

    def test_starlark(self):
        r = dispatch_parse_imports("BUILD", 'load(":defs.bzl", "rule")')
        self.assertEqual(r[0].module, ":defs.bzl")
