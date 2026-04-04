use rusqlite::{params, Connection};
use std::path::Path;
use std::sync::Mutex;

use crate::domain::model::*;

pub struct Database {
    conn: Mutex<Connection>,
}

impl Database {
    pub fn open(path: &Path) -> rusqlite::Result<Self> {
        let conn = Connection::open(path)?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=NORMAL;
             PRAGMA cache_size=-64000;
             PRAGMA busy_timeout=5000;",
        )?;
        create_tables(&conn)?;
        Ok(Database { conn: Mutex::new(conn) })
    }

    pub fn with_conn<F, T>(&self, func: F) -> T
    where
        F: FnOnce(&Connection) -> T,
    {
        let conn = self.conn.lock().unwrap();
        func(&conn)
    }
}

fn create_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "
        CREATE TABLE IF NOT EXISTS files (
            path         TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            language     TEXT,
            lines        INTEGER,
            complexity   INTEGER
        );
        CREATE TABLE IF NOT EXISTS symbols (
            file_path TEXT NOT NULL,
            name      TEXT NOT NULL,
            kind      TEXT,
            line      INTEGER,
            scope     TEXT
        );
        CREATE TABLE IF NOT EXISTS imports (
            file_path TEXT NOT NULL,
            raw       TEXT,
            module    TEXT,
            kind      TEXT
        );
        CREATE TABLE IF NOT EXISTS edges (
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            kind   TEXT NOT NULL,
            PRIMARY KEY (source, target, kind)
        );
        CREATE TABLE IF NOT EXISTS calls (
            caller_file     TEXT NOT NULL,
            caller_function TEXT NOT NULL,
            callee_file     TEXT NOT NULL,
            callee_function TEXT NOT NULL,
            line            INTEGER
        );
        CREATE TABLE IF NOT EXISTS entry_cache (
            content_hash TEXT PRIMARY KEY,
            symbols      BLOB,
            imports      BLOB,
            calls        BLOB
        );
        CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
        CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);
        CREATE INDEX IF NOT EXISTS idx_symbols_scope ON symbols(scope);
        CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_function, callee_file);
        CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_function, caller_file);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
        CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);
        CREATE TABLE IF NOT EXISTS targets (
            path       TEXT NOT NULL,
            name       TEXT NOT NULL,
            rule       TEXT NOT NULL,
            srcs       TEXT,
            deps       TEXT,
            exported_deps TEXT,
            visibility TEXT,
            PRIMARY KEY (path, name)
        );
        CREATE TABLE IF NOT EXISTS target_edges (
            source_label TEXT NOT NULL,
            dep_label    TEXT NOT NULL,
            kind         TEXT NOT NULL,
            PRIMARY KEY (source_label, dep_label, kind)
        );
        CREATE INDEX IF NOT EXISTS idx_targets_name ON targets(name);
        CREATE INDEX IF NOT EXISTS idx_targets_path ON targets(path);
        CREATE INDEX IF NOT EXISTS idx_target_edges_dep ON target_edges(dep_label);
        CREATE INDEX IF NOT EXISTS idx_target_edges_source ON target_edges(source_label);
        ",
    )
}

// ── Queries ──

pub fn lookup(conn: &Connection, symbol: &str) -> rusqlite::Result<Vec<SymbolDef>> {
    let mut stmt = conn.prepare(
        "SELECT name, kind, file_path, line, scope FROM symbols WHERE name = ?",
    )?;
    let rows = stmt.query_map(params![symbol], |row| {
        let name: String = row.get(0)?;
        let kind: String = row.get(1)?;
        let file: String = row.get(2)?;
        let line: i64 = row.get(3)?;
        let scope: String = row.get(4)?;
        let fqn = if scope.is_empty() { name.clone() } else { format!("{}::{}", scope, name) };
        Ok(SymbolDef { fqn, name, kind, file, line, scope })
    })?;
    rows.collect()
}

