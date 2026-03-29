"""
Multi-language import/dependency & complexity parser.

Uses a layered extraction strategy (best-first):
  1. tree-sitter  — precise AST queries for all supported languages
  2. ast-grep     — pattern-based AST matching
  3. Python ast   — stdlib AST for Python specifically
  4. ctags        — symbol extraction for cross-file dependency graphs
  5. Regex        — battle-tested fallback patterns

Codebase complexity is measured via scc (Sloc Cloc and Code).

Supported languages:
  Python, C, C++, Java, Kotlin, Go, Rust, JavaScript, TypeScript,
  CMake, Buck2, Starlark/Bazel
"""

from __future__ import annotations

import ast as python_ast
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ImportInfo:
    """A single import/dependency reference extracted from source."""
    raw: str          # the raw import string as written
    module: str       # normalized module/path for matching
    kind: str = ""    # "module", "header", "package", "target", etc.


@dataclass
class SymbolInfo:
    """A symbol (function, class, variable) defined in a file."""
    name: str
    kind: str         # "function", "class", "struct", "variable", etc.
    line: int = 0
    scope: str = ""   # parent scope if any


@dataclass
class FunctionComplexity:
    """Per-function complexity from lizard."""
    name: str
    start_line: int
    end_line: int
    cyclomatic: int
    cognitive: int         # cognitive complexity (lizard extension)
    token_count: int
    parameter_count: int
    length: int            # lines of code


@dataclass
class FileComplexity:
    """Complexity metrics for a single file."""
    language: str
    lines: int
    code: int
    comments: int
    blanks: int
    complexity: int                                       # cyclomatic total
    functions: list[FunctionComplexity] = field(default_factory=list)
    avg_cyclomatic: float = 0.0
    max_cyclomatic: int = 0


# ---------------------------------------------------------------------------
# tree-sitter queries per language
# ---------------------------------------------------------------------------

# S-expression queries for tree-sitter to extract import nodes.
# These run via `tree-sitter query` or the tree_sitter Python bindings.
_TS_IMPORT_QUERIES: dict[str, str] = {
    "python": """
        (import_statement (dotted_name) @import)
        (import_from_statement module_name: (dotted_name) @import)
        (import_from_statement module_name: (relative_import) @import)
    """,
    "java": """
        (import_declaration (scoped_identifier) @import)
    """,
    "kotlin": """
        (import_header (identifier) @import)
    """,
    "go": """
        (import_spec path: (interpreted_string_literal) @import)
    """,
    "rust": """
        (use_declaration argument: (scoped_identifier) @import)
        (use_declaration argument: (use_wildcard) @import)
        (mod_item name: (identifier) @import)
        (extern_crate_declaration name: (identifier) @import)
    """,
    "c": """
        (preproc_include path: (string_literal) @import)
        (preproc_include path: (system_lib_string) @import)
    """,
    "cpp": """
        (preproc_include path: (string_literal) @import)
        (preproc_include path: (system_lib_string) @import)
    """,
    "javascript": """
        (import_statement source: (string) @import)
        (call_expression
          function: (identifier) @_fn (#eq? @_fn "require")
          arguments: (arguments (string) @import))
    """,
    "typescript": """
        (import_statement source: (string) @import)
        (call_expression
          function: (identifier) @_fn (#eq? @_fn "require")
          arguments: (arguments (string) @import))
    """,
}

# Symbol definition queries for tree-sitter (exported/public symbols)
_TS_SYMBOL_QUERIES: dict[str, str] = {
    "python": """
        (function_definition name: (identifier) @symbol)
        (class_definition name: (identifier) @symbol)
        (assignment left: (identifier) @symbol)
    """,
    "java": """
        (class_declaration name: (identifier) @symbol)
        (method_declaration name: (identifier) @symbol)
        (interface_declaration name: (identifier) @symbol)
    """,
    "kotlin": """
        (class_declaration (type_identifier) @symbol)
        (function_declaration (simple_identifier) @symbol)
        (object_declaration (type_identifier) @symbol)
    """,
    "go": """
        (function_declaration name: (identifier) @symbol)
        (method_declaration name: (field_identifier) @symbol)
        (type_declaration (type_spec name: (type_identifier) @symbol))
    """,
    "rust": """
        (function_item name: (identifier) @symbol)
        (struct_item name: (type_identifier) @symbol)
        (enum_item name: (type_identifier) @symbol)
        (trait_item name: (type_identifier) @symbol)
        (impl_item type: (type_identifier) @symbol)
    """,
    "c": """
        (function_definition declarator: (function_declarator declarator: (identifier) @symbol))
        (struct_specifier name: (type_identifier) @symbol)
        (enum_specifier name: (type_identifier) @symbol)
        (type_definition declarator: (type_identifier) @symbol)
    """,
    "cpp": """
        (function_definition declarator: (function_declarator declarator: (identifier) @symbol))
        (class_specifier name: (type_identifier) @symbol)
        (struct_specifier name: (type_identifier) @symbol)
        (namespace_definition name: (identifier) @symbol)
    """,
    "javascript": """
        (function_declaration name: (identifier) @symbol)
        (class_declaration name: (identifier) @symbol)
        (export_statement declaration: (function_declaration name: (identifier) @symbol))
        (export_statement declaration: (class_declaration name: (identifier) @symbol))
    """,
    "typescript": """
        (function_declaration name: (identifier) @symbol)
        (class_declaration name: (identifier) @symbol)
        (interface_declaration name: (type_identifier) @symbol)
        (type_alias_declaration name: (type_identifier) @symbol)
        (export_statement declaration: (function_declaration name: (identifier) @symbol))
    """,
}


