mod cli;
mod db;
mod graph;
mod index;
mod lsp;
mod server;

use clap::Parser;
use cli::{Cli, Command, DaemonAction};
use std::path::PathBuf;
use std::sync::Arc;

fn main() {
    let cli = Cli::parse();

    match cli.command {
        Command::Daemon { action } => match action {
            DaemonAction::Start => start_daemon(),
            DaemonAction::Stop => stop_daemon(),
            DaemonAction::Status => daemon_status(),
        },
        Command::Index { root } => {
            let root = root.as_deref().unwrap_or(".");
            run_index(root);
        }
        Command::Lookup { symbol, json } => query("lookup", serde_json::json!({"target": symbol}), json),
        Command::Refs { fqn, json } => {
            if fqn.len() == 1 {
                query("refs", serde_json::json!({"target": fqn[0]}), json);
            } else {
                query("refs", serde_json::json!({"targets": fqn}), json);
            }
        }
        Command::Explain { fqn, json } => query("explain", serde_json::json!({"target": fqn}), json),
        Command::Impact { target, json } => query("impact", serde_json::json!({"target": target}), json),
        Command::Deps { target, json } => query("deps", serde_json::json!({"target": target}), json),
        Command::Hotspots { limit, json } => query("hotspots", serde_json::json!({"limit": limit}), json),
        Command::Symbols { file, json } => query("symbols", serde_json::json!({"target": file}), json),
        Command::Trace { source, target, json } => query("trace", serde_json::json!({"source": source, "target": target}), json),
        Command::Status => query("status", serde_json::json!({}), false),
        Command::Reindex { files } => {
            if files.is_empty() {
                query("reindex", serde_json::json!({}), false);
            } else {
                query("reindex", serde_json::json!({"files": files}), false);
            }
        }
        Command::InstallSkill => install_skill(),
    }
}

fn open_db() -> db::Database {
    let db_path = PathBuf::from(".cortex-cache/index.db");
    if let Some(parent) = db_path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    db::Database::open(&db_path).unwrap_or_else(|e| {
        eprintln!("  \x1b[31mFailed to open database: {}\x1b[0m", e);
        std::process::exit(1);
    })
}

fn start_daemon() {
    let db = Arc::new(open_db());

    db.with_conn(|conn| {
        if let (Ok(files), Ok(symbols), Ok(edges), Ok(calls)) = (
            db::queries::file_count(conn),
            db::queries::symbol_count(conn),
            db::queries::edge_count(conn),
            db::queries::call_count(conn),
        ) {
            eprintln!(
                "  \x1b[32m✓\x1b[0m Index: {} files, {} symbols, {} edges, {} calls",
                files, symbols, edges, calls
            );
        }
    });

    if let Err(err) = server::run(db) {
        eprintln!("  \x1b[31mServer error: {}\x1b[0m", err);
        std::process::exit(1);
    }
}

fn run_index(root: &str) {
    let db_path = PathBuf::from(format!("{}/.cortex-cache/index.db", root));
    if let Some(parent) = db_path.parent() {
        std::fs::create_dir_all(parent).ok();
    }

    let db = db::Database::open(&db_path).unwrap_or_else(|e| {
        eprintln!("  \x1b[31mFailed to open database: {}\x1b[0m", e);
        std::process::exit(1);
    });

    // Clear old data
    db.with_conn(|conn| {
        conn.execute_batch(
            "DELETE FROM files; DELETE FROM symbols; DELETE FROM imports; DELETE FROM edges; DELETE FROM calls;",
        ).ok();
    });

    if let Err(e) = index::index_repo(&db, root) {
        eprintln!("  \x1b[31mIndex error: {}\x1b[0m", e);
        std::process::exit(1);
    }

    db.with_conn(|conn| {
        if let (Ok(files), Ok(symbols), Ok(edges), Ok(calls)) = (
            db::queries::file_count(conn),
            db::queries::symbol_count(conn),
            db::queries::edge_count(conn),
            db::queries::call_count(conn),
        ) {
            eprintln!(
                "  \x1b[32m✓\x1b[0m Done: {} files, {} symbols, {} edges, {} calls",
                files, symbols, edges, calls
            );
        }
    });
}

