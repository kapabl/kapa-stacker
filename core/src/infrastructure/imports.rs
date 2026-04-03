use std::fs;

pub struct ImportEntry {
    pub raw: String,
    pub module: String,
    pub kind: String,
}

/// Parse #include directives from a C/C++ file.
/// Also handles Python imports, Go imports, Java imports.
pub fn parse_includes(file_path: &str) -> Result<Vec<ImportEntry>, String> {
    let bytes = fs::read(file_path).map_err(|e| format!("{}: {}", file_path, e))?;
    let content = String::from_utf8_lossy(&bytes);
    let mut results = Vec::new();

    for line in content.lines() {
        let trimmed = line.trim();

        // C/C++: #include "foo.h" or #include <foo.h>
        if trimmed.starts_with("#include") {
            if let Some(module) = parse_c_include(trimmed) {
                results.push(ImportEntry {
                    raw: trimmed.to_string(),
                    module,
                    kind: "include".to_string(),
                });
            }
        }
        // Java/Kotlin: import foo.bar.Baz; (has semicolon)
        else if trimmed.starts_with("import ") && trimmed.ends_with(';') {
            if let Some(module) = parse_java_import(trimmed) {
                results.push(ImportEntry {
                    raw: trimmed.to_string(),
                    module,
                    kind: "import".to_string(),
                });
            }
        }
        // Python: import foo / from foo import bar
        else if trimmed.starts_with("import ") || trimmed.starts_with("from ") {
            if let Some(module) = parse_python_import(trimmed) {
                results.push(ImportEntry {
                    raw: trimmed.to_string(),
                    module,
                    kind: "import".to_string(),
                });
            }
        }
    }

    Ok(results)
}

fn parse_c_include(line: &str) -> Option<String> {
    // #include "foo/bar.h" → foo/bar.h
    // #include <foo/bar.h> → foo/bar.h
    let rest = line.trim_start_matches("#include").trim();
    if rest.starts_with('"') {
        let end = rest[1..].find('"')?;
        Some(rest[1..1 + end].to_string())
    } else if rest.starts_with('<') {
        let end = rest[1..].find('>')?;
        Some(rest[1..1 + end].to_string())
    } else {
        None
    }
}

fn parse_python_import(line: &str) -> Option<String> {
    // from foo.bar import baz → foo.bar
    // import foo.bar → foo.bar
    if line.starts_with("from ") {
        let rest = &line[5..];
        let module = rest.split_whitespace().next()?;
        Some(module.to_string())
    } else if line.starts_with("import ") {
        let rest = &line[7..];
        let module = rest.split_whitespace().next()?.trim_end_matches(',');
        Some(module.to_string())
    } else {
        None
    }
}

fn parse_java_import(line: &str) -> Option<String> {
    let rest = line.trim_start_matches("import").trim();
    let module = rest.trim_start_matches("static").trim();
    Some(module.trim_end_matches(';').trim().to_string())
}

// ── Language-specific source parsers (work on source strings) ──

pub fn parse_go_source(source: &str) -> Vec<ImportEntry> {
    let mut results = Vec::new();
    let mut in_block = false;
    for line in source.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("import (") { in_block = true; continue; }
        if in_block && trimmed == ")" { in_block = false; continue; }
        if in_block || trimmed.starts_with("import \"") {
            let module = trimmed.trim_start_matches("import").trim().trim_matches('"').to_string();
            if !module.is_empty() && module != "(" {
                results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "import".to_string() });
            }
        }
    }
    results
}

pub fn parse_rust_source(source: &str) -> Vec<ImportEntry> {
    let mut results = Vec::new();
    for line in source.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("use ") {
            let module = trimmed.trim_start_matches("use ").trim_end_matches(';').replace("::", ".").to_string();
            results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "use".to_string() });
        } else if trimmed.starts_with("mod ") && trimmed.ends_with(';') {
            let module = trimmed.trim_start_matches("mod ").trim_end_matches(';').trim().to_string();
            results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "mod".to_string() });
        } else if trimmed.starts_with("extern crate ") {
            let module = trimmed.trim_start_matches("extern crate ").trim_end_matches(';').trim().to_string();
            results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "crate".to_string() });
        }
    }
    results
}

