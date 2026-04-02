use tree_sitter::{Language, Parser, Query, QueryCursor};

pub struct CallSite {
    pub caller_function: String,
    pub callee_name: String,
    pub line: usize,
}

/// Extract function call sites from source using tree-sitter.
pub fn extract_calls(source: &str, lang_name: &str) -> Vec<CallSite> {
    let language = match get_language(lang_name) {
        Some(l) => l,
        None => return Vec::new(),
    };

    let mut parser = Parser::new();
    if parser.set_language(&language).is_err() {
        return Vec::new();
    }

    let tree = match parser.parse(source, None) {
        Some(t) => t,
        None => return Vec::new(),
    };

    let call_query_str = match get_call_query(lang_name) {
        Some(q) => q,
        None => return Vec::new(),
    };

    let func_query_str = match get_function_query(lang_name) {
        Some(q) => q,
        None => return Vec::new(),
    };

    let call_query = match Query::new(&language, &call_query_str) {
        Ok(q) => q,
        Err(_) => return Vec::new(),
    };

    let func_query = match Query::new(&language, &func_query_str) {
        Ok(q) => q,
        Err(_) => return Vec::new(),
    };

    let source_bytes = source.as_bytes();
    let root = tree.root_node();

    // Build function ranges: (start_byte, end_byte, name)
    let mut functions: Vec<(usize, usize, String)> = Vec::new();
    let mut func_cursor = QueryCursor::new();
    let func_matches = func_cursor.matches(&func_query, root, source_bytes);
    for m in func_matches {
        if let Some(capture) = m.captures.first() {
            let node = capture.node;
            let name = node.utf8_text(source_bytes).unwrap_or("").to_string();
            let func_node = find_function_ancestor(node);
            functions.push((func_node.start_byte(), func_node.end_byte(), name));
        }
    }

    // Extract call sites
    let mut results = Vec::new();
    let mut call_cursor = QueryCursor::new();
    let call_matches = call_cursor.matches(&call_query, root, source_bytes);
    for m in call_matches {
        if let Some(capture) = m.captures.first() {
            let node = capture.node;
            let callee = node.utf8_text(source_bytes).unwrap_or("").to_string();
            let line = node.start_position().row + 1;
            let byte_pos = node.start_byte();

            let caller = functions
                .iter()
                .filter(|(start, end, _)| byte_pos >= *start && byte_pos <= *end)
                .last()
                .map(|(_, _, name)| name.clone())
                .unwrap_or_else(|| "<module>".to_string());

            results.push(CallSite {
                caller_function: caller,
                callee_name: callee,
                line,
            });
        }
    }

    results
}

fn get_language(lang: &str) -> Option<Language> {
    match lang {
        "cpp" | "c" => Some(tree_sitter_cpp::language()),
        "python" => Some(tree_sitter_python::language()),
        "java" | "kotlin" => Some(tree_sitter_java::language()),
        "go" => Some(tree_sitter_go::language()),
        "javascript" => Some(tree_sitter_javascript::language()),
        "typescript" => Some(tree_sitter_typescript::language_typescript()),
        "rust" => Some(tree_sitter_rust::language()),
        _ => None,
    }
}

fn get_call_query(lang: &str) -> Option<String> {
    let query = match lang {
        "cpp" | "c" => "(call_expression function: (identifier) @callee)
                        (call_expression function: (field_expression field: (field_identifier) @callee))",
        "python" => "(call function: (identifier) @callee)
                     (call function: (attribute attribute: (identifier) @callee))",
        "java" | "kotlin" => "(method_invocation name: (identifier) @callee)",
        "go" => "(call_expression function: (identifier) @callee)
                 (call_expression function: (selector_expression field: (field_identifier) @callee))",
        "javascript" | "typescript" => "(call_expression function: (identifier) @callee)
                                        (call_expression function: (member_expression property: (property_identifier) @callee))",
        "rust" => "(call_expression function: (identifier) @callee)
                   (call_expression function: (field_expression field: (field_identifier) @callee))",
        _ => return None,
    };
    Some(query.to_string())
}

fn get_function_query(lang: &str) -> Option<String> {
    let query = match lang {
        "cpp" | "c" => "(function_declarator declarator: (identifier) @name)
                        (function_declarator declarator: (qualified_identifier name: (identifier) @name))",
        "python" => "(function_definition name: (identifier) @name)",
        "java" | "kotlin" => "(method_declaration name: (identifier) @name)",
        "go" => "(function_declaration name: (identifier) @name)
                 (method_declaration name: (field_identifier) @name)",
        "javascript" | "typescript" => "(function_declaration name: (identifier) @name)
                                        (method_definition name: (property_identifier) @name)",
        "rust" => "(function_item name: (identifier) @name)",
        _ => return None,
    };
    Some(query.to_string())
}

fn find_function_ancestor(node: tree_sitter::Node) -> tree_sitter::Node {
    let mut current = node;
    while let Some(parent) = current.parent() {
        let kind = parent.kind();
        if kind.contains("function") || kind.contains("method") || kind == "translation_unit" {
            return parent;
        }
        current = parent;
    }
    current
}