# ---------------------------------------------------------------------------
# tree-sitter extraction via Python bindings
# ---------------------------------------------------------------------------

def _parse_tree_sitter(source: str, lang: str, query_text: str) -> list[str]:
    """
    Run a tree-sitter query and return matched text nodes.
    Uses tree_sitter Python bindings with pre-installed language grammars.
    """
    try:
        import tree_sitter_languages
        parser = tree_sitter_languages.get_parser(lang)
        ts_lang = tree_sitter_languages.get_language(lang)
    except (ImportError, Exception):
        return []

    tree = parser.parse(source.encode("utf-8"))
    try:
        query = ts_lang.query(query_text)
    except Exception:
        return []

    captures = query.captures(tree.root_node)
    results: list[str] = []
    for node, tag in captures:
        if tag.startswith("_"):
            continue
        text = node.text.decode("utf-8").strip("'\"")
        results.append(text)
    return results


def extract_imports_tree_sitter(source: str, lang: str) -> list[ImportInfo]:
    """Extract imports using tree-sitter AST queries."""
    query = _TS_IMPORT_QUERIES.get(lang)
    if not query:
        return []

    texts = _parse_tree_sitter(source, lang, query)
    results: list[ImportInfo] = []
    seen: set[str] = set()
    for text in texts:
        if text not in seen:
            seen.add(text)
            results.append(ImportInfo(raw=text, module=_normalize_module(text, lang), kind="module"))
    return results


def extract_symbols_tree_sitter(source: str, lang: str) -> list[SymbolInfo]:
    """Extract defined symbols using tree-sitter AST queries."""
    query = _TS_SYMBOL_QUERIES.get(lang)
    if not query:
        return []

    texts = _parse_tree_sitter(source, lang, query)
    results: list[SymbolInfo] = []
    seen: set[str] = set()
    for text in texts:
        if text not in seen:
            seen.add(text)
            results.append(SymbolInfo(name=text, kind="symbol"))
    return results


# ---------------------------------------------------------------------------
# ast-grep extraction
# ---------------------------------------------------------------------------

# Multiple patterns per language for thorough coverage
_AST_GREP_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "python": [
        ("import $MOD", "module"),
        ("from $MOD import $NAMES", "module"),
    ],
    "java": [
        ("import $MOD;", "package"),
        ("import static $MOD;", "package"),
    ],
    "kotlin": [
        ("import $MOD", "package"),
    ],
    "go": [
        ('import "$MOD"', "package"),
    ],
    "rust": [
        ("use $MOD;", "module"),
        ("use $MOD as $ALIAS;", "module"),
        ("mod $MOD;", "module"),
        ("extern crate $MOD;", "crate"),
    ],
    "c": [
        ('#include "$MOD"', "header"),
        ("#include <$MOD>", "header"),
    ],
    "cpp": [
        ('#include "$MOD"', "header"),
        ("#include <$MOD>", "header"),
    ],
    "typescript": [
        ("import $SPEC from '$MOD'", "module"),
        ('import $SPEC from "$MOD"', "module"),
        ("require('$MOD')", "module"),
        ('require("$MOD")', "module"),
    ],
    "javascript": [
        ("import $SPEC from '$MOD'", "module"),
        ('import $SPEC from "$MOD"', "module"),
        ("require('$MOD')", "module"),
        ('require("$MOD")', "module"),
    ],
}