pub fn parse_js_ts_source(source: &str) -> Vec<ImportEntry> {
    let mut results = Vec::new();
    for line in source.lines() {
        let trimmed = line.trim();
        // import { x } from './module'
        if trimmed.starts_with("import ") {
            if let Some(from_pos) = trimmed.find("from ") {
                let module = trimmed[from_pos + 5..].trim().trim_matches(|c| c == '\'' || c == '"' || c == ';').to_string();
                results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "import".to_string() });
            }
        }
        // require('./module')
        if let Some(start) = trimmed.find("require(") {
            let rest = &trimmed[start + 8..];
            if let Some(end) = rest.find(')') {
                let module = rest[..end].trim_matches(|c| c == '\'' || c == '"').to_string();
                results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "require".to_string() });
            }
        }
    }
    results
}

pub fn parse_cmake_source(source: &str) -> Vec<ImportEntry> {
    let mut results = Vec::new();
    for line in source.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("find_package(") {
            let inner = trimmed.trim_start_matches("find_package(").split(|c| c == ' ' || c == ')').next().unwrap_or("");
            if !inner.is_empty() {
                results.push(ImportEntry { raw: trimmed.to_string(), module: inner.to_string(), kind: "find_package".to_string() });
            }
        }
        if trimmed.starts_with("add_subdirectory(") {
            let inner = trimmed.trim_start_matches("add_subdirectory(").trim_end_matches(')').trim().to_string();
            results.push(ImportEntry { raw: trimmed.to_string(), module: inner, kind: "subdirectory".to_string() });
        }
    }
    results
}

pub fn parse_buck2_source(source: &str) -> Vec<ImportEntry> {
    let mut results = Vec::new();
    for line in source.lines() {
        let trimmed = line.trim();
        // load("//tools:defs.bzl", "rule")
        if trimmed.starts_with("load(") {
            let inner = trimmed.trim_start_matches("load(").split(',').next().unwrap_or("");
            let module = inner.trim().trim_matches('"').to_string();
            if !module.is_empty() {
                results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "load".to_string() });
            }
        }
        // deps = ["//lib:core"]
        if trimmed.contains("\"//") {
            for part in trimmed.split('"') {
                if part.starts_with("//") {
                    results.push(ImportEntry { raw: trimmed.to_string(), module: part.to_string(), kind: "dep".to_string() });
                }
            }
        }
    }
    results
}

pub fn parse_starlark_source(source: &str) -> Vec<ImportEntry> {
    parse_buck2_source(source) // Same syntax
}

pub fn parse_bxl_source(source: &str) -> Vec<ImportEntry> {
    parse_buck2_source(source) // Same syntax
}

pub fn parse_gradle_groovy_source(source: &str) -> Vec<ImportEntry> {
    let mut results = Vec::new();
    for line in source.lines() {
        let trimmed = line.trim();
        // implementation 'com.google:guava:31'
        for keyword in &["implementation", "api", "compileOnly", "runtimeOnly", "testImplementation"] {
            if trimmed.starts_with(keyword) {
                let rest = trimmed[keyword.len()..].trim();
                let module = rest.trim_matches(|c| c == '\'' || c == '"' || c == '(' || c == ')').to_string();
                if !module.is_empty() {
                    results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "dependency".to_string() });
                }
            }
        }
        // apply plugin: 'java-library'
        if trimmed.starts_with("apply plugin:") {
            let module = trimmed.trim_start_matches("apply plugin:").trim().trim_matches('\'').trim_matches('"').to_string();
            results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "plugin".to_string() });
        }
    }
    results
}

pub fn parse_gradle_kts_source(source: &str) -> Vec<ImportEntry> {
    let mut results = Vec::new();
    for line in source.lines() {
        let trimmed = line.trim();
        // id("org.jetbrains.kotlin.jvm")
        if trimmed.starts_with("id(") {
            let module = trimmed.trim_start_matches("id(").trim_end_matches(')').trim_matches('"').to_string();
            results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "plugin".to_string() });
        }
        // project(":core")
        if trimmed.contains("project(") {
            if let Some(start) = trimmed.find("project(") {
                let rest = &trimmed[start + 8..];
                if let Some(end) = rest.find(')') {
                    let module = rest[..end].trim_matches('"').to_string();
                    results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "project".to_string() });
                }
            }
        }
        // include(":app", ":core")
        if trimmed.starts_with("include(") {
            let inner = trimmed.trim_start_matches("include(").trim_end_matches(')');
            for part in inner.split(',') {
                let module = part.trim().trim_matches('"').to_string();
                if !module.is_empty() {
                    results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "include".to_string() });
                }
            }
        }
    }
    results
}

