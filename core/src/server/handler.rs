use rusqlite::{Connection, params};
use std::os::unix::net::UnixStream;
use crate::db::{Database, queries};
use super::protocol::{read_request, write_response, Response};

pub fn handle_connection(
    mut stream: UnixStream,
    db: &Database,
) -> std::io::Result<()> {
    let request = read_request(&mut stream)?;
    let response = db.with_conn(|conn| dispatch(&request.action, &request.params, conn));
    write_response(&mut stream, &response)
}

fn dispatch(
    action: &str,
    params: &serde_json::Value,
    conn: &Connection,
) -> Response {
    let result = match action {
        "lookup" => handle_lookup(params, conn),
        "symbols" => handle_symbols(params, conn),
        "explain" => handle_explain(params, conn),
        "trace" => handle_trace(params, conn),
        "impact" => handle_impact(params, conn),
        "deps" => handle_deps(params, conn),
        "hotspots" => handle_hotspots(params, conn),
        "calls" => handle_calls(params, conn),
        "status" => handle_status(conn),
        _ => Err(format!("Unknown action: {}", action)),
    };

    match result {
        Ok(data) => Response::ok(data),
        Err(err) => Response::fail(&err),
    }
}

fn get_target(params: &serde_json::Value) -> Result<&str, String> {
    params
        .get("target")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "Missing 'target' parameter".to_string())
}

fn handle_lookup(
    params: &serde_json::Value,
    conn: &Connection,
) -> Result<serde_json::Value, String> {
    let symbol = get_target(params)?;
    let defs = queries::lookup(conn, symbol).map_err(|e| e.to_string())?;
    Ok(serde_json::json!({
        "query": "lookup",
        "symbol": symbol,
        "definitions": defs,
    }))
}

fn handle_symbols(
    params: &serde_json::Value,
    conn: &Connection,
) -> Result<serde_json::Value, String> {
    let file_path = get_target(params)?;
    let symbols = queries::symbols_for_file(conn, file_path).map_err(|e| e.to_string())?;
    let total = symbols.len();
    Ok(serde_json::json!({
        "query": "symbols",
        "file": file_path,
        "symbols": symbols,
        "total": total,
    }))
}

fn handle_explain(
    params: &serde_json::Value,
    conn: &Connection,
) -> Result<serde_json::Value, String> {
    let fqn = get_target(params)?;
    let (scope, name) = split_fqn(fqn);

    let (file, line) = queries::find_scoped_definition(conn, name, scope)
        .map_err(|e| e.to_string())?
        .ok_or_else(|| format!("Symbol not found: {}", fqn))?;

    let callers = queries::get_callers(conn, name, &file).map_err(|e| e.to_string())?;
    let callees = queries::get_callees(conn, name, &file).map_err(|e| e.to_string())?;

    let all_defs = queries::lookup(conn, name).map_err(|e| e.to_string())?;
    let overrides: Vec<_> = all_defs
        .into_iter()
        .filter(|d| {
            let d_scope = split_fqn(&d.fqn).0;
            d_scope != scope
        })
        .collect();

    let signature = read_line(&file, line as usize);

    Ok(serde_json::json!({
        "query": "explain",
        "fqn": fqn,
        "file": file,
        "line": line,
        "signature": signature,
        "callers": callers,
        "callees": callees,
        "overrides": overrides,
    }))
}

fn handle_trace(
    params: &serde_json::Value,
    conn: &Connection,
) -> Result<serde_json::Value, String> {
    let source_fqn = params
        .get("source")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "Missing 'source' parameter".to_string())?;
    let target_fqn = params
        .get("target")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "Missing 'target' parameter".to_string())?;

    let (src_scope, src_name) = split_fqn(source_fqn);
    let (tgt_scope, tgt_name) = split_fqn(target_fqn);

    let (src_file, _) = queries::find_scoped_definition(conn, src_name, src_scope)
        .map_err(|e| e.to_string())?
        .ok_or_else(|| format!("Source not found: {}", source_fqn))?;

    let (tgt_file, _) = queries::find_scoped_definition(conn, tgt_name, tgt_scope)
        .map_err(|e| e.to_string())?
        .ok_or_else(|| format!("Target not found: {}", target_fqn))?;

    let path = queries::trace_path(conn, src_name, &src_file, tgt_name, &tgt_file)
        .map_err(|e| e.to_string())?;

    let hops = path.len();
    Ok(serde_json::json!({
        "query": "trace",
        "source": source_fqn,
        "target": target_fqn,
        "path": path,
        "hops": hops,
    }))
}