def extract_imports_ast_grep(file_path: str, source: str, lang: str) -> list[ImportInfo]:
    """Use ast-grep for pattern-based AST import extraction."""
    patterns = _AST_GREP_PATTERNS.get(lang)
    if not patterns:
        return []

    results: list[ImportInfo] = []
    seen: set[str] = set()

    # Write source to temp file for ast-grep to process
    suffix = Path(file_path).suffix or ".txt"
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
        f.write(source)
        tmp_path = f.name

    try:
        for pattern, kind in patterns:
            try:
                result = subprocess.run(
                    ["ast-grep", "--pattern", pattern, "--lang", lang, "--json", tmp_path],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode != 0:
                    continue
                matches = json.loads(result.stdout)
                for match in matches:
                    meta = match.get("metaVariables", {})
                    mod_node = meta.get("single", {}).get("MOD") or meta.get("MOD")
                    if isinstance(mod_node, dict):
                        mod = mod_node.get("text", "")
                    else:
                        mod = match.get("text", "")
                    mod = mod.strip("'\"")
                    if mod and mod not in seen:
                        seen.add(mod)
                        results.append(ImportInfo(
                            raw=mod,
                            module=_normalize_module(mod, lang),
                            kind=kind,
                        ))
            except (subprocess.TimeoutExpired, json.JSONDecodeError):
                continue
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return results


# ---------------------------------------------------------------------------
# ctags symbol extraction
# ---------------------------------------------------------------------------

def extract_symbols_ctags(file_path: str, source: str) -> list[SymbolInfo]:
    """
    Run universal-ctags on source to get defined symbols.
    Returns list of SymbolInfo for cross-file symbol resolution.
    """
    suffix = Path(file_path).suffix or ".txt"
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
        f.write(source)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [
                "ctags", "--output-format=json", "--fields=+neKS",
                "--kinds-all=*", "-f", "-", tmp_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    symbols: list[SymbolInfo] = []
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
            name = entry.get("name", "")
            kind = entry.get("kind", "unknown")
            line_no = entry.get("line", 0)
            scope = entry.get("scope", "")
            if name:
                symbols.append(SymbolInfo(
                    name=name, kind=kind, line=line_no, scope=scope,
                ))
        except json.JSONDecodeError:
            continue
    return symbols


# ---------------------------------------------------------------------------
# scc complexity analysis
# ---------------------------------------------------------------------------

def analyze_complexity_scc(file_paths: list[str]) -> dict[str, FileComplexity]:
    """
    Run scc on a set of files and return per-file complexity metrics.
    """
    if not file_paths:
        return {}

    try:
        result = subprocess.run(
            ["scc", "--format", "json", "--by-file", "--no-cocomo", *file_paths],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    metrics: dict[str, FileComplexity] = {}
    try:
        data = json.loads(result.stdout)
        for lang_group in data:
            language = lang_group.get("Name", "")
            for file_entry in lang_group.get("Files", []):
                path = file_entry.get("Location", "")
                metrics[path] = FileComplexity(
                    language=language,
                    lines=file_entry.get("Lines", 0),
                    code=file_entry.get("Code", 0),
                    comments=file_entry.get("Comments", 0),
                    blanks=file_entry.get("Blank", 0),
                    complexity=file_entry.get("Complexity", 0),
                )
    except (json.JSONDecodeError, KeyError):
        pass

    return metrics


def analyze_complexity_scc_dir(directory: str = ".") -> dict[str, FileComplexity]:
    """Run scc on an entire directory tree."""
    try:
        result = subprocess.run(
            ["scc", "--format", "json", "--by-file", "--no-cocomo", directory],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return {}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    metrics: dict[str, FileComplexity] = {}
    try:
        data = json.loads(result.stdout)
        for lang_group in data:
            language = lang_group.get("Name", "")
            for file_entry in lang_group.get("Files", []):
                path = file_entry.get("Location", "")
                metrics[path] = FileComplexity(
                    language=language,
                    lines=file_entry.get("Lines", 0),
                    code=file_entry.get("Code", 0),
                    comments=file_entry.get("Comments", 0),
                    blanks=file_entry.get("Blank", 0),
                    complexity=file_entry.get("Complexity", 0),
                )
    except (json.JSONDecodeError, KeyError):
        pass

    return metrics


# ---------------------------------------------------------------------------
# Lizard — function-level cyclomatic + cognitive complexity
# ---------------------------------------------------------------------------

def analyze_complexity_lizard(file_paths: list[str]) -> dict[str, FileComplexity]:
    """
    Run lizard on files for function-level complexity metrics.
    Falls back to scc if lizard is not installed.

    lizard gives us:
      - Per-function cyclomatic complexity
      - Function length, parameter count, token count
      - Works for C/C++, Java, Python, Go, Rust, JS/TS, Kotlin, and more
    """
    if not file_paths:
        return {}

    try:
        import lizard
    except ImportError:
        # Fall back to scc
        return analyze_complexity_scc(file_paths)

    metrics: dict[str, FileComplexity] = {}

    for path in file_paths:
        if not os.path.exists(path):
            continue
        try:
            analysis = lizard.analyze_file(path)
        except Exception:
            continue

        functions: list[FunctionComplexity] = []
        total_cyclomatic = 0

        for func in analysis.function_list:
            cc = func.cyclomatic_complexity
            total_cyclomatic += cc
            functions.append(FunctionComplexity(
                name=func.name,
                start_line=func.start_line,
                end_line=func.end_line,
                cyclomatic=cc,
                cognitive=0,  # lizard doesn't compute cognitive by default
                token_count=func.token_count,
                parameter_count=len(func.parameters),
                length=func.nloc,
            ))

        avg_cc = total_cyclomatic / len(functions) if functions else 0
        max_cc = max((f.cyclomatic for f in functions), default=0)

        metrics[path] = FileComplexity(
            language=analysis.filename.rsplit(".", 1)[-1] if "." in analysis.filename else "",
            lines=analysis.nloc,
            code=analysis.nloc,
            comments=0,
            blanks=0,
            complexity=total_cyclomatic,
            functions=functions,
            avg_cyclomatic=round(avg_cc, 1),
            max_cyclomatic=max_cc,
        )

    return metrics


def analyze_complexity_best(file_paths: list[str]) -> dict[str, FileComplexity]:
    """Use the best available complexity analyzer: lizard > scc."""
    try:
        import lizard  # noqa: F401
        return analyze_complexity_lizard(file_paths)
    except ImportError:
        return analyze_complexity_scc(file_paths)


import os  # needed by lizard functions above


# ---------------------------------------------------------------------------
# Python AST (stdlib, always available)
# ---------------------------------------------------------------------------

def _parse_python_ast(source: str) -> list[ImportInfo]:
    try:
        tree = python_ast.parse(source)
    except SyntaxError:
        return _parse_python_regex(source)

    results: list[ImportInfo] = []
    for node in python_ast.walk(tree):
        if isinstance(node, python_ast.Import):
            for alias in node.names:
                results.append(ImportInfo(
                    raw=alias.name, module=alias.name, kind="module",
                ))
        elif isinstance(node, python_ast.ImportFrom):
            mod = node.module or ""
            level = node.level or 0
            prefix = "." * level
            results.append(ImportInfo(
                raw=f"{prefix}{mod}", module=mod, kind="module",
            ))
    return results


_PY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE
)


def _parse_python_regex(source: str) -> list[ImportInfo]:
    results: list[ImportInfo] = []
    for m in _PY_IMPORT_RE.finditer(source):
        mod = m.group(1) or m.group(2)
        results.append(ImportInfo(raw=mod, module=mod, kind="module"))
    return results


# ---------------------------------------------------------------------------
# Regex fallback parsers (all languages)
# ---------------------------------------------------------------------------

_CPP_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+[<"]([\w/.\-+]+)[>"]', re.MULTILINE)

def _parse_cpp_regex(source: str) -> list[ImportInfo]:
    return [
        ImportInfo(raw=m.group(1), module=m.group(1).replace("/", ".").removesuffix(".h").removesuffix(".hpp"), kind="header")
        for m in _CPP_INCLUDE_RE.finditer(source)
    ]


_JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)

def _parse_java_regex(source: str) -> list[ImportInfo]:
    return [ImportInfo(raw=m.group(1), module=m.group(1), kind="package") for m in _JAVA_IMPORT_RE.finditer(source)]


_KOTLIN_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)

def _parse_kotlin_regex(source: str) -> list[ImportInfo]:
    return [ImportInfo(raw=m.group(1), module=m.group(1), kind="package") for m in _KOTLIN_IMPORT_RE.finditer(source)]


_GO_SINGLE_IMPORT_RE = re.compile(r'^\s*import\s+"([\w./\-]+)"', re.MULTILINE)
_GO_BLOCK_IMPORT_RE = re.compile(r"import\s*\((.*?)\)", re.DOTALL)
_GO_IMPORT_PATH_RE = re.compile(r'"([\w./\-]+)"')

def _parse_go_regex(source: str) -> list[ImportInfo]:
    results, seen = [], set()
    for m in _GO_SINGLE_IMPORT_RE.finditer(source):
        pkg = m.group(1)
        if pkg not in seen:
            seen.add(pkg); results.append(ImportInfo(raw=pkg, module=pkg, kind="package"))
    for block in _GO_BLOCK_IMPORT_RE.finditer(source):
        for pm in _GO_IMPORT_PATH_RE.finditer(block.group(1)):
            pkg = pm.group(1)
            if pkg not in seen:
                seen.add(pkg); results.append(ImportInfo(raw=pkg, module=pkg, kind="package"))
    return results


_RUST_USE_RE = re.compile(r"^\s*(?:pub\s+)?use\s+([\w:]+)", re.MULTILINE)
_RUST_MOD_RE = re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)\s*[;{]", re.MULTILINE)
_RUST_EXTERN_RE = re.compile(r"^\s*extern\s+crate\s+(\w+)", re.MULTILINE)

def _parse_rust_regex(source: str) -> list[ImportInfo]:
    results, seen = [], set()
    for m in _RUST_USE_RE.finditer(source):
        path = m.group(1); mod = path.replace("::", ".")
        if mod not in seen:
            seen.add(mod); results.append(ImportInfo(raw=path, module=mod, kind="module"))
    for m in _RUST_MOD_RE.finditer(source):
        mod = m.group(1)
        if mod not in seen:
            seen.add(mod); results.append(ImportInfo(raw=mod, module=mod, kind="module"))
    for m in _RUST_EXTERN_RE.finditer(source):
        crate = m.group(1)
        if crate not in seen:
            seen.add(crate); results.append(ImportInfo(raw=crate, module=crate, kind="crate"))
    return results


_JS_IMPORT_FROM_RE = re.compile(r"""(?:import|export)\s+.*?\s+from\s+['"]([\w@./\-]+)['"]""", re.MULTILINE)
_JS_IMPORT_SIDE_RE = re.compile(r"""import\s+['"]([\w@./\-]+)['"]""", re.MULTILINE)
_JS_REQUIRE_RE = re.compile(r"""require\s*\(\s*['"]([\w@./\-]+)['"]\s*\)""", re.MULTILINE)

def _parse_js_ts_regex(source: str) -> list[ImportInfo]:
    results, seen = [], set()
    for pat in [_JS_IMPORT_FROM_RE, _JS_IMPORT_SIDE_RE, _JS_REQUIRE_RE]:
        for m in pat.finditer(source):
            mod = m.group(1)
            if mod not in seen:
                seen.add(mod); results.append(ImportInfo(raw=mod, module=mod, kind="module"))
    return results


_CMAKE_INCLUDE_RE = re.compile(r"include\s*\(\s*(\S+)\s*\)", re.MULTILINE)
_CMAKE_FIND_PKG_RE = re.compile(r"find_package\s*\(\s*(\w+)", re.MULTILINE)
_CMAKE_ADD_SUBDIR_RE = re.compile(r"add_subdirectory\s*\(\s*([^\s)]+)", re.MULTILINE)
_CMAKE_TARGET_LINK_RE = re.compile(r"target_link_libraries\s*\([^)]*\b(\w+::\w+)", re.MULTILINE)

def _parse_cmake_regex(source: str) -> list[ImportInfo]:
    results, seen = [], set()
    for pat, kind in [
        (_CMAKE_INCLUDE_RE, "include"), (_CMAKE_FIND_PKG_RE, "package"),
        (_CMAKE_ADD_SUBDIR_RE, "subdirectory"), (_CMAKE_TARGET_LINK_RE, "target"),
    ]:
        for m in pat.finditer(source):
            val = m.group(1).strip("\"'")
            if val not in seen:
                seen.add(val); results.append(ImportInfo(raw=val, module=val, kind=kind))
    return results


_BUCK_LOAD_RE = re.compile(r'load\s*\(\s*"([^"]+)"', re.MULTILINE)
_BUCK_DEPS_RE = re.compile(r'deps\s*=\s*\[(.*?)\]', re.DOTALL)
_BUCK_STRING_RE = re.compile(r'"([^"]+)"')

def _parse_buck2_regex(source: str) -> list[ImportInfo]:
    results, seen = [], set()
    for m in _BUCK_LOAD_RE.finditer(source):
        t = m.group(1)
        if t not in seen:
            seen.add(t); results.append(ImportInfo(raw=t, module=t, kind="load"))
    for block in _BUCK_DEPS_RE.finditer(source):
        for sm in _BUCK_STRING_RE.finditer(block.group(1)):
            dep = sm.group(1)
            if dep not in seen:
                seen.add(dep); results.append(ImportInfo(raw=dep, module=dep, kind="target"))
    return results


_STARLARK_LOAD_RE = re.compile(r'load\s*\(\s*"([^"]+)"', re.MULTILINE)

def _parse_starlark_regex(source: str) -> list[ImportInfo]:
    return [ImportInfo(raw=m.group(1), module=m.group(1), kind="load") for m in _STARLARK_LOAD_RE.finditer(source)]


# ---------------------------------------------------------------------------
# Gradle (Groovy DSL) — build.gradle
# ---------------------------------------------------------------------------

# implementation 'com.google.guava:guava:31.1-jre'
# implementation "com.google.guava:guava:$guavaVersion"
# api project(':core')
# classpath "com.android.tools.build:gradle:7.0.0"
# apply plugin: 'java'
# apply from: 'other.gradle'
_GRADLE_DEP_RE = re.compile(
    r"""(?:implementation|api|compileOnly|runtimeOnly|testImplementation|"""
    r"""testRuntimeOnly|classpath|annotationProcessor)\s*"""
    r"""[\(]?\s*['"]([\w.:@\-/\${}]+)['"]\s*[\)]?""",
    re.MULTILINE,
)
_GRADLE_PROJECT_RE = re.compile(
    r"""project\s*\(\s*['"]([:.\w\-/]+)['"]\s*\)""", re.MULTILINE,
)
_GRADLE_APPLY_FROM_RE = re.compile(
    r"""apply\s+from:\s*['"]([\w./\-]+)['"]""", re.MULTILINE,
)
_GRADLE_PLUGIN_RE = re.compile(
    r"""(?:apply\s+plugin:\s*['"]([\w.\-]+)['"]|id\s*\(?\s*['"]([\w.\-]+)['"]\s*\)?)""",
    re.MULTILINE,
)
_GRADLE_BUILDSCRIPT_CLASSPATH_RE = re.compile(
    r"""classpath\s*[\(]?\s*['"]([\w.:@\-/]+)['"]\s*[\)]?""", re.MULTILINE,
)


def _parse_gradle_groovy_regex(source: str) -> list[ImportInfo]:
    results, seen = [], set()

    # Dependencies
    for m in _GRADLE_DEP_RE.finditer(source):
        dep = m.group(1)
        # Normalize: com.google.guava:guava:31.1-jre → com.google.guava:guava
        parts = dep.split(":")
        mod = ":".join(parts[:2]) if len(parts) >= 2 else dep
        if mod not in seen:
            seen.add(mod)
            results.append(ImportInfo(raw=dep, module=mod, kind="dependency"))

    # Project dependencies: project(':core') → :core
    for m in _GRADLE_PROJECT_RE.finditer(source):
        proj = m.group(1)
        if proj not in seen:
            seen.add(proj)
            results.append(ImportInfo(raw=proj, module=proj, kind="project"))

    # apply from
    for m in _GRADLE_APPLY_FROM_RE.finditer(source):
        path = m.group(1)
        if path not in seen:
            seen.add(path)
            results.append(ImportInfo(raw=path, module=path, kind="script"))

    # Plugins
    for m in _GRADLE_PLUGIN_RE.finditer(source):
        plugin = m.group(1) or m.group(2)
        if plugin and plugin not in seen:
            seen.add(plugin)
            results.append(ImportInfo(raw=plugin, module=plugin, kind="plugin"))

    return results


# ---------------------------------------------------------------------------
# Gradle Kotlin DSL — build.gradle.kts, settings.gradle.kts
# ---------------------------------------------------------------------------

# implementation("com.google.guava:guava:31.1-jre")
# api(project(":core"))
# plugins { id("org.jetbrains.kotlin.jvm") version "1.8.0" }
# include(":app", ":core", ":utils")
_GRADLE_KTS_DEP_RE = re.compile(
    r"""(?:implementation|api|compileOnly|runtimeOnly|testImplementation|"""
    r"""testRuntimeOnly|classpath|annotationProcessor)\s*\(\s*"""
    r"""['"]([\w.:@\-/]+)['"]\s*\)""",
    re.MULTILINE,
)
_GRADLE_KTS_PROJECT_RE = re.compile(
    r"""project\s*\(\s*['"]([\w:.\-]+)['"]\s*\)""", re.MULTILINE,
)
_GRADLE_KTS_PLUGIN_RE = re.compile(
    r"""id\s*\(\s*['"]([\w.\-]+)['"]\s*\)""", re.MULTILINE,
)
_GRADLE_KTS_INCLUDE_RE = re.compile(
    r"""include\s*\((.*?)\)""", re.DOTALL,
)
_GRADLE_KTS_STRING_RE = re.compile(r"""['"]([\w:.\-/]+)['"]""")
_GRADLE_KTS_APPLY_RE = re.compile(
    r"""apply\s*\(\s*from\s*=\s*['"]([\w./\-]+)['"]\s*\)""", re.MULTILINE,
)


def _parse_gradle_kts_regex(source: str) -> list[ImportInfo]:
    results, seen = [], set()

    # Dependencies
    for m in _GRADLE_KTS_DEP_RE.finditer(source):
        dep = m.group(1)
        parts = dep.split(":")
        mod = ":".join(parts[:2]) if len(parts) >= 2 else dep
        if mod not in seen:
            seen.add(mod)
            results.append(ImportInfo(raw=dep, module=mod, kind="dependency"))

    # Project deps
    for m in _GRADLE_KTS_PROJECT_RE.finditer(source):
        proj = m.group(1)
        if proj not in seen:
            seen.add(proj)
            results.append(ImportInfo(raw=proj, module=proj, kind="project"))

    # Plugins
    for m in _GRADLE_KTS_PLUGIN_RE.finditer(source):
        plugin = m.group(1)
        if plugin not in seen:
            seen.add(plugin)
            results.append(ImportInfo(raw=plugin, module=plugin, kind="plugin"))

    # include(":app", ":core")  — settings.gradle.kts
    for block in _GRADLE_KTS_INCLUDE_RE.finditer(source):
        for sm in _GRADLE_KTS_STRING_RE.finditer(block.group(1)):
            proj = sm.group(1)
            if proj not in seen:
                seen.add(proj)
                results.append(ImportInfo(raw=proj, module=proj, kind="project"))

    # apply(from = "...")
    for m in _GRADLE_KTS_APPLY_RE.finditer(source):
        path = m.group(1)
        if path not in seen:
            seen.add(path)
            results.append(ImportInfo(raw=path, module=path, kind="script"))

    # Also parse Kotlin imports (the file can have import statements)
    results.extend(_parse_kotlin_regex(source))

    return results


# ---------------------------------------------------------------------------
# BXL (Buck Extension Language) — .bxl files
# ---------------------------------------------------------------------------

# BXL is a Starlark dialect for Buck2 with additional built-ins:
#   bxl.main(), ctx.analysis(), ctx.target_universe(), etc.
# load() statements are the main dependency mechanism
_BXL_LOAD_RE = re.compile(r'load\s*\(\s*"([^"]+)"', re.MULTILINE)
# BXL-specific: target patterns in strings
_BXL_TARGET_RE = re.compile(r'"(//[\w/.\-]+(?::[\w.\-]+)?)"', re.MULTILINE)
# ctx.lazy.* references
_BXL_LAZY_RE = re.compile(r'ctx\.lazy\.([a-zA-Z_]\w*)', re.MULTILINE)


def _parse_bxl_regex(source: str) -> list[ImportInfo]:
    results, seen = [], set()

    for m in _BXL_LOAD_RE.finditer(source):
        target = m.group(1)
        if target not in seen:
            seen.add(target)
            results.append(ImportInfo(raw=target, module=target, kind="load"))

    for m in _BXL_TARGET_RE.finditer(source):
        target = m.group(1)
        if target not in seen:
            seen.add(target)
            results.append(ImportInfo(raw=target, module=target, kind="target"))

    return results


# ---------------------------------------------------------------------------
# Groovy — .groovy files (non-Gradle)
# ---------------------------------------------------------------------------

_GROOVY_IMPORT_RE = re.compile(
    r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;?", re.MULTILINE,
)

def _parse_groovy_regex(source: str) -> list[ImportInfo]:
    return [
        ImportInfo(raw=m.group(1), module=m.group(1), kind="package")
        for m in _GROOVY_IMPORT_RE.finditer(source)
    ]


# ---------------------------------------------------------------------------
# Module normalization
# ---------------------------------------------------------------------------

def _normalize_module(raw: str, lang: str) -> str:
    """Normalize a raw import string to a dot-separated module key."""
    cleaned = raw.strip("'\"<>")
    if lang in ("c", "cpp"):
        return cleaned.replace("/", ".").removesuffix(".h").removesuffix(".hpp").removesuffix(".hxx")
    if lang in ("rust",):
        return cleaned.replace("::", ".")
    if lang in ("go",):
        return cleaned  # Go import paths are already canonical
    return cleaned.replace("/", ".").replace("\\", ".")


# ---------------------------------------------------------------------------
# Extension → language mapping
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    # Python
    ".py": "python", ".pyi": "python",
    # C / C++
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hxx": "cpp",
    # Java
    ".java": "java",
    # Kotlin
    ".kt": "kotlin", ".kts": "kotlin",
    # Go
    ".go": "go",
    # Rust
    ".rs": "rust",
    # JavaScript / TypeScript
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    # CMake
    ".cmake": "cmake",
    # Starlark / Bazel
    ".bzl": "starlark", ".star": "starlark",
    # BXL (Buck Extension Language)
    ".bxl": "bxl",
    # Groovy
    ".groovy": "groovy",
    # Gradle Groovy DSL (handled by filename, but .gradle extension too)
    ".gradle": "gradle_groovy",
}

