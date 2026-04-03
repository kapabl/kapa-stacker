use crate::infrastructure::sqlite::Database;
use crate::infrastructure::{ctags, hasher, walker};
use crate::infrastructure::tree_sitter as ts;
use rusqlite::params;
use std::time::Instant;

/// Full index: walk repo, parse each file, build edges.
pub fn index_repo(db: &Database, root: &str) -> Result<(), String> {
    let start = Instant::now();
    let files = walker::find_source_files(root)?;
    let total = files.len();
    eprintln!("  \x1b[36mIndexing {} files...\x1b[0m", total);

    let root_prefix = format!(
        "{}/",
        std::fs::canonicalize(root).map_err(|e| e.to_string())?.display()
    );

    db.with_conn(|conn| -> Result<(), String> {
        conn.execute_batch("BEGIN").map_err(|e| e.to_string())?;

        let mut symbol_count: usize = 0;
        let mut import_count: usize = 0;
        let mut call_count: usize = 0;

        for (idx, file_path) in files.iter().enumerate() {
            if idx % 100 == 0 && idx > 0 {
                let elapsed = start.elapsed().as_secs();
                let pct = idx * 100 / total;
                eprint!("\r\x1b[2K  \x1b[36m{}/{} ({}%) {}s\x1b[0m", idx, total, pct, elapsed);
            }

            let abs = std::fs::canonicalize(file_path)
                .unwrap_or_else(|_| std::path::PathBuf::from(file_path));
            let relative = abs
                .to_string_lossy()
                .strip_prefix(&root_prefix)
                .unwrap_or(&abs.to_string_lossy())
                .to_string();

            let hash = hasher::hash_file(file_path)?;

            conn.execute(
                "INSERT OR REPLACE INTO files (path, content_hash) VALUES (?, ?)",
                params![relative, hash],
            )
            .map_err(|e| e.to_string())?;

            let symbols = ctags::parse_file(file_path)?;
            for sym in &symbols {
                conn.execute(
                    "INSERT INTO symbols (file_path, name, kind, line, scope) VALUES (?, ?, ?, ?, ?)",
                    params![relative, sym.name, sym.kind, sym.line, sym.scope],
                )
                .map_err(|e| e.to_string())?;
                symbol_count += 1;
            }

            let file_imports = crate::infrastructure::imports::parse_includes(file_path)?;
            for imp in &file_imports {
                conn.execute(
                    "INSERT INTO imports (file_path, raw, module, kind) VALUES (?, ?, ?, ?)",
                    params![relative, imp.raw, imp.module, imp.kind],
                )
                .map_err(|e| e.to_string())?;
                import_count += 1;
            }

            let lang = detect_language(file_path);
            if let Some(lang_name) = lang {
                let source =
                    String::from_utf8_lossy(&std::fs::read(file_path).unwrap_or_default())
                        .to_string();
                let call_sites = ts::extract_calls(&source, lang_name);
                for call in &call_sites {
                    conn.execute(
                        "INSERT INTO calls (caller_file, caller_function, callee_file, callee_function, line)
                         VALUES (?, ?, '', ?, ?)",
                        params![relative, call.caller_function, call.callee_name, call.line],
                    )
                    .map_err(|e| e.to_string())?;
                    call_count += 1;
                }
            }
        }

        conn.execute_batch("COMMIT").map_err(|e| e.to_string())?;
        let elapsed = start.elapsed().as_secs_f32();
        eprintln!(
            "\r\x1b[2K  \x1b[32m✓\x1b[0m {} symbols, {} imports, {} calls ({:.1}s)",
            symbol_count, import_count, call_count, elapsed
        );
        Ok(())
    })?;

    let edge_start = Instant::now();
    let edge_count = build_edges(db)?;
    eprintln!(
        "  \x1b[32m✓\x1b[0m {} edges ({:.1}s)",
        edge_count,
        edge_start.elapsed().as_secs_f32()
    );
    // Complexity via lizard
    let cx_start = Instant::now();
    let cx_count = compute_complexity(db, root, &root_prefix)?;
    eprintln!(
        "  \x1b[32m✓\x1b[0m {} files with complexity ({:.1}s)",
        cx_count,
        cx_start.elapsed().as_secs_f32()
    );

    eprintln!(
        "  \x1b[32m✓\x1b[0m Index complete in {:.1}s",
        start.elapsed().as_secs_f32()
    );
    Ok(())
}

