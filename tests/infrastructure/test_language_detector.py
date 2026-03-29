"""Tests for language detection from file paths."""

import unittest
from src.infrastructure.parsers.language_detector import detect_language


class TestLanguageDetector(unittest.TestCase):
    def test_python(self):
        self.assertEqual(detect_language("src/main.py"), "python")

    def test_cpp(self):
        self.assertEqual(detect_language("src/main.cpp"), "cpp")

    def test_c_header(self):
        self.assertEqual(detect_language("include/header.h"), "c")

    def test_java(self):
        self.assertEqual(detect_language("src/Main.java"), "java")

    def test_kotlin(self):
        self.assertEqual(detect_language("src/App.kt"), "kotlin")

    def test_go(self):
        self.assertEqual(detect_language("cmd/server.go"), "go")

    def test_rust(self):
        self.assertEqual(detect_language("src/lib.rs"), "rust")

    def test_typescript(self):
        self.assertEqual(detect_language("src/app.ts"), "typescript")

    def test_tsx(self):
        self.assertEqual(detect_language("src/Button.tsx"), "typescript")

    def test_gradle_groovy(self):
        self.assertEqual(detect_language("build.gradle"), "gradle_groovy")

    def test_gradle_kts(self):
        self.assertEqual(detect_language("build.gradle.kts"), "gradle_kts")

    def test_settings_gradle_kts(self):
        self.assertEqual(detect_language("settings.gradle.kts"), "gradle_kts")

    def test_buck2(self):
        self.assertEqual(detect_language("BUCK"), "buck2")

    def test_bxl(self):
        self.assertEqual(detect_language("checks/lint.bxl"), "bxl")

    def test_starlark(self):
        self.assertEqual(detect_language("BUILD"), "starlark")

    def test_bzl(self):
        self.assertEqual(detect_language("defs.bzl"), "starlark")

    def test_cmake(self):
        self.assertEqual(detect_language("CMakeLists.txt"), "cmake")

    def test_cmake_module(self):
        self.assertEqual(detect_language("cmake/FindFoo.cmake"), "cmake")

    def test_unknown(self):
        self.assertIsNone(detect_language("README.md"))

    def test_groovy(self):
        self.assertEqual(detect_language("script.groovy"), "groovy")
