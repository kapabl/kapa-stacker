"""Regex-based import parsers for all supported languages."""

from __future__ import annotations

import re

from src.domain.value_object.import_ref import ImportRef

# ---------------------------------------------------------------------------
# C / C++
# ---------------------------------------------------------------------------

_CPP_RE = re.compile(r'^\s*#\s*include\s+[<"]([\w/.\-+]+)[>"]', re.MULTILINE)


def parse_cpp(source: str) -> list[ImportRef]:
    return [
        ImportRef(m.group(1), m.group(1).replace("/", ".").removesuffix(".h").removesuffix(".hpp"), "header")
        for m in _CPP_RE.finditer(source)
    ]


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

_JAVA_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)


def parse_java(source: str) -> list[ImportRef]:
    return [ImportRef(m.group(1), m.group(1), "package") for m in _JAVA_RE.finditer(source)]


# ---------------------------------------------------------------------------
# Kotlin
# ---------------------------------------------------------------------------

_KOTLIN_RE = re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)


def parse_kotlin(source: str) -> list[ImportRef]:
    return [ImportRef(m.group(1), m.group(1), "package") for m in _KOTLIN_RE.finditer(source)]


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

_GO_SINGLE_RE = re.compile(r'^\s*import\s+"([\w./\-]+)"', re.MULTILINE)
_GO_BLOCK_RE = re.compile(r"import\s*\((.*?)\)", re.DOTALL)
_GO_PATH_RE = re.compile(r'"([\w./\-]+)"')


def parse_go(source: str) -> list[ImportRef]:
    results, seen = [], set()
    for m in _GO_SINGLE_RE.finditer(source):
        p = m.group(1)
        if p not in seen:
            seen.add(p)
            results.append(ImportRef(p, p, "package"))
    for block in _GO_BLOCK_RE.finditer(source):
        for pm in _GO_PATH_RE.finditer(block.group(1)):
            p = pm.group(1)
            if p not in seen:
                seen.add(p)
                results.append(ImportRef(p, p, "package"))
    return results


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