pub fn find_scoped_definition(
    conn: &Connection, name: &str, scope: &str,
) -> rusqlite::Result<Option<(String, i64)>> {
    let rows: Vec<(String, i64)> = if scope.is_empty() {
        conn.prepare(
            "SELECT file_path, line FROM symbols WHERE name = ? AND kind IN ('function', 'method', 'prototype', 'class', 'struct')",
        )?.query_map(params![name], |row| Ok((row.get(0)?, row.get(1)?)))?
            .collect::<Result<Vec<_>, _>>()?
    } else {
        conn.prepare(
            "SELECT file_path, line FROM symbols WHERE name = ? AND scope = ?",
        )?.query_map(params![name, scope], |row| Ok((row.get(0)?, row.get(1)?)))?
            .collect::<Result<Vec<_>, _>>()?
    };
    let header = rows.iter().find(|(f, _)| f.ends_with(".h") || f.ends_with(".hpp"));
    if let Some(result) = header {
        return Ok(Some(result.clone()));
    }
    Ok(rows.into_iter().next())
}

pub fn get_callers(conn: &Connection, function: &str, _file: &str) -> rusqlite::Result<Vec<CallerInfo>> {
    let mut stmt = conn.prepare(
        "SELECT DISTINCT caller_function, caller_file, line FROM calls WHERE callee_function = ?",
    )?;
    let rows = stmt.query_map(params![function], |row| {
        Ok(CallerInfo { function: row.get(0)?, file: row.get(1)?, line: row.get(2)? })
    })?;
    rows.collect()
}

pub fn get_callees(conn: &Connection, function: &str, file: &str) -> rusqlite::Result<Vec<CalleeInfo>> {
    let mut stmt = conn.prepare(
        "SELECT callee_function, callee_file, line FROM calls WHERE caller_function = ? AND caller_file = ?",
    )?;
    let rows = stmt.query_map(params![function, file], |row| {
        Ok(CalleeInfo { function: row.get(0)?, file: row.get(1)?, line: row.get(2)? })
    })?;
    rows.collect()
}

pub fn symbols_for_file(conn: &Connection, file_path: &str) -> rusqlite::Result<Vec<SymbolInfo>> {
    let mut stmt = conn.prepare(
        "SELECT name, kind, line, scope FROM symbols WHERE file_path = ? ORDER BY line",
    )?;
    let rows = stmt.query_map(params![file_path], |row| {
        Ok(SymbolInfo { name: row.get(0)?, kind: row.get(1)?, line: row.get(2)?, scope: row.get(3)? })
    })?;
    rows.collect()
}

pub fn get_dependents(conn: &Connection, file_path: &str) -> rusqlite::Result<Vec<String>> {
    let mut stmt = conn.prepare("SELECT source FROM edges WHERE target = ?")?;
    let rows = stmt.query_map(params![file_path], |row| row.get(0))?;
    rows.collect()
}

pub fn get_dependencies(conn: &Connection, file_path: &str) -> rusqlite::Result<Vec<String>> {
    let mut stmt = conn.prepare("SELECT target FROM edges WHERE source = ?")?;
    let rows = stmt.query_map(params![file_path], |row| row.get(0))?;
    rows.collect()
}

pub fn find_impact(conn: &Connection, target: &str, max_depth: usize) -> rusqlite::Result<ImpactResult> {
    let direct = get_dependents(conn, target)?;
    let mut visited: std::collections::HashSet<String> = std::collections::HashSet::new();
    visited.insert(target.to_string());
    for d in &direct { visited.insert(d.clone()); }

    let mut queue: std::collections::VecDeque<(String, usize)> = std::collections::VecDeque::new();
    for d in &direct { queue.push_back((d.clone(), 1)); }

    let mut transitive = Vec::new();
    while let Some((current, depth)) = queue.pop_front() {
        if depth >= max_depth { continue; }
        for dep in get_dependents(conn, &current)? {
            if !visited.contains(&dep) {
                visited.insert(dep.clone());
                transitive.push(dep.clone());
                queue.push_back((dep, depth + 1));
            }
        }
    }
    Ok(ImpactResult { target: target.to_string(), direct, transitive })
}

pub fn find_deps(conn: &Connection, target: &str, max_depth: usize) -> rusqlite::Result<Vec<String>> {
    let mut visited: std::collections::HashSet<String> = std::collections::HashSet::new();
    visited.insert(target.to_string());
    let mut queue: std::collections::VecDeque<(String, usize)> = std::collections::VecDeque::new();
    queue.push_back((target.to_string(), 0));
    let mut result = Vec::new();
    while let Some((current, depth)) = queue.pop_front() {
        if depth >= max_depth { continue; }
        for dep in get_dependencies(conn, &current)? {
            if !visited.contains(&dep) {
                visited.insert(dep.clone());
                result.push(dep.clone());
                queue.push_back((dep, depth + 1));
            }
        }
    }
    Ok(result)
}