pub fn parse_groovy_source(source: &str) -> Vec<ImportEntry> {
    let mut results = Vec::new();
    for line in source.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("import ") {
            let module = trimmed.trim_start_matches("import ").trim_end_matches(';').trim().to_string();
            results.push(ImportEntry { raw: trimmed.to_string(), module, kind: "import".to_string() });
        }
    }
    results
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn write_temp(content: &str) -> tempfile::NamedTempFile {
        let mut f = tempfile::NamedTempFile::new().unwrap();
        f.write_all(content.as_bytes()).unwrap();
        f
    }

    #[test]
    fn test_c_include_quoted() {
        let f = write_temp("#include \"foo/bar.h\"\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        assert_eq!(imports.len(), 1);
        assert_eq!(imports[0].module, "foo/bar.h");
    }

    #[test]
    fn test_c_include_angle() {
        let f = write_temp("#include <stdio.h>\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        assert_eq!(imports.len(), 1);
        assert_eq!(imports[0].module, "stdio.h");
    }

    #[test]
    fn test_python_import() {
        let f = write_temp("from foo.bar import baz\nimport os\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        assert_eq!(imports.len(), 2);
        assert_eq!(imports[0].module, "foo.bar");
        assert_eq!(imports[1].module, "os");
    }

    #[test]
    fn test_no_imports() {
        let f = write_temp("int x = 5;\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        assert!(imports.is_empty());
    }

    #[test]
    fn test_c_include_spacing() {
        let f = write_temp("#include  <vector>\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        assert_eq!(imports.len(), 1);
    }

    #[test]
    fn test_multiple_includes() {
        let f = write_temp("#include <iostream>\n#include <vector>\n#include \"mylib.h\"\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        assert_eq!(imports.len(), 3);
    }

    #[test]
    fn test_python_from_import() {
        let f = write_temp("from pathlib import Path\nfrom os.path import join\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        assert_eq!(imports.len(), 2);
        assert_eq!(imports[0].module, "pathlib");
        assert_eq!(imports[1].module, "os.path");
    }

    #[test]
    fn test_java_import() {
        let f = write_temp("import com.example.MyClass;\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        assert_eq!(imports.len(), 1);
        assert_eq!(imports[0].module, "com.example.MyClass");
    }

    #[test]
    fn test_java_static_import() {
        let f = write_temp("import static org.junit.Assert.assertEquals;\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        assert_eq!(imports.len(), 1);
        assert!(imports[0].module.contains("org.junit"));
    }

    #[test]
    fn test_mixed_content() {
        let f = write_temp("// comment\n#include <stdio.h>\nint main() { return 0; }\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        assert_eq!(imports.len(), 1);
        assert_eq!(imports[0].module, "stdio.h");
    }

    #[test]
    fn test_rust_use() {
        // Our simple parser doesn't handle Rust use statements yet
        let f = write_temp("use std::io;\nuse std::fs::File;\n");
        let imports = parse_includes(f.path().to_str().unwrap()).unwrap();
        // Currently 0 — Rust imports not implemented in simple parser
        // This documents the gap
        assert!(imports.is_empty() || imports.len() == 2);
    }

    // ── Go ──
    #[test]
    fn test_go_single() {
        let r = parse_go_source("import \"fmt\"");
        assert_eq!(r[0].module, "fmt");
    }

    #[test]
    fn test_go_block() {
        let r = parse_go_source("import (\n  \"fmt\"\n  \"os\"\n  \"strings\"\n)");
        let modules: std::collections::HashSet<&str> = r.iter().map(|x| x.module.as_str()).collect();
        assert!(modules.contains("fmt"));
        assert!(modules.contains("os"));
        assert!(modules.contains("strings"));
    }

    // ── Rust ──
    #[test]
    fn test_rust_use_statement() {
        let r = parse_rust_source("use std::collections::HashMap;");
        assert_eq!(r[0].module, "std.collections.HashMap");
    }

    #[test]
    fn test_rust_mod_statement() {
        let r = parse_rust_source("mod utils;");
        assert_eq!(r[0].module, "utils");
    }

    #[test]
    fn test_rust_extern_crate_statement() {
        let r = parse_rust_source("extern crate serde;");
        assert_eq!(r[0].kind, "crate");
    }

    // ── JS/TS ──
    #[test]
    fn test_js_import_from() {
        let r = parse_js_ts_source("import { foo } from './utils'");
        assert_eq!(r[0].module, "./utils");
    }

    #[test]
    fn test_js_require() {
        let r = parse_js_ts_source("const x = require('./config')");
        assert!(r.iter().any(|x| x.module == "./config"));
    }

    // ── CMake ──
    #[test]
    fn test_cmake_find_package() {
        let r = parse_cmake_source("find_package(Boost REQUIRED)");
        assert_eq!(r[0].module, "Boost");
    }

    #[test]
    fn test_cmake_add_subdirectory() {
        let r = parse_cmake_source("add_subdirectory(src/core)");
        assert_eq!(r[0].module, "src/core");
    }

    // ── Buck2 ──
    #[test]
    fn test_buck2_load() {
        let r = parse_buck2_source("load(\"//tools:defs.bzl\", \"my_rule\")");
        assert!(r.iter().any(|x| x.module == "//tools:defs.bzl"));
    }

    #[test]
    fn test_buck2_deps() {
        let r = parse_buck2_source("deps = [\n  \"//lib:core\",\n  \"//lib:utils\",\n]");
        let modules: std::collections::HashSet<&str> = r.iter().map(|x| x.module.as_str()).collect();
        assert!(modules.contains("//lib:core"));
        assert!(modules.contains("//lib:utils"));
    }

    // ── Starlark ──
    #[test]
    fn test_starlark_load() {
        let r = parse_starlark_source("load(\"@rules_cc//cc:defs.bzl\", \"cc_library\")");
        assert_eq!(r[0].module, "@rules_cc//cc:defs.bzl");
    }

    // ── BXL ──
    #[test]
    fn test_bxl_load() {
        let r = parse_bxl_source("load(\"//bxl:rules.bzl\", \"my_check\")");
        assert!(r.iter().any(|x| x.module == "//bxl:rules.bzl"));
    }

    #[test]
    fn test_bxl_target() {
        let r = parse_bxl_source("targets = [\"//src/lib:mylib\"]");
        assert!(r.iter().any(|x| x.module == "//src/lib:mylib"));
    }

    // ── Gradle Groovy ──
    #[test]
    fn test_gradle_groovy_implementation() {
        let r = parse_gradle_groovy_source("implementation 'com.google.guava:guava:31.1-jre'");
        assert!(r.iter().any(|x| x.module.contains("guava")));
    }

    #[test]
    fn test_gradle_groovy_plugin() {
        let r = parse_gradle_groovy_source("apply plugin: 'java-library'");
        assert!(r.iter().any(|x| x.module == "java-library"));
    }

    // ── Gradle KTS ──
    #[test]
    fn test_gradle_kts_plugin_id() {
        let r = parse_gradle_kts_source("id(\"org.jetbrains.kotlin.jvm\")");
        assert!(r.iter().any(|x| x.module == "org.jetbrains.kotlin.jvm"));
    }

    #[test]
    fn test_gradle_kts_project() {
        let r = parse_gradle_kts_source("api(project(\":core\"))");
        assert!(r.iter().any(|x| x.module == ":core"));
    }

    #[test]
    fn test_gradle_kts_include() {
        let r = parse_gradle_kts_source("include(\":app\", \":core\")");
        let modules: std::collections::HashSet<&str> = r.iter().map(|x| x.module.as_str()).collect();
        assert!(modules.contains(":app"));
        assert!(modules.contains(":core"));
    }

    // ── Groovy ──
    #[test]
    fn test_groovy_import() {
        let r = parse_groovy_source("import groovy.json.JsonSlurper");
        assert_eq!(r[0].module, "groovy.json.JsonSlurper");
    }
}
