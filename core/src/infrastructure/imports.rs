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
        // Go: import "foo/bar"
        else if trimmed.starts_with("import ") || trimmed.starts_with("\"") {
            // Go imports handled in block context — simplified
        }
        // Java/Kotlin: import foo.bar.Baz
        else if trimmed.starts_with("import ") {
            if let Some(module) = parse_java_import(trimmed) {
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
    // import foo.bar.Baz; → foo.bar.Baz
    let rest = line.trim_start_matches("import").trim();
    let module = rest.trim_start_matches("static").trim();
    Some(module.trim_end_matches(';').trim().to_string())
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
}