pub fn find_hotspots(conn: &Connection, limit: usize) -> rusqlite::Result<Vec<HotspotEntry>> {
    let mut stmt = conn.prepare(
        "SELECT f.path, f.complexity,
                (SELECT COUNT(*) FROM edges e WHERE e.target = f.path) AS dep_count
         FROM files f WHERE f.complexity > 0
         ORDER BY f.complexity * (1 + (SELECT COUNT(*) FROM edges e WHERE e.target = f.path)) DESC
         LIMIT ?",
    )?;
    let rows = stmt.query_map(params![limit as i64], |row| {
        let path: String = row.get(0)?;
        let complexity: i64 = row.get(1)?;
        let dependents: i64 = row.get(2)?;
        Ok(HotspotEntry { path, complexity, dependents, score: complexity as f64 * (1.0 + dependents as f64) })
    })?;
    rows.collect()
}

pub fn find_call_impact(conn: &Connection, symbol: &str, file: &str, max_depth: usize) -> rusqlite::Result<Vec<CallerInfo>> {
    let mut visited: std::collections::HashSet<(String, String)> = std::collections::HashSet::new();
    visited.insert((symbol.to_string(), file.to_string()));
    let mut queue: std::collections::VecDeque<(String, String, usize)> = std::collections::VecDeque::new();
    let mut result = Vec::new();

    let direct = get_callers(conn, symbol, file)?;
    for caller in &direct {
        result.push(caller.clone());
        let key = (caller.function.clone(), caller.file.clone());
        if !visited.contains(&key) { visited.insert(key.clone()); queue.push_back((key.0, key.1, 1)); }
    }
    while let Some((func, file, depth)) = queue.pop_front() {
        if depth >= max_depth { continue; }
        for caller in get_callers(conn, &func, &file)? {
            result.push(caller.clone());
            let key = (caller.function.clone(), caller.file.clone());
            if !visited.contains(&key) { visited.insert(key.clone()); queue.push_back((key.0, key.1, depth + 1)); }
        }
    }
    Ok(result)
}

pub fn trace_path(conn: &Connection, src_fn: &str, _src_file: &str, tgt_fn: &str, _tgt_file: &str) -> rusqlite::Result<Vec<CallerInfo>> {
    use std::collections::{HashMap, HashSet, VecDeque};
    let mut visited: HashSet<String> = HashSet::new();
    let mut parent: HashMap<String, (String, String, i64)> = HashMap::new();
    let mut queue: VecDeque<String> = VecDeque::new();
    queue.push_back(src_fn.to_string());
    visited.insert(src_fn.to_string());

    let mut stmt = conn.prepare(
        "SELECT DISTINCT callee_function, caller_file, line FROM calls WHERE caller_function = ?"
    )?;
    while let Some(current) = queue.pop_front() {
        if current == tgt_fn {
            let mut path = Vec::new();
            let mut node = current;
            while let Some((prev_fn, file, line)) = parent.get(&node) {
                path.push(CallerInfo { function: node.clone(), file: file.clone(), line: *line });
                node = prev_fn.clone();
            }
            path.reverse();
            return Ok(path);
        }
        let callees: Vec<(String, String, i64)> = stmt
            .query_map(params![current], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))?
            .collect::<Result<Vec<_>, _>>()?;
        for (callee_fn, caller_file, line) in callees {
            if !visited.contains(&callee_fn) {
                visited.insert(callee_fn.clone());
                parent.insert(callee_fn.clone(), (current.clone(), caller_file, line));
                queue.push_back(callee_fn);
            }
        }
    }
    Ok(Vec::new())
}

pub fn file_count(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row("SELECT COUNT(*) FROM files", [], |row| row.get(0))
}

pub fn symbol_count(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row("SELECT COUNT(*) FROM symbols", [], |row| row.get(0))
}

pub fn edge_count(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row("SELECT COUNT(*) FROM edges", [], |row| row.get(0))
}

pub fn call_count(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row("SELECT COUNT(*) FROM calls", [], |row| row.get(0))
}