_RUST_USE_RE = re.compile(r"^\s*(?:pub\s+)?use\s+([\w:]+)", re.MULTILINE)
_RUST_MOD_RE = re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)\s*[;{]", re.MULTILINE)
_RUST_EXTERN_RE = re.compile(r"^\s*extern\s+crate\s+(\w+)", re.MULTILINE)


def parse_rust(source: str) -> list[ImportRef]:
    results, seen = [], set()
    for m in _RUST_USE_RE.finditer(source):
        mod = m.group(1).replace("::", ".")
        if mod not in seen:
            seen.add(mod)
            results.append(ImportRef(m.group(1), mod, "module"))
    for m in _RUST_MOD_RE.finditer(source):
        mod = m.group(1)
        if mod not in seen:
            seen.add(mod)
            results.append(ImportRef(mod, mod, "module"))
    for m in _RUST_EXTERN_RE.finditer(source):
        c = m.group(1)
        if c not in seen:
            seen.add(c)
            results.append(ImportRef(c, c, "crate"))
    return results


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------

_JS_FROM_RE = re.compile(r"""(?:import|export)\s+.*?\s+from\s+['"]([\w@./\-]+)['"]""", re.MULTILINE)
_JS_SIDE_RE = re.compile(r"""import\s+['"]([\w@./\-]+)['"]""", re.MULTILINE)
_JS_REQ_RE = re.compile(r"""require\s*\(\s*['"]([\w@./\-]+)['"]\s*\)""", re.MULTILINE)


def parse_js_ts(source: str) -> list[ImportRef]:
    results, seen = [], set()
    for pat in [_JS_FROM_RE, _JS_SIDE_RE, _JS_REQ_RE]:
        for m in pat.finditer(source):
            mod = m.group(1)
            if mod not in seen:
                seen.add(mod)
                results.append(ImportRef(mod, mod, "module"))
    return results


# ---------------------------------------------------------------------------
# CMake
# ---------------------------------------------------------------------------

_CMAKE_INCLUDE_RE = re.compile(r"include\s*\(\s*(\S+)\s*\)", re.MULTILINE)
_CMAKE_PKG_RE = re.compile(r"find_package\s*\(\s*(\w+)", re.MULTILINE)
_CMAKE_SUBDIR_RE = re.compile(r"add_subdirectory\s*\(\s*([^\s)]+)", re.MULTILINE)
_CMAKE_LINK_RE = re.compile(r"target_link_libraries\s*\([^)]*\b(\w+::\w+)", re.MULTILINE)


def parse_cmake(source: str) -> list[ImportRef]:
    results, seen = [], set()
    for pat, kind in [
        (_CMAKE_INCLUDE_RE, "include"), (_CMAKE_PKG_RE, "package"),
        (_CMAKE_SUBDIR_RE, "subdirectory"), (_CMAKE_LINK_RE, "target"),
    ]:
        for m in pat.finditer(source):
            val = m.group(1).strip("\"'")
            if val not in seen:
                seen.add(val)
                results.append(ImportRef(val, val, kind))
    return results


# ---------------------------------------------------------------------------
# Buck2
# ---------------------------------------------------------------------------

_BUCK_LOAD_RE = re.compile(r'load\s*\(\s*"([^"]+)"', re.MULTILINE)
_BUCK_DEPS_RE = re.compile(r'deps\s*=\s*\[(.*?)\]', re.DOTALL)
_BUCK_STR_RE = re.compile(r'"([^"]+)"')


def parse_buck2(source: str) -> list[ImportRef]:
    results, seen = [], set()
    for m in _BUCK_LOAD_RE.finditer(source):
        t = m.group(1)
        if t not in seen:
            seen.add(t)
            results.append(ImportRef(t, t, "load"))
    for block in _BUCK_DEPS_RE.finditer(source):
        for sm in _BUCK_STR_RE.finditer(block.group(1)):
            d = sm.group(1)
            if d not in seen:
                seen.add(d)
                results.append(ImportRef(d, d, "target"))
    return results


# ---------------------------------------------------------------------------
# Starlark / Bazel
# ---------------------------------------------------------------------------

_STARLARK_LOAD_RE = re.compile(r'load\s*\(\s*"([^"]+)"', re.MULTILINE)


def parse_starlark(source: str) -> list[ImportRef]:
    return [ImportRef(m.group(1), m.group(1), "load") for m in _STARLARK_LOAD_RE.finditer(source)]


# ---------------------------------------------------------------------------
# BXL
# ---------------------------------------------------------------------------

_BXL_LOAD_RE = re.compile(r'load\s*\(\s*"([^"]+)"', re.MULTILINE)
_BXL_TARGET_RE = re.compile(r'"(//[\w/.\-]+(?::[\w.\-]+)?)"', re.MULTILINE)


def parse_bxl(source: str) -> list[ImportRef]:
    results, seen = [], set()
    for m in _BXL_LOAD_RE.finditer(source):
        t = m.group(1)
        if t not in seen:
            seen.add(t)
            results.append(ImportRef(t, t, "load"))
    for m in _BXL_TARGET_RE.finditer(source):
        t = m.group(1)
        if t not in seen:
            seen.add(t)
            results.append(ImportRef(t, t, "target"))
    return results


# ---------------------------------------------------------------------------
# Gradle Groovy
# ---------------------------------------------------------------------------

_GRADLE_DEP_RE = re.compile(
    r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|"
    r"testRuntimeOnly|classpath|annotationProcessor)\s*"
    r"[\(]?\s*['\"]([\w.:@\-/\${}]+)['\"]\s*[\)]?",
    re.MULTILINE,
)
_GRADLE_PROJECT_RE = re.compile(r"project\s*\(\s*['\"]([:.\w\-/]+)['\"]\s*\)", re.MULTILINE)
_GRADLE_APPLY_RE = re.compile(r"apply\s+from:\s*['\"]([\w./\-]+)['\"]", re.MULTILINE)
_GRADLE_PLUGIN_RE = re.compile(
    r"(?:apply\s+plugin:\s*['\"]([\w.\-]+)['\"]|id\s*\(?\s*['\"]([\w.\-]+)['\"]\s*\)?)",
    re.MULTILINE,
)


def parse_gradle_groovy(source: str) -> list[ImportRef]:
    results, seen = [], set()
    for m in _GRADLE_DEP_RE.finditer(source):
        dep = m.group(1)
        parts = dep.split(":")
        mod = ":".join(parts[:2]) if len(parts) >= 2 else dep
        if mod not in seen:
            seen.add(mod)
            results.append(ImportRef(dep, mod, "dependency"))
    for m in _GRADLE_PROJECT_RE.finditer(source):
        proj = m.group(1)
        if proj not in seen:
            seen.add(proj)
            results.append(ImportRef(proj, proj, "project"))
    for m in _GRADLE_APPLY_RE.finditer(source):
        p = m.group(1)
        if p not in seen:
            seen.add(p)
            results.append(ImportRef(p, p, "script"))
    for m in _GRADLE_PLUGIN_RE.finditer(source):
        plugin = m.group(1) or m.group(2)
        if plugin and plugin not in seen:
            seen.add(plugin)
            results.append(ImportRef(plugin, plugin, "plugin"))
    return results


# ---------------------------------------------------------------------------
# Gradle Kotlin DSL
# ---------------------------------------------------------------------------

_GK_DEP_RE = re.compile(
    r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|"
    r"testRuntimeOnly|classpath|annotationProcessor)\s*\(\s*"
    r"['\"]([\w.:@\-/]+)['\"]\s*\)",
    re.MULTILINE,
)
_GK_PROJECT_RE = re.compile(r"project\s*\(\s*['\"]([\w:.\-]+)['\"]\s*\)", re.MULTILINE)
_GK_PLUGIN_RE = re.compile(r"id\s*\(\s*['\"]([\w.\-]+)['\"]\s*\)", re.MULTILINE)
_GK_INCLUDE_RE = re.compile(r"include\s*\((.*?)\)", re.DOTALL)
_GK_STRING_RE = re.compile(r"['\"]([\w:.\-/]+)['\"]")


def parse_gradle_kts(source: str) -> list[ImportRef]:
    results, seen = [], set()
    for m in _GK_DEP_RE.finditer(source):
        dep = m.group(1)
        parts = dep.split(":")
        mod = ":".join(parts[:2]) if len(parts) >= 2 else dep
        if mod not in seen:
            seen.add(mod)
            results.append(ImportRef(dep, mod, "dependency"))
    for m in _GK_PROJECT_RE.finditer(source):
        proj = m.group(1)
        if proj not in seen:
            seen.add(proj)
            results.append(ImportRef(proj, proj, "project"))
    for m in _GK_PLUGIN_RE.finditer(source):
        plugin = m.group(1)
        if plugin not in seen:
            seen.add(plugin)
            results.append(ImportRef(plugin, plugin, "plugin"))
    for block in _GK_INCLUDE_RE.finditer(source):
        for sm in _GK_STRING_RE.finditer(block.group(1)):
            proj = sm.group(1)
            if proj not in seen:
                seen.add(proj)
                results.append(ImportRef(proj, proj, "project"))
    results.extend(parse_kotlin(source))
    return results


# ---------------------------------------------------------------------------
# Groovy
# ---------------------------------------------------------------------------

_GROOVY_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;?", re.MULTILINE)


def parse_groovy(source: str) -> list[ImportRef]:
    return [ImportRef(m.group(1), m.group(1), "package") for m in _GROOVY_RE.finditer(source)]
