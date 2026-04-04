use starlark_syntax::syntax::ast::*;
use starlark_syntax::syntax::module::AstModuleFields;
use starlark_syntax::syntax::top_level_stmts::top_level_stmts;
use starlark_syntax::syntax::AstModule;
use starlark_syntax::dialect::Dialect;

#[derive(Debug, Clone)]
pub struct Buck2Target {
    pub name: String,
    pub rule: String,
    pub srcs: Vec<String>,
    pub deps: Vec<String>,
    pub exported_deps: Vec<String>,
    pub visibility: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct Buck2Load {
    pub module: String,
    pub symbols: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct Buck2File {
    pub targets: Vec<Buck2Target>,
    pub loads: Vec<Buck2Load>,
}

pub fn parse_targets_file(filename: &str, source: &str) -> Result<Buck2File, String> {
    let module = AstModule::parse(filename, source.to_string(), &Dialect::Extended)
        .map_err(|e| format!("Parse error in {}: {}", filename, e))?;

    let stmt = module.statement();
    let stmts = top_level_stmts(stmt);

    let mut targets = Vec::new();
    let mut loads = Vec::new();

    for top_stmt in stmts {
        match &top_stmt.node {
            StmtP::Load(load) => {
                let module_path = load.module.node.clone();
                let symbols: Vec<String> = load.args.iter()
                    .map(|arg| arg.their.node.clone())
                    .collect();
                loads.push(Buck2Load { module: module_path, symbols });
            }
            StmtP::Expression(expr) => {
                if let Some(target) = extract_target(&expr.node) {
                    targets.push(target);
                }
            }
            _ => {}
        }
    }

    Ok(Buck2File { targets, loads })
}

fn extract_target(expr: &ExprP<AstNoPayload>) -> Option<Buck2Target> {
    let ExprP::Call(callee, call_args) = expr else { return None };
    let ExprP::Identifier(ident) = &callee.node else { return None };

    let rule = ident.ident.clone();

    let mut name = String::new();
    let mut srcs = Vec::new();
    let mut deps = Vec::new();
    let mut exported_deps = Vec::new();
    let mut visibility = Vec::new();

    for arg in &call_args.args {
        let ArgumentP::Named(arg_name, arg_value) = &arg.node else { continue };
        match arg_name.node.as_str() {
            "name" => name = extract_string(&arg_value.node),
            "srcs" => srcs = extract_string_list(&arg_value.node),
            "deps" => deps = extract_string_list(&arg_value.node),
            "exported_deps" => exported_deps = extract_string_list(&arg_value.node),
            "visibility" => visibility = extract_string_list(&arg_value.node),
            _ => {}
        }
    }

    if name.is_empty() { return None; }

    Some(Buck2Target { name, rule, srcs, deps, exported_deps, visibility })
}

fn extract_string(expr: &ExprP<AstNoPayload>) -> String {
    match expr {
        ExprP::Literal(AstLiteral::String(s)) => s.node.clone(),
        _ => String::new(),
    }
}

fn extract_string_list(expr: &ExprP<AstNoPayload>) -> Vec<String> {
    match expr {
        ExprP::List(items) => {
            items.iter().filter_map(|item| {
                match &item.node {
                    ExprP::Literal(AstLiteral::String(s)) => Some(s.node.clone()),
                    ExprP::Call(callee, _args) => {
                        // Handle glob(), select(), etc. — store as placeholder
                        if let ExprP::Identifier(ident) = &callee.node {
                            Some(format!("{}(...)", ident.ident))
                        } else {
                            None
                        }
                    }
                    _ => None,
                }
            }).collect()
        }
        ExprP::Call(callee, _args) => {
            // Top-level glob() or select() as srcs value
            if let ExprP::Identifier(ident) = &callee.node {
                vec![format!("{}(...)", ident.ident)]
            } else {
                Vec::new()
            }
        }
        ExprP::Op(left, BinOp::Add, right) => {
            let mut result = extract_string_list(&left.node);
            result.extend(extract_string_list(&right.node));
            result
        }
        _ => Vec::new(),
    }
}

/// Resolve a target label relative to a package path.
/// ":foo" -> "//pkg:foo", "//other:bar" stays as-is.
pub fn resolve_label(dep: &str, package_path: &str) -> String {
    if dep.starts_with("//") || dep.starts_with("@") {
        dep.to_string()
    } else if dep.starts_with(":") {
        format!("//{}:{}", package_path, &dep[1..])
    } else {
        format!("//{}:{}", package_path, dep)
    }
}

/// Extract package path from a TARGETS file path.
/// "lib/foo/TARGETS" -> "lib/foo"
pub fn package_from_targets_path(targets_path: &str) -> String {
    std::path::Path::new(targets_path)
        .parent()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_cxx_library() {
        let source = r#"
cxx_library(
    name = "mylib",
    srcs = ["foo.cpp", "bar.cpp"],
    deps = [":utils", "//third_party:boost"],
    exported_deps = ["//common:base"],
    visibility = ["PUBLIC"],
)
"#;
        let result = parse_targets_file("TARGETS", source).unwrap();
        assert_eq!(result.targets.len(), 1);
        let target = &result.targets[0];
        assert_eq!(target.name, "mylib");
        assert_eq!(target.rule, "cxx_library");
        assert_eq!(target.srcs, vec!["foo.cpp", "bar.cpp"]);
        assert_eq!(target.deps, vec![":utils", "//third_party:boost"]);
        assert_eq!(target.exported_deps, vec!["//common:base"]);
        assert_eq!(target.visibility, vec!["PUBLIC"]);
    }

    #[test]
    fn test_parse_multiple_targets() {
        let source = r#"
cxx_library(
    name = "lib_a",
    srcs = ["a.cpp"],
    deps = [],
)

cxx_binary(
    name = "main",
    srcs = ["main.cpp"],
    deps = [":lib_a"],
)
"#;
        let result = parse_targets_file("TARGETS", source).unwrap();
        assert_eq!(result.targets.len(), 2);
        assert_eq!(result.targets[0].name, "lib_a");
        assert_eq!(result.targets[0].rule, "cxx_library");
        assert_eq!(result.targets[1].name, "main");
        assert_eq!(result.targets[1].rule, "cxx_binary");
        assert_eq!(result.targets[1].deps, vec![":lib_a"]);
    }

    #[test]
    fn test_parse_load() {
        let source = r#"
load("//tools:defs.bzl", "my_rule", "other_rule")
load("@prelude//cxx:cxx.bzl", "cxx_library")

my_rule(
    name = "custom",
    srcs = ["x.cpp"],
)
"#;
        let result = parse_targets_file("TARGETS", source).unwrap();
        assert_eq!(result.loads.len(), 2);
        assert_eq!(result.loads[0].module, "//tools:defs.bzl");
        assert_eq!(result.loads[0].symbols, vec!["my_rule", "other_rule"]);
        assert_eq!(result.loads[1].module, "@prelude//cxx:cxx.bzl");
        assert_eq!(result.targets.len(), 1);
        assert_eq!(result.targets[0].rule, "my_rule");
    }

    #[test]
    fn test_parse_glob_srcs() {
        let source = r#"
cxx_library(
    name = "all_cpp",
    srcs = glob(["**/*.cpp"]),
    deps = [],
)
"#;
        let result = parse_targets_file("TARGETS", source).unwrap();
        assert_eq!(result.targets[0].srcs, vec!["glob(...)"]);
    }

    #[test]
    fn test_parse_concat_srcs() {
        let source = r#"
cxx_library(
    name = "combined",
    srcs = ["a.cpp"] + ["b.cpp"],
    deps = [],
)
"#;
        let result = parse_targets_file("TARGETS", source).unwrap();
        assert_eq!(result.targets[0].srcs, vec!["a.cpp", "b.cpp"]);
    }

    #[test]
    fn test_resolve_label() {
        assert_eq!(resolve_label(":foo", "lib/bar"), "//lib/bar:foo");
        assert_eq!(resolve_label("//other:baz", "lib/bar"), "//other:baz");
        assert_eq!(resolve_label("@ext//lib:x", "lib/bar"), "@ext//lib:x");
    }

    #[test]
    fn test_package_from_path() {
        assert_eq!(package_from_targets_path("lib/foo/TARGETS"), "lib/foo");
        assert_eq!(package_from_targets_path("TARGETS"), "");
    }

    #[test]
    fn test_python_library() {
        let source = r#"
python_library(
    name = "pylib",
    srcs = ["main.py", "utils.py"],
    deps = ["//common:logging"],
)
"#;
        let result = parse_targets_file("TARGETS", source).unwrap();
        assert_eq!(result.targets[0].rule, "python_library");
        assert_eq!(result.targets[0].srcs, vec!["main.py", "utils.py"]);
    }

    #[test]
    fn test_empty_file() {
        let result = parse_targets_file("TARGETS", "").unwrap();
        assert!(result.targets.is_empty());
        assert!(result.loads.is_empty());
    }

    #[test]
    fn test_real_buck2_file() {
        let source = r#"
load("@fbsource//tools/build_defs:rust_library.bzl", "rust_library")

oncall("build_infra")

rust_library(
    name = "buck2_action_impl",
    srcs = glob(["src/**/*.rs"]),
    deps = [
        "fbsource//third-party/rust:async-trait",
        "fbsource//third-party/rust:futures",
        "//buck2/app/buck2_core:buck2_core",
        "//buck2/app/buck2_error:buck2_error",
        "//buck2/starlark-rust/starlark:starlark",
    ],
)
"#;
        let result = parse_targets_file("app/buck2_action_impl/BUCK", source).unwrap();
        assert_eq!(result.loads.len(), 1);
        assert_eq!(result.loads[0].module, "@fbsource//tools/build_defs:rust_library.bzl");
        assert_eq!(result.loads[0].symbols, vec!["rust_library"]);
        assert_eq!(result.targets.len(), 1);
        let target = &result.targets[0];
        assert_eq!(target.name, "buck2_action_impl");
        assert_eq!(target.rule, "rust_library");
        assert_eq!(target.srcs, vec!["glob(...)"]);
        assert_eq!(target.deps.len(), 5);
        assert!(target.deps[0].starts_with("fbsource//"));
        assert!(target.deps[3].starts_with("//buck2/"));
    }

    #[test]
    fn test_select_in_deps() {
        let source = r#"
rust_library(
    name = "io",
    srcs = glob(["src/**/*.rs"]),
    deps = [
        "//lib:core",
    ] + select({
        "DEFAULT": [],
        "ovr_config//os:linux": ["//linux:termios"],
    }),
)
"#;
        let result = parse_targets_file("TARGETS", source).unwrap();
        assert_eq!(result.targets.len(), 1);
        let deps = &result.targets[0].deps;
        assert!(deps.contains(&"//lib:core".to_string()));
        assert!(deps.contains(&"select(...)".to_string()));
    }

    #[test]
    fn test_no_name_skipped() {
        let source = r#"
cxx_library(
    srcs = ["foo.cpp"],
)
"#;
        let result = parse_targets_file("TARGETS", source).unwrap();
        assert!(result.targets.is_empty());
    }
}