pub fn target_count(conn: &Connection) -> rusqlite::Result<i64> {
    conn.query_row("SELECT COUNT(*) FROM targets", [], |row| row.get(0))
}

pub fn find_target_for_file(conn: &Connection, source_file: &str) -> rusqlite::Result<Vec<TargetResult>> {
    let basename = std::path::Path::new(source_file)
        .file_name()
        .map(|f| f.to_string_lossy().to_string())
        .unwrap_or_default();
    let pattern = format!("%{}%", basename);
    let mut stmt = conn.prepare(
        "SELECT path, name, rule, srcs, deps FROM targets WHERE srcs LIKE ?"
    )?;
    let rows = stmt.query_map(params![pattern], |row| {
        Ok(TargetResult {
            path: row.get(0)?,
            name: row.get(1)?,
            rule: row.get(2)?,
            srcs: row.get(3)?,
            deps: row.get(4)?,
        })
    })?;
    // Filter to exact file match within the JSON array
    let results: Vec<TargetResult> = rows
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .filter(|t| {
            if let Some(ref srcs) = t.srcs {
                srcs.contains(&basename) || srcs.contains(source_file)
            } else {
                false
            }
        })
        .collect();
    Ok(results)
}

pub fn target_rdeps(conn: &Connection, target_label: &str) -> rusqlite::Result<Vec<String>> {
    let mut stmt = conn.prepare(
        "SELECT source_label FROM target_edges WHERE dep_label = ?"
    )?;
    let rows = stmt.query_map(params![target_label], |row| row.get(0))?;
    rows.collect()
}

pub fn target_deps(conn: &Connection, target_label: &str) -> rusqlite::Result<Vec<String>> {
    let mut stmt = conn.prepare(
        "SELECT dep_label FROM target_edges WHERE source_label = ?"
    )?;
    let rows = stmt.query_map(params![target_label], |row| row.get(0))?;
    rows.collect()
}

#[derive(Debug, Clone)]
pub struct TargetResult {
    pub path: String,
    pub name: String,
    pub rule: String,
    pub srcs: Option<String>,
    pub deps: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn test_db() -> Database {
        Database::open(&PathBuf::from(":memory:")).unwrap()
    }

    #[test]
    fn test_empty_lookup() {
        let db = test_db();
        db.with_conn(|conn| {
            let results = lookup(conn, "nonexistent").unwrap();
            assert!(results.is_empty());
        });
    }

    #[test]
    fn test_insert_and_lookup() {
        let db = test_db();
        db.with_conn(|conn| {
            conn.execute(
                "INSERT INTO symbols (file_path, name, kind, line, scope) VALUES (?, ?, ?, ?, ?)",
                params!["foo.cpp", "myFunc", "function", 10, "MyClass"],
            ).unwrap();
            let results = lookup(conn, "myFunc").unwrap();
            assert_eq!(results.len(), 1);
            assert_eq!(results[0].fqn, "MyClass::myFunc");
            assert_eq!(results[0].file, "foo.cpp");
            assert_eq!(results[0].line, 10);
        });
    }

    #[test]
    fn test_scoped_definition_prefers_header() {
        let db = test_db();
        db.with_conn(|conn| {
            conn.execute(
                "INSERT INTO symbols (file_path, name, kind, line, scope) VALUES (?, ?, ?, ?, ?)",
                params!["foo.cpp", "bar", "function", 50, "Foo"],
            ).unwrap();
            conn.execute(
                "INSERT INTO symbols (file_path, name, kind, line, scope) VALUES (?, ?, ?, ?, ?)",
                params!["foo.h", "bar", "prototype", 10, "Foo"],
            ).unwrap();
            let result = find_scoped_definition(conn, "bar", "Foo").unwrap();
            assert_eq!(result, Some(("foo.h".to_string(), 10)));
        });
    }

    #[test]
    fn test_impact_direct_and_transitive() {
        let db = test_db();
        db.with_conn(|conn| {
            conn.execute("INSERT INTO edges (source, target, kind) VALUES ('b.cpp', 'a.h', 'import')", []).unwrap();
            conn.execute("INSERT INTO edges (source, target, kind) VALUES ('c.cpp', 'b.cpp', 'import')", []).unwrap();
            conn.execute("INSERT INTO edges (source, target, kind) VALUES ('d.cpp', 'c.cpp', 'import')", []).unwrap();

            let result = find_impact(conn, "a.h", 10).unwrap();
            assert_eq!(result.direct, vec!["b.cpp"]);
            assert_eq!(result.transitive, vec!["c.cpp", "d.cpp"]);
            assert_eq!(result.total_affected(), 3);
        });
    }