fn handle_impact(
    params: &serde_json::Value,
    conn: &Connection,
) -> Result<serde_json::Value, String> {
    let target = get_target(params)?;

    // Check if target is a file or a symbol
    let file_exists: bool = conn
        .query_row("SELECT COUNT(*) FROM files WHERE path = ?", params![target], |row| row.get::<_, i64>(0))
        .map_err(|e| e.to_string())?
        > 0;

    if file_exists {
        let (direct, transitive) = queries::find_impact(conn, target, 10).map_err(|e| e.to_string())?;
        Ok(serde_json::json!({
            "query": "impact",
            "target": target,
            "direct": direct,
            "transitive": transitive,
            "total_affected": direct.len() + transitive.len(),
        }))
    } else {
        // Symbol — find its file, then do call impact
        let (scope, name) = split_fqn(target);
        let (file, _line) = queries::find_scoped_definition(conn, name, scope)
            .map_err(|e| e.to_string())?
            .ok_or_else(|| format!("Not found: {}", target))?;

        let callers = queries::find_call_impact(conn, name, &file, 10).map_err(|e| e.to_string())?;
        let affected_files: Vec<String> = callers.iter().map(|c| c.file.clone()).collect::<std::collections::HashSet<_>>().into_iter().collect();
        Ok(serde_json::json!({
            "query": "symbol_impact",
            "target": target,
            "file": file,
            "callers": callers,
            "affected_files": affected_files,
            "total_affected": affected_files.len(),
        }))
    }
}

fn handle_deps(
    params: &serde_json::Value,
    conn: &Connection,
) -> Result<serde_json::Value, String> {
    let target = get_target(params)?;
    let deps = queries::find_deps(conn, target, 10).map_err(|e| e.to_string())?;
    Ok(serde_json::json!({
        "query": "deps",
        "target": target,
        "dependencies": deps,
        "total": deps.len(),
    }))
}

fn handle_hotspots(
    params: &serde_json::Value,
    conn: &Connection,
) -> Result<serde_json::Value, String> {
    let limit = params.get("limit").and_then(|v| v.as_u64()).unwrap_or(20) as usize;
    let hotspots = queries::find_hotspots(conn, limit).map_err(|e| e.to_string())?;
    Ok(serde_json::json!({
        "query": "hotspots",
        "hotspots": hotspots,
    }))
}

fn handle_calls(
    params: &serde_json::Value,
    conn: &Connection,
) -> Result<serde_json::Value, String> {
    let fqn = get_target(params)?;
    let (scope, name) = split_fqn(fqn);

    let (file, _line) = queries::find_scoped_definition(conn, name, scope)
        .map_err(|e| e.to_string())?
        .ok_or_else(|| format!("Symbol not found: {}", fqn))?;

    let callers = queries::find_call_impact(conn, name, &file, 10).map_err(|e| e.to_string())?;
    let affected_files: Vec<String> = callers.iter().map(|c| c.file.clone()).collect::<std::collections::HashSet<_>>().into_iter().collect();

    Ok(serde_json::json!({
        "query": "calls",
        "fqn": fqn,
        "file": file,
        "callers": callers,
        "affected_files": affected_files,
        "total_affected": affected_files.len(),
    }))
}

fn handle_status(conn: &Connection) -> Result<serde_json::Value, String> {
    let files = queries::file_count(conn).map_err(|e| e.to_string())?;
    let symbols = queries::symbol_count(conn).map_err(|e| e.to_string())?;
    let edges = queries::edge_count(conn).map_err(|e| e.to_string())?;
    let calls = queries::call_count(conn).map_err(|e| e.to_string())?;

    Ok(serde_json::json!({
        "running": true,
        "index_files": files,
        "index_symbols": symbols,
        "index_edges": edges,
        "index_calls": calls,
    }))
}

fn split_fqn(fqn: &str) -> (&str, &str) {
    if let Some(pos) = fqn.rfind("::") {
        (&fqn[..pos], &fqn[pos + 2..])
    } else {
        ("", fqn)
    }
}

fn read_line(file_path: &str, line: usize) -> String {
    std::fs::read_to_string(file_path)
        .ok()
        .and_then(|content| {
            content.lines().nth(line.saturating_sub(1)).map(|l| l.trim().to_string())
        })
        .unwrap_or_default()
}
