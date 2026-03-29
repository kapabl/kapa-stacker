"""Extract function call sites from source code using tree-sitter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CallSite:
    """A function call found in source code."""
    caller_file: str
    caller_function: str
    callee_name: str
    line: int


# Tree-sitter queries for function calls per language
_CALL_QUERIES: dict[str, str] = {
    "python": """
        (call function: (identifier) @callee)
        (call function: (attribute attribute: (identifier) @callee))
    """,
    "java": """
        (method_invocation name: (identifier) @callee)
    """,
    "kotlin": """
        (call_expression (simple_identifier) @callee)
    """,
    "go": """
        (call_expression function: (identifier) @callee)
        (call_expression function: (selector_expression field: (field_identifier) @callee))
    """,
    "rust": """
        (call_expression function: (identifier) @callee)
        (call_expression function: (field_expression field: (field_identifier) @callee))
    """,
    "c": """
        (call_expression function: (identifier) @callee)
    """,
    "cpp": """
        (call_expression function: (identifier) @callee)
        (call_expression function: (field_expression field: (field_identifier) @callee))
    """,
    "javascript": """
        (call_expression function: (identifier) @callee)
        (call_expression function: (member_expression property: (property_identifier) @callee))
    """,
    "typescript": """
        (call_expression function: (identifier) @callee)
        (call_expression function: (member_expression property: (property_identifier) @callee))
    """,
}

# Tree-sitter queries for function definitions (to determine caller context)
_FUNCTION_QUERIES: dict[str, str] = {
    "python": """
        (function_definition name: (identifier) @func_name)
        (class_definition name: (identifier) @func_name)
    """,
    "java": """
        (method_declaration name: (identifier) @func_name)
        (class_declaration name: (identifier) @func_name)
    """,
    "go": """
        (function_declaration name: (identifier) @func_name)
        (method_declaration name: (field_identifier) @func_name)
    """,
    "rust": """
        (function_item name: (identifier) @func_name)
    """,
    "c": """
        (function_definition declarator: (function_declarator declarator: (identifier) @func_name))
    """,
    "cpp": """
        (function_definition declarator: (function_declarator declarator: (identifier) @func_name))
    """,
    "javascript": """
        (function_declaration name: (identifier) @func_name)
    """,
    "typescript": """
        (function_declaration name: (identifier) @func_name)
    """,
    "kotlin": """
        (function_declaration (simple_identifier) @func_name)
    """,
}


_parser_cache: dict[str, tuple] = {}


def _get_parser(language: str):
    """Get cached tree-sitter parser and language for a given language."""
    if language in _parser_cache:
        return _parser_cache[language]
    try:
        import warnings
        import tree_sitter_languages
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parser = tree_sitter_languages.get_parser(language)
            ts_lang = tree_sitter_languages.get_language(language)
        _parser_cache[language] = (parser, ts_lang)
        return parser, ts_lang
    except (ImportError, Exception):
        return None


def extract_calls(file_path: str, source: str, language: str) -> list[CallSite]:
    """Extract function call sites from source code."""
    call_query = _CALL_QUERIES.get(language)
    if not call_query:
        return []

    cached = _get_parser(language)
    if not cached:
        return []
    parser, ts_lang = cached

    tree = parser.parse(source.encode("utf-8"))

    function_ranges = _build_function_ranges(ts_lang, tree, language)

    try:
        query = ts_lang.query(call_query)
    except Exception:
        return []

    calls: list[CallSite] = []
    seen: set[tuple[str, str, int]] = set()

    for node, tag in query.captures(tree.root_node):
        if tag.startswith("_"):
            continue
        callee_name = node.text.decode("utf-8")
        call_line = node.start_point[0] + 1
        caller_function = _find_enclosing_function(call_line, function_ranges)

        key = (caller_function, callee_name, call_line)
        if key not in seen:
            seen.add(key)
            calls.append(CallSite(
                caller_file=file_path,
                caller_function=caller_function,
                callee_name=callee_name,
                line=call_line,
            ))

    return calls


def _build_function_ranges(
    ts_lang, tree, language: str,
) -> list[tuple[str, int, int]]:
    """Build (name, start_line, end_line) for all functions in the file."""
    func_query = _FUNCTION_QUERIES.get(language)
    if not func_query:
        return []

    try:
        query = ts_lang.query(func_query)
    except Exception:
        return []

    ranges: list[tuple[str, int, int]] = []
    for node, tag in query.captures(tree.root_node):
        if tag.startswith("_"):
            continue
        name = node.text.decode("utf-8")
        parent = node.parent
        if parent:
            start_line = parent.start_point[0] + 1
            end_line = parent.end_point[0] + 1
            ranges.append((name, start_line, end_line))

    return ranges


def _find_enclosing_function(
    line: int, function_ranges: list[tuple[str, int, int]],
) -> str:
    """Find which function contains the given line."""
    best_match = "<module>"
    best_size = float("inf")

    for name, start, end in function_ranges:
        if start <= line <= end:
            size = end - start
            if size < best_size:
                best_size = size
                best_match = name

    return best_match