_FILENAME_TO_LANG: dict[str, str] = {
    # CMake
    "CMakeLists.txt": "cmake",
    # Buck2
    "BUCK": "buck2", "TARGETS": "buck2",
    # Bazel / Starlark
    "BUILD": "starlark", "BUILD.bazel": "starlark", "WORKSPACE": "starlark",
    "WORKSPACE.bazel": "starlark",
    # Gradle
    "build.gradle": "gradle_groovy",
    "settings.gradle": "gradle_groovy",
    "build.gradle.kts": "gradle_kts",
    "settings.gradle.kts": "gradle_kts",
    "gradle.properties": None,  # no imports to parse
    "buildSrc/build.gradle.kts": "gradle_kts",
}

# Regex fallback per language
_REGEX_PARSERS: dict[str, callable] = {
    "python": _parse_python_ast,
    "c": _parse_cpp_regex,
    "cpp": _parse_cpp_regex,
    "java": _parse_java_regex,
    "kotlin": _parse_kotlin_regex,
    "go": _parse_go_regex,
    "rust": _parse_rust_regex,
    "javascript": _parse_js_ts_regex,
    "typescript": _parse_js_ts_regex,
    "cmake": _parse_cmake_regex,
    "buck2": _parse_buck2_regex,
    "starlark": _parse_starlark_regex,
    "bxl": _parse_bxl_regex,
    "gradle_groovy": _parse_gradle_groovy_regex,
    "gradle_kts": _parse_gradle_kts_regex,
    "groovy": _parse_groovy_regex,
}