fn query(action: &str, params: serde_json::Value, json_output: bool) {
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;

    let mut stream = match UnixStream::connect(server::SOCKET_PATH) {
        Ok(s) => s,
        Err(_) => {
            eprintln!("  \x1b[33mNo daemon running. Start with: kapa-cortex daemon start\x1b[0m");
            std::process::exit(1);
        }
    };

    let payload = serde_json::json!({"action": action, "params": params});
    let bytes = serde_json::to_vec(&payload).unwrap();
    let header = (bytes.len() as u64).to_be_bytes();
    stream.write_all(&header).unwrap();
    stream.write_all(&bytes).unwrap();

    let mut header_buf = [0u8; 8];
    stream.read_exact(&mut header_buf).unwrap();
    let length = u64::from_be_bytes(header_buf) as usize;

    let mut response = vec![0u8; length];
    let mut read = 0;
    while read < length {
        let n = stream.read(&mut response[read..]).unwrap();
        if n == 0 { break; }
        read += n;
    }

    let parsed: serde_json::Value = serde_json::from_slice(&response).unwrap_or_default();

    if parsed.get("status").and_then(|s| s.as_str()) == Some("error") {
        let error = parsed.get("error").and_then(|e| e.as_str()).unwrap_or("unknown error");
        eprintln!("  \x1b[31m{}\x1b[0m", error);
        std::process::exit(1);
    }

    let data = parsed.get("data").unwrap_or(&serde_json::Value::Null);

    if json_output {
        println!("{}", serde_json::to_string_pretty(data).unwrap_or_default());
    } else {
        print_result(action, data);
    }
}

fn print_result(action: &str, data: &serde_json::Value) {
    match action {
        "lookup" => {
            let symbol = data.get("symbol").and_then(|s| s.as_str()).unwrap_or("");
            let defs = data.get("definitions").and_then(|d| d.as_array());
            if let Some(defs) = defs {
                println!("  \x1b[1m{}\x1b[0m ({} definitions):", symbol, defs.len());
                for d in defs {
                    let fqn = d.get("fqn").and_then(|s| s.as_str()).unwrap_or("");
                    let kind = d.get("kind").and_then(|s| s.as_str()).unwrap_or("");
                    let file = d.get("file").and_then(|s| s.as_str()).unwrap_or("");
                    let line = d.get("line").and_then(|l| l.as_i64()).unwrap_or(0);
                    println!("    {:<50} {:<10} {}:{}", fqn, kind, file, line);
                }
            }
        }
        "impact" | "symbol_impact" => {
            let target = data.get("target").and_then(|s| s.as_str()).unwrap_or("");
            let total = data.get("total_affected").and_then(|n| n.as_i64()).unwrap_or(0);
            println!("  \x1b[1mImpact of {}\x1b[0m ({} affected):", target, total);
            if let Some(direct) = data.get("direct").and_then(|d| d.as_array()) {
                for d in direct {
                    println!("    {}  direct", d.as_str().unwrap_or(""));
                }
            }
            if let Some(transitive) = data.get("transitive").and_then(|d| d.as_array()) {
                for t in transitive.iter().take(20) {
                    println!("    {}  transitive", t.as_str().unwrap_or(""));
                }
                if transitive.len() > 20 {
                    println!("    ... and {} more", transitive.len() - 20);
                }
            }
        }
        "deps" => {
            let target = data.get("target").and_then(|s| s.as_str()).unwrap_or("");
            let total = data.get("total").and_then(|n| n.as_i64()).unwrap_or(0);
            println!("  \x1b[1mDependencies of {}\x1b[0m ({}):", target, total);
            if let Some(deps) = data.get("dependencies").and_then(|d| d.as_array()) {
                for d in deps {
                    println!("    {}", d.as_str().unwrap_or(""));
                }
            }
        }
        "hotspots" => {
            println!("  \x1b[1mHotspots:\x1b[0m");
            if let Some(hotspots) = data.get("hotspots").and_then(|h| h.as_array()) {
                for h in hotspots {
                    let path = h.get("path").and_then(|s| s.as_str()).unwrap_or("");
                    let complexity = h.get("complexity").and_then(|n| n.as_i64()).unwrap_or(0);
                    let deps = h.get("dependents").and_then(|n| n.as_i64()).unwrap_or(0);
                    let score = h.get("score").and_then(|n| n.as_f64()).unwrap_or(0.0);
                    println!("    {:<60} c={} d={} s={:.0}", path, complexity, deps, score);
                }
            }
        }
        "explain" => {
            let fqn = data.get("fqn").and_then(|s| s.as_str()).unwrap_or("");
            let sig = data.get("signature").and_then(|s| s.as_str()).unwrap_or("");
            let file = data.get("file").and_then(|s| s.as_str()).unwrap_or("");
            let line = data.get("line").and_then(|l| l.as_i64()).unwrap_or(0);
            println!("  \x1b[1m{}\x1b[0m", fqn);
            println!("  \x1b[2m{}\x1b[0m", sig);
            println!("  \x1b[2m{}:{}\x1b[0m", file, line);
            if let Some(callers) = data.get("callers").and_then(|c| c.as_array()) {
                println!("  callers ({}):", callers.len());
                for c in callers {
                    let f = c.get("function").and_then(|s| s.as_str()).unwrap_or("");
                    let file = c.get("file").and_then(|s| s.as_str()).unwrap_or("");
                    let line = c.get("line").and_then(|l| l.as_i64()).unwrap_or(0);
                    println!("    {}  {}:{}", f, file, line);
                }
            }
            if let Some(callees) = data.get("callees").and_then(|c| c.as_array()) {
                if !callees.is_empty() {
                    println!("  callees ({}):", callees.len());
                    for c in callees {
                        let f = c.get("function").and_then(|s| s.as_str()).unwrap_or("");
                        println!("    {}", f);
                    }
                }
            }
            if let Some(overrides) = data.get("overrides").and_then(|o| o.as_array()) {
                if !overrides.is_empty() {
                    println!("  overrides ({}):", overrides.len());
                    for o in overrides {
                        let fqn = o.get("fqn").and_then(|s| s.as_str()).unwrap_or("");
                        let file = o.get("file").and_then(|s| s.as_str()).unwrap_or("");
                        let line = o.get("line").and_then(|l| l.as_i64()).unwrap_or(0);
                        println!("    {}  {}:{}", fqn, file, line);
                    }
                }
            }
        }
        "symbols" => {
            let file = data.get("file").and_then(|s| s.as_str()).unwrap_or("");
            let total = data.get("total").and_then(|n| n.as_i64()).unwrap_or(0);
            println!("  \x1b[1m{}\x1b[0m ({} symbols):", file, total);
            if let Some(symbols) = data.get("symbols").and_then(|s| s.as_array()) {
                for s in symbols {
                    let name = s.get("name").and_then(|n| n.as_str()).unwrap_or("");
                    let kind = s.get("kind").and_then(|k| k.as_str()).unwrap_or("");
                    let line = s.get("line").and_then(|l| l.as_i64()).unwrap_or(0);
                    let scope = s.get("scope").and_then(|s| s.as_str()).unwrap_or("");
                    if scope.is_empty() {
                        println!("    {:>5} {:<10} {}", line, kind, name);
                    } else {
                        println!("    {:>5} {:<10} {}::{}", line, kind, scope, name);
                    }
                }
            }
        }
        "trace" => {
            let source = data.get("source").and_then(|s| s.as_str()).unwrap_or("");
            let target = data.get("target").and_then(|s| s.as_str()).unwrap_or("");
            let hops = data.get("hops").and_then(|n| n.as_i64()).unwrap_or(0);
            println!("  \x1b[1m{} → {}\x1b[0m ({} hops):", source, target, hops);
            if let Some(path) = data.get("path").and_then(|p| p.as_array()) {
                for step in path {
                    let f = step.get("function").and_then(|s| s.as_str()).unwrap_or("");
                    let file = step.get("file").and_then(|s| s.as_str()).unwrap_or("");
                    let line = step.get("line").and_then(|l| l.as_i64()).unwrap_or(0);
                    println!("    → {}  {}:{}", f, file, line);
                }
            }
        }
        "status" => {
            println!("{}", serde_json::to_string_pretty(data).unwrap_or_default());
        }
        _ => {
            println!("{}", serde_json::to_string_pretty(data).unwrap_or_default());
        }
    }
}