fn compute_complexity(db: &Database, root: &str, _root_prefix: &str) -> Result<usize, String> {
    use crate::infrastructure::complexity;

    let results = complexity::analyze_directory(root);
    let mut count = 0;

    db.with_conn(|conn| -> Result<(), String> {
        conn.execute_batch("BEGIN").map_err(|e| e.to_string())?;
        for fc in &results {
            let updated = conn.execute(
                "UPDATE files SET complexity = ?, lines = ? WHERE path = ?",
                params![fc.complexity, fc.lines, fc.path],
            ).map_err(|e| e.to_string())?;
            if updated > 0 {
                count += 1;
            }
        }
        conn.execute_batch("COMMIT").map_err(|e| e.to_string())?;
        Ok(())
    })?;
    Ok(count)
}

fn detect_language(file_path: &str) -> Option<&str> {
    let ext = std::path::Path::new(file_path).extension().and_then(|e| e.to_str())?;
    match ext {
        "c" | "h" => Some("c"),
        "cpp" | "cc" | "cxx" | "hpp" | "hxx" => Some("cpp"),
        "py" | "pyi" => Some("python"),
        "java" => Some("java"),
        "go" => Some("go"),
        "js" | "jsx" | "mjs" | "cjs" => Some("javascript"),
        "ts" | "tsx" => Some("typescript"),
        "rs" => Some("rust"),
        "kt" | "kts" => Some("kotlin"),
        _ => None,
    }
}

fn build_edges(db: &Database) -> Result<usize, String> {
    db.with_conn(|conn| -> Result<usize, String> {
        let mut file_index: std::collections::HashMap<String, Vec<String>> =
            std::collections::HashMap::new();
        let mut stmt = conn.prepare("SELECT path FROM files").map_err(|e| e.to_string())?;
        let paths: Vec<String> = stmt
            .query_map([], |row| row.get(0))
            .map_err(|e| e.to_string())?
            .filter_map(|r| r.ok())
            .collect();

        for path in &paths {
            if let Some(basename) = std::path::Path::new(path).file_name() {
                file_index
                    .entry(basename.to_string_lossy().to_string())
                    .or_default()
                    .push(path.clone());
            }
        }

        let mut import_stmt = conn
            .prepare("SELECT file_path, module FROM imports")
            .map_err(|e| e.to_string())?;
        let import_rows: Vec<(String, String)> = import_stmt
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
            .map_err(|e| e.to_string())?
            .filter_map(|r| r.ok())
            .collect();

        conn.execute_batch("BEGIN").map_err(|e| e.to_string())?;
        let mut edge_count = 0;
        for (source, module) in &import_rows {
            let basename = std::path::Path::new(module)
                .file_name()
                .map(|f| f.to_string_lossy().to_string())
                .unwrap_or_default();
            if let Some(targets) = file_index.get(&basename) {
                for target in targets {
                    if target != source {
                        conn.execute(
                            "INSERT OR IGNORE INTO edges (source, target, kind) VALUES (?, ?, 'import')",
                            params![source, target],
                        )
                        .map_err(|e| e.to_string())?;
                        edge_count += 1;
                    }
                }
            }
        }
        conn.execute_batch("COMMIT").map_err(|e| e.to_string())?;
        Ok(edge_count)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detect_python() {
        assert_eq!(detect_language("src/main.py"), Some("python"));
        assert_eq!(detect_language("types.pyi"), Some("python"));
    }

    #[test]
    fn test_detect_cpp() {
        assert_eq!(detect_language("src/main.cpp"), Some("cpp"));
        assert_eq!(detect_language("include/header.hpp"), Some("cpp"));
    }

    #[test]
    fn test_detect_c() {
        assert_eq!(detect_language("src/main.c"), Some("c"));
        assert_eq!(detect_language("include/header.h"), Some("c"));
    }

    #[test]
    fn test_detect_java() {
        assert_eq!(detect_language("src/Main.java"), Some("java"));
    }

    #[test]
    fn test_detect_kotlin() {
        assert_eq!(detect_language("src/App.kt"), Some("kotlin"));
        assert_eq!(detect_language("build.gradle.kts"), Some("kotlin"));
    }

    #[test]
    fn test_detect_go() {
        assert_eq!(detect_language("cmd/server.go"), Some("go"));
    }

    #[test]
    fn test_detect_rust() {
        assert_eq!(detect_language("src/lib.rs"), Some("rust"));
    }

    #[test]
    fn test_detect_typescript() {
        assert_eq!(detect_language("src/app.ts"), Some("typescript"));
        assert_eq!(detect_language("src/Button.tsx"), Some("typescript"));
    }

    #[test]
    fn test_detect_javascript() {
        assert_eq!(detect_language("src/index.js"), Some("javascript"));
        assert_eq!(detect_language("src/App.jsx"), Some("javascript"));
    }

    #[test]
    fn test_detect_unknown() {
        assert_eq!(detect_language("README.md"), None);
        assert_eq!(detect_language("Makefile"), None);
        assert_eq!(detect_language("Dockerfile"), None);
    }
}