    #[test]
    fn test_deps() {
        let db = test_db();
        db.with_conn(|conn| {
            conn.execute("INSERT INTO edges (source, target, kind) VALUES ('a.cpp', 'b.h', 'import')", []).unwrap();
            conn.execute("INSERT INTO edges (source, target, kind) VALUES ('a.cpp', 'c.h', 'import')", []).unwrap();
            conn.execute("INSERT INTO edges (source, target, kind) VALUES ('b.h', 'd.h', 'import')", []).unwrap();

            let result = find_deps(conn, "a.cpp", 10).unwrap();
            assert_eq!(result.len(), 3); // b.h, c.h, d.h
            assert!(result.contains(&"b.h".to_string()));
            assert!(result.contains(&"c.h".to_string()));
            assert!(result.contains(&"d.h".to_string()));
        });
    }

    #[test]
    fn test_callers() {
        let db = test_db();
        db.with_conn(|conn| {
            conn.execute(
                "INSERT INTO calls (caller_file, caller_function, callee_file, callee_function, line) VALUES (?, ?, ?, ?, ?)",
                params!["main.cpp", "main", "foo.cpp", "doWork", 42],
            ).unwrap();
            let callers = get_callers(conn, "doWork", "foo.cpp").unwrap();
            assert_eq!(callers.len(), 1);
            assert_eq!(callers[0].function, "main");
            assert_eq!(callers[0].file, "main.cpp");
        });
    }

    #[test]
    fn test_callees() {
        let db = test_db();
        db.with_conn(|conn| {
            conn.execute(
                "INSERT INTO calls (caller_file, caller_function, callee_file, callee_function, line) VALUES (?, ?, ?, ?, ?)",
                params!["main.cpp", "main", "foo.cpp", "doWork", 42],
            ).unwrap();
            let callees = get_callees(conn, "main", "main.cpp").unwrap();
            assert_eq!(callees.len(), 1);
            assert_eq!(callees[0].function, "doWork");
        });
    }

    #[test]
    fn test_symbols_for_file() {
        let db = test_db();
        db.with_conn(|conn| {
            conn.execute(
                "INSERT INTO symbols (file_path, name, kind, line, scope) VALUES (?, ?, ?, ?, ?)",
                params!["foo.h", "Foo", "class", 5, ""],
            ).unwrap();
            conn.execute(
                "INSERT INTO symbols (file_path, name, kind, line, scope) VALUES (?, ?, ?, ?, ?)",
                params!["foo.h", "bar", "function", 10, "Foo"],
            ).unwrap();
            let symbols = symbols_for_file(conn, "foo.h").unwrap();
            assert_eq!(symbols.len(), 2);
            assert_eq!(symbols[0].name, "Foo");
            assert_eq!(symbols[1].name, "bar");
        });
    }

    #[test]
    fn test_call_impact_transitive() {
        let db = test_db();
        db.with_conn(|conn| {
            conn.execute(
                "INSERT INTO calls VALUES ('b.cpp', 'funcB', 'a.cpp', 'funcA', 10)", [],
            ).unwrap();
            conn.execute(
                "INSERT INTO calls VALUES ('c.cpp', 'funcC', 'b.cpp', 'funcB', 20)", [],
            ).unwrap();

            let result = find_call_impact(conn, "funcA", "a.cpp", 10).unwrap();
            assert_eq!(result.len(), 2);
            assert_eq!(result[0].function, "funcB");
            assert_eq!(result[1].function, "funcC");
        });
    }

    #[test]
    fn test_file_counts() {
        let db = test_db();
        db.with_conn(|conn| {
            assert_eq!(file_count(conn).unwrap(), 0);
            assert_eq!(symbol_count(conn).unwrap(), 0);
            conn.execute("INSERT INTO files (path, content_hash) VALUES ('a.cpp', 'abc')", []).unwrap();
            assert_eq!(file_count(conn).unwrap(), 1);
        });
    }
}