fn stop_daemon() {
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;

    let mut stream = match UnixStream::connect(server::SOCKET_PATH) {
        Ok(s) => s,
        Err(_) => {
            eprintln!("  \x1b[33mNo daemon running.\x1b[0m");
            return;
        }
    };

    let payload = serde_json::json!({"action": "shutdown", "params": {}});
    let bytes = serde_json::to_vec(&payload).unwrap();
    let header = (bytes.len() as u64).to_be_bytes();
    stream.write_all(&header).ok();
    stream.write_all(&bytes).ok();

    let mut response = Vec::new();
    stream.read_to_end(&mut response).ok();
    eprintln!("  \x1b[32mDaemon stopped.\x1b[0m");
}

fn daemon_status() {
    query("status", serde_json::json!({}), true);
}

fn install_skill() {
    let skill_src = std::path::Path::new("src/interface/skill/SKILL.md");
    let skill_dst = std::path::Path::new(".claude/skills/kapa-cortex/SKILL.md");
    if let Some(parent) = skill_dst.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    match std::fs::copy(skill_src, skill_dst) {
        Ok(_) => eprintln!("  \x1b[32m✓\x1b[0m Skill installed"),
        Err(e) => eprintln!("  \x1b[31mFailed: {}\x1b[0m", e),
    }
}
