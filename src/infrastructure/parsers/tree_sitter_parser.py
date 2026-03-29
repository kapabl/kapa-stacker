"""Tree-sitter based import and symbol extraction."""

from __future__ import annotations

from src.domain.entity.import_ref import ImportRef
from src.domain.entity.symbol_def import SymbolDef

# ---------------------------------------------------------------------------
# Import queries per language
# ---------------------------------------------------------------------------

_IMPORT_QUERIES: dict[str, str] = {
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

# ---------------------------------------------------------------------------
# Symbol queries per language
# ---------------------------------------------------------------------------

_SYMBOL_QUERIES: dict[str, str] = {
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


_parser_cache: dict[str, tuple] = {}


def _get_parser(lang: str):
    """Get cached tree-sitter parser and language."""
    if lang in _parser_cache:
        return _parser_cache[lang]
    try:
        import warnings
        import tree_sitter_languages
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parser = tree_sitter_languages.get_parser(lang)
            ts_lang = tree_sitter_languages.get_language(lang)
        _parser_cache[lang] = (parser, ts_lang)
        return parser, ts_lang
    except (ImportError, Exception):
        return None


def _run_query(source: str, lang: str, query_text: str) -> list[str]:
    """Run a tree-sitter query and return matched text nodes."""
    cached = _get_parser(lang)
    if not cached:
        return []
    parser, ts_lang = cached

    tree = parser.parse(source.encode("utf-8"))
    try:
        query = ts_lang.query(query_text)
    except Exception:
        return []

    results: list[str] = []
    for node, tag in query.captures(tree.root_node):
        if not tag.startswith("_"):
            results.append(node.text.decode("utf-8").strip("'\""))
    return results


def _normalize(raw: str, lang: str) -> str:
    """Normalize a raw import string to a dot-separated module key."""
    cleaned = raw.strip("'\"<>")
    if lang in ("c", "cpp"):
        return cleaned.replace("/", ".").removesuffix(".h").removesuffix(".hpp").removesuffix(".hxx")
    if lang == "rust":
        return cleaned.replace("::", ".")
    if lang == "go":
        return cleaned
    return cleaned.replace("/", ".").replace("\\", ".")


def parse_imports(source: str, lang: str) -> list[ImportRef]:
    """Extract imports using tree-sitter AST queries."""
    query = _IMPORT_QUERIES.get(lang)
    if not query:
        return []

    texts = _run_query(source, lang, query)
    seen: set[str] = set()
    results: list[ImportRef] = []
    for text in texts:
        if text not in seen:
            seen.add(text)
            results.append(ImportRef(raw=text, module=_normalize(text, lang), kind="module"))
    return results


def extract_symbols(source: str, lang: str) -> list[SymbolDef]:
    """Extract defined symbols using tree-sitter AST queries."""
    query = _SYMBOL_QUERIES.get(lang)
    if not query:
        return []

    texts = _run_query(source, lang, query)
    seen: set[str] = set()
    results: list[SymbolDef] = []
    for text in texts:
        if text not in seen:
            seen.add(text)
            results.append(SymbolDef(name=text, kind="symbol"))
    return results
