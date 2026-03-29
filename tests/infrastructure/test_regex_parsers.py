"""Tests for regex-based import parsers across all languages."""

import unittest
from src.infrastructure.parsers.regex_parsers import (
    parse_cpp, parse_java, parse_kotlin, parse_go,
    parse_rust, parse_js_ts, parse_cmake, parse_buck2,
    parse_starlark, parse_bxl, parse_gradle_groovy,
    parse_gradle_kts, parse_groovy,
)


class TestCppParser(unittest.TestCase):
    def test_angle_brackets(self):
        r = parse_cpp('#include <iostream>\n#include <vector>')
        self.assertEqual(len(r), 2)

    def test_quotes(self):
        r = parse_cpp('#include "mylib/utils.h"')
        self.assertEqual(r[0].module, "mylib.utils")

    def test_spacing(self):
        r = parse_cpp('#  include <stdio.h>')
        self.assertEqual(len(r), 1)


class TestJavaParser(unittest.TestCase):
    def test_import(self):
        r = parse_java("import com.example.MyClass;")
        self.assertEqual(r[0].module, "com.example.MyClass")

    def test_static_import(self):
        r = parse_java("import static org.junit.Assert.assertEquals;")
        self.assertEqual(r[0].module, "org.junit.Assert.assertEquals")


class TestKotlinParser(unittest.TestCase):
    def test_import(self):
        r = parse_kotlin("import com.example.data.Repository")
        self.assertEqual(r[0].module, "com.example.data.Repository")


class TestGoParser(unittest.TestCase):
    def test_single(self):
        r = parse_go('import "fmt"')
        self.assertEqual(r[0].module, "fmt")

    def test_block(self):
        r = parse_go('import (\n  "fmt"\n  "os"\n  "strings"\n)')
        self.assertEqual({x.module for x in r}, {"fmt", "os", "strings"})


class TestRustParser(unittest.TestCase):
    def test_use(self):
        r = parse_rust("use std::collections::HashMap;")
        self.assertEqual(r[0].module, "std.collections.HashMap")

    def test_mod(self):
        r = parse_rust("mod utils;")
        self.assertEqual(r[0].module, "utils")

    def test_extern_crate(self):
        r = parse_rust("extern crate serde;")
        self.assertEqual(r[0].kind, "crate")


class TestJsTsParser(unittest.TestCase):
    def test_import_from(self):
        r = parse_js_ts("import { foo } from './utils'")
        self.assertEqual(r[0].module, "./utils")

    def test_require(self):
        r = parse_js_ts("const x = require('./config')")
        modules = {x.module for x in r}
        self.assertIn("./config", modules)


class TestCMakeParser(unittest.TestCase):
    def test_find_package(self):
        r = parse_cmake("find_package(Boost REQUIRED)")
        self.assertEqual(r[0].module, "Boost")

    def test_add_subdirectory(self):
        r = parse_cmake("add_subdirectory(src/core)")
        self.assertEqual(r[0].module, "src/core")


class TestBuck2Parser(unittest.TestCase):
    def test_load(self):
        r = parse_buck2('load("//tools:defs.bzl", "my_rule")')
        self.assertIn("//tools:defs.bzl", {x.module for x in r})

    def test_deps(self):
        r = parse_buck2('deps = [\n  "//lib:core",\n  "//lib:utils",\n]')
        modules = {x.module for x in r}
        self.assertIn("//lib:core", modules)
        self.assertIn("//lib:utils", modules)


class TestStarlarkParser(unittest.TestCase):
    def test_load(self):
        r = parse_starlark('load("@rules_cc//cc:defs.bzl", "cc_library")')
        self.assertEqual(r[0].module, "@rules_cc//cc:defs.bzl")


class TestBxlParser(unittest.TestCase):
    def test_load(self):
        r = parse_bxl('load("//bxl:rules.bzl", "my_check")')
        self.assertIn("//bxl:rules.bzl", {x.module for x in r})

    def test_target(self):
        r = parse_bxl('targets = ["//src/lib:mylib"]')
        self.assertIn("//src/lib:mylib", {x.module for x in r})


class TestGradleGroovyParser(unittest.TestCase):
    def test_implementation(self):
        r = parse_gradle_groovy("implementation 'com.google.guava:guava:31.1-jre'")
        modules = {x.module for x in r}
        self.assertTrue(any("guava" in m for m in modules))

    def test_plugin(self):
        r = parse_gradle_groovy("apply plugin: 'java-library'")
        modules = {x.module for x in r}
        self.assertIn("java-library", modules)


class TestGradleKtsParser(unittest.TestCase):
    def test_plugin_id(self):
        r = parse_gradle_kts('id("org.jetbrains.kotlin.jvm")')
        modules = {x.module for x in r}
        self.assertIn("org.jetbrains.kotlin.jvm", modules)

    def test_project_dep(self):
        r = parse_gradle_kts('api(project(":core"))')
        modules = {x.module for x in r}
        self.assertIn(":core", modules)

    def test_include(self):
        r = parse_gradle_kts('include(":app", ":core")')
        modules = {x.module for x in r}
        self.assertIn(":app", modules)
        self.assertIn(":core", modules)


class TestGroovyParser(unittest.TestCase):
    def test_import(self):
        r = parse_groovy("import groovy.json.JsonSlurper")
        self.assertEqual(r[0].module, "groovy.json.JsonSlurper")
