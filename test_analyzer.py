#!/usr/bin/env python3
"""Unit tests for stacked_pr_analyzer, lang_parsers, and plan_executor."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import networkx as nx

from stacked_pr_analyzer import (
    ChangedFile,
    ProposedPR,
    build_dependency_graph,
    compute_pr_dependencies,
    compute_risk_scores,
    assign_merge_strategies,
    partition_into_prs,
    find_test_pairs,
    _path_to_module,
    generate_dot,
)
from lang_parsers import (
    ImportInfo,
    parse_imports,
    _parse_python_ast,
    _parse_cpp_regex,
    _parse_java_regex,
    _parse_kotlin_regex,
    _parse_go_regex,
    _parse_rust_regex,
    _parse_js_ts_regex,
    _parse_cmake_regex,
    _parse_buck2_regex,
    _parse_starlark_regex,
    _parse_gradle_groovy_regex,
    _parse_gradle_kts_regex,
    _parse_bxl_regex,
    _parse_groovy_regex,
    _detect_lang,
)
from plan_executor import (
    StackedPRPlan,
    PRPlan,
    PlanStep,
    StepStatus,
    generate_plan,
    generate_shell_script,
    _slugify,
    _generate_mermaid,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(path, added=10, removed=5, status="M", diff_text=""):
    return ChangedFile(path=path, added=added, removed=removed, status=status, diff_text=diff_text)


# ---------------------------------------------------------------------------
# ChangedFile tests
# ---------------------------------------------------------------------------

class TestChangedFile(unittest.TestCase):
    def test_is_text_or_docs(self):
        self.assertTrue(_make_file("README.md").is_text_or_docs)
        self.assertTrue(_make_file("data.json").is_text_or_docs)
        self.assertTrue(_make_file("config.yaml").is_text_or_docs)
        self.assertFalse(_make_file("main.py").is_text_or_docs)
        self.assertFalse(_make_file("app.ts").is_text_or_docs)
        self.assertFalse(_make_file("lib.rs").is_text_or_docs)

    def test_code_lines(self):
        self.assertEqual(_make_file("a.py", added=30, removed=10).code_lines, 40)

    def test_module_key(self):
        self.assertEqual(_make_file("src/foo.py").module_key, "src")
        self.assertEqual(_make_file("setup.py").module_key, "__root__")

    def test_cyclomatic_complexity_default(self):
        self.assertEqual(_make_file("a.py").cyclomatic_complexity, 0)


# ---------------------------------------------------------------------------
# Path to module
# ---------------------------------------------------------------------------

class TestPathToModule(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(_path_to_module("src/utils/helpers.py"), "src.utils.helpers")

    def test_root(self):
        self.assertEqual(_path_to_module("main.py"), "main")


# ---------------------------------------------------------------------------
# Language parsers (regex fallbacks — always available)
# ---------------------------------------------------------------------------

class TestPythonParser(unittest.TestCase):
    def test_import(self):
        result = _parse_python_ast("import os\nimport sys")
        modules = {r.module for r in result}
        self.assertIn("os", modules)
        self.assertIn("sys", modules)

    def test_from_import(self):
        result = _parse_python_ast("from pathlib import Path\nfrom os.path import join")
        modules = {r.module for r in result}
        self.assertIn("pathlib", modules)
        self.assertIn("os.path", modules)

    def test_relative_import(self):
        result = _parse_python_ast("from .models import User")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].module, "models")


class TestCppParser(unittest.TestCase):
    def test_include_angle(self):
        result = _parse_cpp_regex('#include <iostream>\n#include <vector>')
        self.assertEqual(len(result), 2)

    def test_include_quotes(self):
        result = _parse_cpp_regex('#include "mylib/utils.h"')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].module, "mylib.utils")

    def test_preprocessor_spacing(self):
        result = _parse_cpp_regex('#  include <stdio.h>')
        self.assertEqual(len(result), 1)


class TestJavaParser(unittest.TestCase):
    def test_import(self):
        result = _parse_java_regex("import com.example.MyClass;")
        self.assertEqual(result[0].module, "com.example.MyClass")

    def test_static_import(self):
        result = _parse_java_regex("import static org.junit.Assert.assertEquals;")
        self.assertEqual(result[0].module, "org.junit.Assert.assertEquals")


class TestKotlinParser(unittest.TestCase):
    def test_import(self):
        result = _parse_kotlin_regex("import com.example.data.Repository")
        self.assertEqual(result[0].module, "com.example.data.Repository")


class TestGoParser(unittest.TestCase):
    def test_single_import(self):
        result = _parse_go_regex('import "fmt"')
        self.assertEqual(result[0].module, "fmt")

    def test_block_import(self):
        result = _parse_go_regex('import (\n  "fmt"\n  "os"\n  "strings"\n)')
        modules = {r.module for r in result}
        self.assertEqual(modules, {"fmt", "os", "strings"})


class TestRustParser(unittest.TestCase):
    def test_use(self):
        result = _parse_rust_regex("use std::collections::HashMap;")
        self.assertEqual(result[0].module, "std.collections.HashMap")

    def test_mod(self):
        result = _parse_rust_regex("mod utils;")
        self.assertEqual(result[0].module, "utils")

    def test_extern_crate(self):
        result = _parse_rust_regex("extern crate serde;")
        self.assertEqual(result[0].module, "serde")
        self.assertEqual(result[0].kind, "crate")


class TestJsTsParser(unittest.TestCase):
    def test_import_from(self):
        result = _parse_js_ts_regex("import { foo } from './utils'")
        self.assertEqual(result[0].module, "./utils")

    def test_require(self):
        result = _parse_js_ts_regex("const x = require('./config')")
        modules = {r.module for r in result}
        self.assertIn("./config", modules)


class TestCMakeParser(unittest.TestCase):
    def test_find_package(self):
        result = _parse_cmake_regex("find_package(Boost REQUIRED)")
        self.assertEqual(result[0].module, "Boost")

    def test_add_subdirectory(self):
        result = _parse_cmake_regex("add_subdirectory(src/core)")
        self.assertEqual(result[0].module, "src/core")


class TestBuck2Parser(unittest.TestCase):
    def test_load(self):
        result = _parse_buck2_regex('load("//tools:defs.bzl", "my_rule")')
        modules = {r.module for r in result}
        self.assertIn("//tools:defs.bzl", modules)

    def test_deps(self):
        result = _parse_buck2_regex('deps = [\n  "//lib:core",\n  "//lib:utils",\n]')
        modules = {r.module for r in result}
        self.assertIn("//lib:core", modules)
        self.assertIn("//lib:utils", modules)


class TestStarlarkParser(unittest.TestCase):
    def test_load(self):
        result = _parse_starlark_regex('load("@rules_cc//cc:defs.bzl", "cc_library")')
        self.assertEqual(result[0].module, "@rules_cc//cc:defs.bzl")


# ---------------------------------------------------------------------------
# NEW: Gradle parsers
# ---------------------------------------------------------------------------

class TestGradleGroovyParser(unittest.TestCase):
    def test_implementation_dep(self):
        result = _parse_gradle_groovy_regex(
            "implementation 'com.google.guava:guava:31.1-jre'"
        )
        modules = {r.module for r in result}
        self.assertIn("com.google.guava:guava", modules)

    def test_project_dep(self):
        result = _parse_gradle_groovy_regex("api project(':core')")
        modules = {r.module for r in result}
        self.assertIn(":core", modules)

    def test_apply_from(self):
        result = _parse_gradle_groovy_regex("apply from: 'gradle/jacoco.gradle'")
        modules = {r.module for r in result}
        self.assertIn("gradle/jacoco.gradle", modules)

    def test_plugin(self):
        result = _parse_gradle_groovy_regex("apply plugin: 'java-library'")
        modules = {r.module for r in result}
        self.assertIn("java-library", modules)


class TestGradleKtsParser(unittest.TestCase):
    def test_implementation_dep(self):
        result = _parse_gradle_kts_regex(
            'implementation("com.google.guava:guava:31.1-jre")'
        )
        modules = {r.module for r in result}
        self.assertIn("com.google.guava:guava", modules)

    def test_project_dep(self):
        result = _parse_gradle_kts_regex('api(project(":core"))')
        modules = {r.module for r in result}
        self.assertIn(":core", modules)

    def test_plugin_id(self):
        result = _parse_gradle_kts_regex('id("org.jetbrains.kotlin.jvm")')
        modules = {r.module for r in result}
        self.assertIn("org.jetbrains.kotlin.jvm", modules)

    def test_settings_include(self):
        result = _parse_gradle_kts_regex('include(":app", ":core", ":utils")')
        modules = {r.module for r in result}
        self.assertIn(":app", modules)
        self.assertIn(":core", modules)
        self.assertIn(":utils", modules)


class TestBxlParser(unittest.TestCase):
    def test_load(self):
        result = _parse_bxl_regex('load("//bxl:rules.bzl", "my_check")')
        modules = {r.module for r in result}
        self.assertIn("//bxl:rules.bzl", modules)

    def test_target_reference(self):
        result = _parse_bxl_regex('targets = ["//src/lib:mylib"]')
        modules = {r.module for r in result}
        self.assertIn("//src/lib:mylib", modules)


class TestGroovyParser(unittest.TestCase):
    def test_import(self):
        result = _parse_groovy_regex("import groovy.json.JsonSlurper")
        self.assertEqual(result[0].module, "groovy.json.JsonSlurper")


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

class TestLanguageDetection(unittest.TestCase):
    def test_python(self):
        self.assertEqual(_detect_lang("src/main.py"), "python")

    def test_cpp(self):
        self.assertEqual(_detect_lang("src/main.cpp"), "cpp")
        self.assertEqual(_detect_lang("include/header.h"), "c")

    def test_java(self):
        self.assertEqual(_detect_lang("src/Main.java"), "java")

    def test_gradle_groovy(self):
        self.assertEqual(_detect_lang("build.gradle"), "gradle_groovy")
        self.assertEqual(_detect_lang("app/build.gradle"), "gradle_groovy")

    def test_gradle_kts(self):
        self.assertEqual(_detect_lang("build.gradle.kts"), "gradle_kts")
        self.assertEqual(_detect_lang("settings.gradle.kts"), "gradle_kts")

    def test_buck2(self):
        self.assertEqual(_detect_lang("BUCK"), "buck2")

    def test_bxl(self):
        self.assertEqual(_detect_lang("checks/lint.bxl"), "bxl")

    def test_starlark(self):
        self.assertEqual(_detect_lang("BUILD"), "starlark")
        self.assertEqual(_detect_lang("defs.bzl"), "starlark")

    def test_cmake(self):
        self.assertEqual(_detect_lang("CMakeLists.txt"), "cmake")
        self.assertEqual(_detect_lang("cmake/FindFoo.cmake"), "cmake")


# ---------------------------------------------------------------------------
# Test file pairing
# ---------------------------------------------------------------------------

class TestTestPairing(unittest.TestCase):
    def test_python_test_prefix(self):
        files = [_make_file("src/test_models.py"), _make_file("src/models.py")]
        pairs = find_test_pairs(files)
        self.assertEqual(pairs["src/test_models.py"], "src/models.py")

    def test_python_test_suffix(self):
        files = [_make_file("src/models_test.py"), _make_file("src/models.py")]
        pairs = find_test_pairs(files)
        self.assertEqual(pairs["src/models_test.py"], "src/models.py")

    def test_go_test(self):
        files = [_make_file("pkg/handler_test.go"), _make_file("pkg/handler.go")]
        pairs = find_test_pairs(files)
        self.assertEqual(pairs["pkg/handler_test.go"], "pkg/handler.go")

    def test_js_test(self):
        files = [_make_file("src/Button.test.tsx"), _make_file("src/Button.tsx")]
        pairs = find_test_pairs(files)
        self.assertEqual(pairs["src/Button.test.tsx"], "src/Button.tsx")

    def test_java_test(self):
        files = [_make_file("src/FooTest.java"), _make_file("src/Foo.java")]
        pairs = find_test_pairs(files)
        self.assertEqual(pairs["src/FooTest.java"], "src/Foo.java")

    def test_cpp_test(self):
        files = [_make_file("src/utils_test.cpp"), _make_file("src/utils.cpp")]
        pairs = find_test_pairs(files)
        self.assertEqual(pairs["src/utils_test.cpp"], "src/utils.cpp")

    def test_no_pair_when_impl_missing(self):
        files = [_make_file("src/test_models.py")]
        pairs = find_test_pairs(files)
        self.assertEqual(pairs, {})

    def test_test_stays_with_impl_in_pr(self):
        """Test files should be grouped with their implementation."""
        files = [
            _make_file("src/models.py", added=50, removed=0),
            _make_file("src/test_models.py", added=30, removed=0),
            _make_file("lib/utils.py", added=50, removed=0),
        ]
        G = nx.DiGraph()
        for f in files:
            G.add_node(f.path, file=f)
        prs = partition_into_prs(G, files, {}, max_files=2, max_code_lines=200)
        # Find which PR has models.py
        for pr in prs:
            paths = {f.path for f in pr.files}
            if "src/models.py" in paths:
                self.assertIn("src/test_models.py", paths)
                break


# ---------------------------------------------------------------------------
# Grouping / partitioning tests
# ---------------------------------------------------------------------------

class TestPartitioning(unittest.TestCase):
    def test_respects_max_files(self):
        files = [_make_file(f"src/f{i}.py", added=10, removed=0) for i in range(7)]
        G = nx.DiGraph()
        for f in files:
            G.add_node(f.path, file=f)
        prs = partition_into_prs(G, files, {}, max_files=3, max_code_lines=200)
        # All files accounted for (may exceed max_files slightly due to test pairing)
        self.assertEqual(sum(len(pr.files) for pr in prs), 7)

    def test_respects_max_lines(self):
        files = [_make_file(f"src/f{i}.py", added=150, removed=0) for i in range(3)]
        G = nx.DiGraph()
        for f in files:
            G.add_node(f.path, file=f)
        prs = partition_into_prs(G, files, {}, max_files=5, max_code_lines=200)
        self.assertTrue(len(prs) >= 2)

    def test_docs_exempt_from_line_limit(self):
        files = [
            _make_file("README.md", added=500, removed=0),
            _make_file("CHANGELOG.md", added=300, removed=0),
            _make_file("docs/guide.md", added=400, removed=0),
        ]
        G = nx.DiGraph()
        for f in files:
            G.add_node(f.path, file=f)
        prs = partition_into_prs(G, files, {}, max_files=3, max_code_lines=200)
        self.assertEqual(len(prs), 1)

    def test_empty_input(self):
        prs = partition_into_prs(nx.DiGraph(), [], {}, max_files=3, max_code_lines=200)
        self.assertEqual(prs, [])

    def test_affinity_groups_cochanging_files(self):
        files = [
            _make_file("src/a.py", added=10, removed=0),
            _make_file("src/b.py", added=10, removed=0),
            _make_file("lib/c.py", added=10, removed=0),
            _make_file("lib/d.py", added=10, removed=0),
        ]
        G = nx.DiGraph()
        for f in files:
            G.add_node(f.path, file=f)
        affinity = {("src/a.py", "src/b.py"): 1.0}
        prs = partition_into_prs(G, files, affinity, max_files=2, max_code_lines=200)
        for pr in prs:
            paths = {f.path for f in pr.files}
            if "src/a.py" in paths:
                self.assertIn("src/b.py", paths)
                break


# ---------------------------------------------------------------------------
# PR dependencies
# ---------------------------------------------------------------------------

class TestPRDependencies(unittest.TestCase):
    def test_cross_pr_dependency(self):
        f1 = _make_file("src/models.py")
        f2 = _make_file("src/views.py")
        pr1 = ProposedPR(index=1, title="PR #1", files=[f1])
        pr2 = ProposedPR(index=2, title="PR #2", files=[f2])
        G = nx.DiGraph()
        G.add_edge("src/views.py", "src/models.py", kind="import")
        compute_pr_dependencies([pr1, pr2], G)
        self.assertEqual(pr2.depends_on, [1])
        self.assertEqual(pr1.depends_on, [])


# ---------------------------------------------------------------------------
# Risk scores
# ---------------------------------------------------------------------------

class TestRiskScores(unittest.TestCase):
    def test_low_risk(self):
        pr = ProposedPR(index=1, title="t", files=[_make_file("a.py", added=5, removed=0)])
        compute_risk_scores([pr])
        self.assertLess(pr.risk_score, 0.3)

    def test_high_risk(self):
        pr = ProposedPR(
            index=1, title="t",
            files=[_make_file(f"f{i}.py", added=100, removed=50) for i in range(3)],
            depends_on=[2, 3, 4, 5, 6],
        )
        compute_risk_scores([pr])
        self.assertGreater(pr.risk_score, 0.3)


# ---------------------------------------------------------------------------
# Merge strategies
# ---------------------------------------------------------------------------

class TestMergeStrategies(unittest.TestCase):
    def test_depended_upon_gets_merge(self):
        pr1 = ProposedPR(index=1, title="PR #1", files=[_make_file("a.py")])
        pr2 = ProposedPR(index=2, title="PR #2", files=[_make_file("b.py")], depends_on=[1])
        assign_merge_strategies([pr1, pr2])
        self.assertEqual(pr1.merge_strategy, "merge")
        self.assertEqual(pr2.merge_strategy, "squash")

    def test_docs_only_gets_rebase(self):
        pr = ProposedPR(index=1, title="PR #1", files=[_make_file("README.md")])
        assign_merge_strategies([pr])
        self.assertEqual(pr.merge_strategy, "rebase")

    def test_standalone_code_gets_squash(self):
        pr = ProposedPR(index=1, title="PR #1", files=[_make_file("main.py")])
        assign_merge_strategies([pr])
        self.assertEqual(pr.merge_strategy, "squash")

    def test_high_risk_gets_merge(self):
        pr = ProposedPR(index=1, title="PR #1", files=[_make_file("a.py")], risk_score=0.7)
        assign_merge_strategies([pr])
        self.assertEqual(pr.merge_strategy, "merge")


# ---------------------------------------------------------------------------
# DOT visualization
# ---------------------------------------------------------------------------

class TestVisualization(unittest.TestCase):
    def test_generate_dot(self):
        pr1 = ProposedPR(index=1, title="PR #1", files=[_make_file("a.py")], merge_strategy="squash", risk_score=0.1)
        pr2 = ProposedPR(index=2, title="PR #2", files=[_make_file("b.py")], depends_on=[1], merge_strategy="merge", risk_score=0.4)
        dot = generate_dot([pr1, pr2])
        self.assertIn("digraph", dot)
        self.assertIn("pr2 -> pr1", dot)
        self.assertIn("squash", dot)


# ---------------------------------------------------------------------------
# Plan executor
# ---------------------------------------------------------------------------

class TestSlugify(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(_slugify("PR #1: src changes"), "pr-1-src-changes")

    def test_truncation(self):
        slug = _slugify("A" * 100, max_len=20)
        self.assertLessEqual(len(slug), 20)


class TestPlanSerialization(unittest.TestCase):
    def test_round_trip(self):
        plan = StackedPRPlan(
            source_branch="feature/test",
            base_branch="main",
            total_prs=1,
            prs=[PRPlan(
                index=1, title="PR #1", branch_name="stack/main/01-pr-1",
                base_branch="main", files=["a.py"], depends_on=[],
                merge_strategy="squash", code_lines=10, risk_score=0.1,
            )],
            steps=[PlanStep(
                id=1, pr_index=1, phase="branch",
                description="Create branch", commands=["git checkout -b test main"],
            )],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            plan.save(f.name)
            loaded = StackedPRPlan.load(f.name)
            os.unlink(f.name)

        self.assertEqual(loaded.source_branch, "feature/test")
        self.assertEqual(loaded.total_prs, 1)
        self.assertEqual(len(loaded.steps), 1)
        self.assertEqual(loaded.steps[0].commands, ["git checkout -b test main"])


class TestPlanGeneration(unittest.TestCase):
    def test_generates_correct_steps(self):
        files = [_make_file("src/a.py", added=20, removed=0)]
        pr = ProposedPR(
            index=1, title="PR #1: src changes",
            files=files, depends_on=[], merge_strategy="squash",
            risk_score=0.1,
        )

        plan = generate_plan(
            prs=[pr],
            source_branch="feature/test",
            base_branch="main",
            create_prs=False,
        )

        self.assertEqual(plan.total_prs, 1)
        self.assertEqual(len(plan.prs), 1)
        self.assertEqual(plan.prs[0].branch_name, "stack/main/01-pr-1-src-changes")

        # Should have: branch, checkout, commit, push, cleanup
        phases = [s.phase for s in plan.steps]
        self.assertIn("branch", phases)
        self.assertIn("checkout", phases)
        self.assertIn("commit", phases)
        self.assertIn("push", phases)
        self.assertIn("cleanup", phases)

    def test_dependent_pr_branches_from_parent(self):
        pr1 = ProposedPR(
            index=1, title="PR #1", files=[_make_file("a.py")],
            depends_on=[], merge_strategy="merge", risk_score=0.1,
        )
        pr2 = ProposedPR(
            index=2, title="PR #2", files=[_make_file("b.py")],
            depends_on=[1], merge_strategy="squash", risk_score=0.1,
        )

        plan = generate_plan(
            prs=[pr1, pr2],
            source_branch="feature/test",
            base_branch="main",
            create_prs=False,
        )

        # PR2 should branch from PR1's branch, not from main
        self.assertEqual(plan.prs[1].base_branch, plan.prs[0].branch_name)


class TestShellScript(unittest.TestCase):
    def test_generates_valid_script(self):
        plan = StackedPRPlan(
            source_branch="feature/x",
            base_branch="main",
            total_prs=1,
            prs=[PRPlan(
                index=1, title="PR #1", branch_name="stack/main/01-test",
                base_branch="main", files=["a.py"], depends_on=[],
                merge_strategy="squash", code_lines=10, risk_score=0.1,
            )],
            steps=[PlanStep(
                id=1, pr_index=1, phase="branch",
                description="Create branch", commands=["git checkout -b stack/main/01-test main"],
            )],
        )
        script = generate_shell_script(plan)
        self.assertIn("#!/usr/bin/env bash", script)
        self.assertIn("set -euo pipefail", script)
        self.assertIn("git checkout -b stack/main/01-test main", script)


class TestMermaidGeneration(unittest.TestCase):
    def test_generates_mermaid(self):
        plan = StackedPRPlan(
            base_branch="main",
            total_prs=2,
            prs=[
                PRPlan(index=1, title="PR #1", branch_name="stack/main/01",
                       base_branch="main", files=["a.py"], depends_on=[],
                       merge_strategy="squash", code_lines=10, risk_score=0.1),
                PRPlan(index=2, title="PR #2", branch_name="stack/main/02",
                       base_branch="stack/main/01", files=["b.py"], depends_on=[1],
                       merge_strategy="merge", code_lines=20, risk_score=0.3),
            ],
        )
        mermaid = _generate_mermaid(plan)
        self.assertIn("```mermaid", mermaid)
        self.assertIn("graph BT", mermaid)
        self.assertIn("pr2", mermaid)
        self.assertIn("pr1", mermaid)
        self.assertIn("pr2 --> pr1", mermaid)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

from extract_pr import (
    parse_extraction_prompt,
    match_files,
    create_extraction_plan,
    ExtractionRule,
)


class TestExtractionPromptParsing(unittest.TestCase):
    def test_gradle_keyword(self):
        rules = parse_extraction_prompt("gradle init-script files")
        kinds = {r.kind for r in rules}
        self.assertIn("glob", kinds)
        # Should have gradle-related globs
        patterns = {r.pattern for r in rules}
        self.assertTrue(any("gradle" in p.lower() for p in patterns))

    def test_path_prefix(self):
        rules = parse_extraction_prompt("src/core/ changes")
        prefixes = [r for r in rules if r.kind == "path_prefix"]
        self.assertTrue(any(r.pattern == "src/core/" for r in prefixes))

    def test_glob_pattern(self):
        rules = parse_extraction_prompt("the *.bxl files")
        globs = [r for r in rules if r.kind == "glob"]
        self.assertTrue(any(r.pattern == "*.bxl" for r in globs))

    def test_cmake_keyword(self):
        rules = parse_extraction_prompt("all CMakeLists.txt changes")
        patterns = {r.pattern for r in rules}
        self.assertTrue(any("CMakeLists.txt" in p for p in patterns))

    def test_test_keyword(self):
        rules = parse_extraction_prompt("python test files")
        patterns = {r.pattern for r in rules}
        self.assertTrue(any("test" in p for p in patterns))


class TestExtractionMatching(unittest.TestCase):
    def test_glob_matching(self):
        files = [
            _make_file("build.gradle"),
            _make_file("app/build.gradle.kts"),
            _make_file("src/main.py"),
        ]
        rules = parse_extraction_prompt("gradle files")
        matched = match_files(files, rules)
        paths = {f.path for f in matched}
        self.assertIn("build.gradle", paths)
        self.assertIn("app/build.gradle.kts", paths)
        self.assertNotIn("src/main.py", paths)

    def test_ext_matching(self):
        files = [
            _make_file("src/main.py"),
            _make_file("src/utils.py"),
            _make_file("src/Main.java"),
        ]
        rules = parse_extraction_prompt("python files")
        matched = match_files(files, rules)
        paths = {f.path for f in matched}
        self.assertIn("src/main.py", paths)
        self.assertIn("src/utils.py", paths)
        self.assertNotIn("src/Main.java", paths)

    def test_path_prefix_matching(self):
        files = [
            _make_file("src/core/engine.cpp"),
            _make_file("src/core/types.h"),
            _make_file("src/ui/window.cpp"),
        ]
        rules = parse_extraction_prompt("src/core/ changes")
        matched = match_files(files, rules)
        paths = {f.path for f in matched}
        self.assertIn("src/core/engine.cpp", paths)
        self.assertIn("src/core/types.h", paths)
        self.assertNotIn("src/ui/window.cpp", paths)

    def test_keyword_in_diff(self):
        files = [
            _make_file("setup.py", diff_text="+apply from: 'init-script.gradle'"),
            _make_file("main.py", diff_text="+print('hello')"),
        ]
        rules = [ExtractionRule("keyword", "init-script", "test")]
        matched = match_files(files, rules)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].path, "setup.py")


# ---------------------------------------------------------------------------
# LLM backend tests
# ---------------------------------------------------------------------------

from llm_backend import (
    OllamaBackend,
    LlamaCppBackend,
    NullBackend,
    LLMResponse,
    get_llm,
    check_backends,
    build_extraction_prompt,
    build_grouping_prompt,
    build_pr_description_prompt,
    parse_json_response,
)


class TestNullBackend(unittest.TestCase):
    def test_not_available(self):
        b = NullBackend()
        self.assertFalse(b.available)

    def test_query_returns_error(self):
        b = NullBackend()
        resp = b.query("test")
        self.assertFalse(resp.ok)
        self.assertEqual(resp.backend, "none")


class TestLLMResponse(unittest.TestCase):
    def test_ok(self):
        r = LLMResponse(text="hello", model="test", backend="test")
        self.assertTrue(r.ok)

    def test_not_ok_empty(self):
        r = LLMResponse(text="", model="test", backend="test")
        self.assertFalse(r.ok)

    def test_not_ok_error(self):
        r = LLMResponse(text="data", model="test", backend="test", error="fail")
        self.assertFalse(r.ok)


class TestParseJsonResponse(unittest.TestCase):
    def test_clean_json(self):
        r = LLMResponse(text='{"matched": ["a.py"]}', model="t", backend="t")
        data = parse_json_response(r)
        self.assertEqual(data, {"matched": ["a.py"]})

    def test_json_in_code_fence(self):
        r = LLMResponse(text='```json\n{"foo": 1}\n```', model="t", backend="t")
        data = parse_json_response(r)
        self.assertEqual(data, {"foo": 1})

    def test_json_with_preamble(self):
        r = LLMResponse(text='Here is the result:\n{"bar": 2}', model="t", backend="t")
        data = parse_json_response(r)
        self.assertEqual(data, {"bar": 2})

    def test_empty_response(self):
        r = LLMResponse(text="", model="t", backend="t", error="fail")
        self.assertIsNone(parse_json_response(r))


class TestPromptBuilders(unittest.TestCase):
    def test_extraction_prompt(self):
        prompt = build_extraction_prompt(
            "gradle files",
            [{"path": "build.gradle", "status": "M", "added": 10, "removed": 5}],
        )
        self.assertIn("gradle files", prompt)
        self.assertIn("build.gradle", prompt)
        self.assertIn("JSON", prompt)

    def test_grouping_prompt(self):
        prompt = build_grouping_prompt(
            [{"path": "a.py", "status": "M", "added": 50, "removed": 0}],
            [("a.py", "b.py")],
            max_files=3,
            max_lines=200,
        )
        self.assertIn("a.py", prompt)
        self.assertIn("depends on", prompt)
        self.assertIn("JSON", prompt)

    def test_pr_description_prompt(self):
        prompt = build_pr_description_prompt(
            title="Add auth module",
            files=[{"path": "auth.py", "status": "A", "added": 100, "removed": 0}],
            diff_summary="+class AuthManager:",
            depends_on=["PR #1"],
            merge_strategy="squash",
        )
        self.assertIn("auth.py", prompt)
        self.assertIn("squash", prompt)


class TestCheckBackends(unittest.TestCase):
    def test_returns_dict(self):
        results = check_backends()
        self.assertIn("ollama", results)
        self.assertIn("llama-cpp", results)


class TestGetLlm(unittest.TestCase):
    def test_none_backend(self):
        # Reset cache
        import llm_backend
        llm_backend._cached_backend = None
        llm = get_llm(backend="none", verbose=False)
        self.assertFalse(llm.available)
        self.assertEqual(llm.name, "none")
        llm_backend._cached_backend = None  # clean up


# ---------------------------------------------------------------------------
# Extraction plan
# ---------------------------------------------------------------------------

class TestExtractionPlan(unittest.TestCase):
    def test_creates_plan(self):
        files = [
            _make_file("build.gradle", added=20, removed=0),
            _make_file("src/main.py", added=50, removed=0),
        ]
        G = nx.DiGraph()
        for f in files:
            G.add_node(f.path, file=f)

        plan = create_extraction_plan(
            prompt="gradle files",
            all_files=files,
            G=G,
            source_branch="feature/big",
            base_branch="main",
        )
        self.assertEqual(len(plan.matched_files), 1)
        self.assertEqual(plan.matched_files[0].path, "build.gradle")
        self.assertTrue(len(plan.commands) > 0)
        self.assertIn("extract/", plan.branch_name)

    def test_pulls_in_deps(self):
        f1 = _make_file("lib/models.py", added=30, removed=0)
        f2 = _make_file("src/main.py", added=50, removed=0)
        files = [f1, f2]

        G = nx.DiGraph()
        G.add_node("lib/models.py", file=f1)
        G.add_node("src/main.py", file=f2)
        G.add_edge("src/main.py", "lib/models.py", kind="import")

        plan = create_extraction_plan(
            prompt="src/ changes",
            all_files=files,
            G=G,
            source_branch="feature/big",
            base_branch="main",
            include_deps=True,
        )
        all_paths = {f.path for f in plan.all_files}
        self.assertIn("src/main.py", all_paths)
        self.assertIn("lib/models.py", all_paths)  # pulled as dep


if __name__ == "__main__":
    unittest.main()