# ---------------------------------------------------------------------------
# Main dispatcher — layered extraction
# ---------------------------------------------------------------------------

def _detect_lang(file_path: str) -> str | None:
    """Detect language from file path."""
    p = Path(file_path)

    # Check exact filename first
    lang = _FILENAME_TO_LANG.get(p.name)
    if lang is not None:
        return lang if lang else None

    # Handle compound extensions: .gradle.kts, .test.ts, etc.
    suffixes = "".join(p.suffixes).lower()
    if suffixes.endswith(".gradle.kts"):
        return "gradle_kts"

    return _EXT_TO_LANG.get(p.suffix.lower())


def parse_imports(file_path: str, source: str) -> list[ImportInfo]:
    """
    Parse imports from source code using the best available strategy.

    Chain: tree-sitter → ast-grep → Python AST (Python only) → regex
    Returns on the first strategy that yields results.
    """
    lang = _detect_lang(file_path)
    if not lang:
        return []

    # Layer 1: tree-sitter (most precise)
    results = extract_imports_tree_sitter(source, lang)
    if results:
        return results

    # Layer 2: ast-grep (pattern-based AST)
    results = extract_imports_ast_grep(file_path, source, lang)
    if results:
        return results

    # Layer 3: regex / Python ast fallback
    parser = _REGEX_PARSERS.get(lang)
    if parser:
        return parser(source)

    return []


def parse_symbols(file_path: str, source: str) -> list[SymbolInfo]:
    """
    Extract symbol definitions from source code.

    Chain: tree-sitter → ctags
    """
    lang = _detect_lang(file_path)
    if not lang:
        return []

    # Layer 1: tree-sitter
    results = extract_symbols_tree_sitter(source, lang)
    if results:
        return results

    # Layer 2: ctags
    return extract_symbols_ctags(file_path, source)


def supported_extensions() -> set[str]:
    return set(_EXT_TO_LANG.keys())


def supported_filenames() -> set[str]:
    return set(_FILENAME_TO_LANG.keys())
