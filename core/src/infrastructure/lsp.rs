use serde_json::Value;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};

pub struct LspClient {
    process: Child,
    writer: Mutex<Box<dyn Write + Send>>,
    reader: Mutex<BufReader<Box<dyn Read + Send>>>,
    request_id: AtomicI64,
}

impl LspClient {
    pub fn start(language: &str, root_path: &str) -> Option<Self> {
        let (binary, args) = server_command(language)?;
        if std::process::Command::new("which").arg(binary).output().ok()?.status.success() == false {
            return None;
        }

        let mut child = Command::new(binary)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .ok()?;

        let stdin = child.stdin.take()?;
        let stdout = child.stdout.take()?;

        let client = LspClient {
            process: child,
            writer: Mutex::new(Box::new(stdin)),
            reader: Mutex::new(BufReader::new(Box::new(stdout))),
            request_id: AtomicI64::new(0),
        };

        // Initialize
        let root_uri = format!("file://{}", std::fs::canonicalize(root_path).ok()?.display());
        let _init_result = client.request("initialize", serde_json::json!({
            "processId": std::process::id(),
            "rootUri": root_uri,
            "capabilities": {"window": {"workDoneProgress": true}},
            "workspaceFolders": [{"uri": root_uri, "name": "root"}],
        }))?;

        client.notify("initialized", serde_json::json!({}))?;

        // Open a trigger file to kick CDB discovery (clangd needs this)
        if let Some(trigger) = find_trigger_file(root_path, language) {
            client.open_file(&trigger);
            // Wait for clangd to start indexing
            std::thread::sleep(std::time::Duration::from_secs(2));
        }

        Some(client)
    }

    pub fn get_references(&self, file_path: &str, line: i64, column: i64) -> Vec<Value> {
        let uri = path_to_uri(file_path);
        self.open_file(file_path);

        let result = self.request("textDocument/references", serde_json::json!({
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": column},
            "context": {"includeDeclaration": false},
        }));

        match result {
            Some(Value::Array(refs)) => refs,
            _ => Vec::new(),
        }
    }

    fn open_file(&self, file_path: &str) {
        let uri = path_to_uri(file_path);
        let text = std::fs::read_to_string(file_path).unwrap_or_default();
        self.notify("textDocument/didOpen", serde_json::json!({
            "textDocument": {
                "uri": uri,
                "languageId": "cpp",
                "version": 1,
                "text": text,
            },
        }));
    }

    fn request(&self, method: &str, params: Value) -> Option<Value> {
        let id = self.request_id.fetch_add(1, Ordering::SeqCst) + 1;
        let msg = serde_json::json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });

        self.write_message(&msg).ok()?;

        // Read responses until we get the one matching our id
        loop {
            let response = self.read_message()?;
            // Handle server requests (like window/workDoneProgress/create)
            if response.get("method").is_some() && response.get("id").is_some() {
                // Respond to server request
                let server_id = response.get("id").cloned()?;
                let reply = serde_json::json!({"jsonrpc": "2.0", "id": server_id, "result": null});
                self.write_message(&reply).ok();
                continue;
            }
            // Skip notifications
            if response.get("method").is_some() {
                continue;
            }
            // Check if it's our response
            if response.get("id").and_then(|v| v.as_i64()) == Some(id) {
                return response.get("result").cloned();
            }
        }
    }

    fn notify(&self, method: &str, params: Value) -> Option<()> {
        let msg = serde_json::json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        });
        self.write_message(&msg).ok()
    }

    fn write_message(&self, msg: &Value) -> std::io::Result<()> {
        let body = serde_json::to_vec(msg)?;
        let header = format!("Content-Length: {}\r\n\r\n", body.len());
        let mut writer = self.writer.lock().unwrap();
        writer.write_all(header.as_bytes())?;
        writer.write_all(&body)?;
        writer.flush()
    }

    fn read_message(&self) -> Option<Value> {
        let mut reader = self.reader.lock().unwrap();

        // Read headers
        let mut content_length: usize = 0;
        loop {
            let mut line = String::new();
            reader.read_line(&mut line).ok()?;
            let trimmed = line.trim();
            if trimmed.is_empty() {
                break;
            }
            if let Some(len_str) = trimmed.strip_prefix("Content-Length: ") {
                content_length = len_str.parse().ok()?;
            }
        }

        if content_length == 0 {
            return None;
        }

        let mut body = vec![0u8; content_length];
        reader.read_exact(&mut body).ok()?;
        serde_json::from_slice(&body).ok()
    }
}

impl Drop for LspClient {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
    }
}

fn server_command(language: &str) -> Option<(&'static str, Vec<&'static str>)> {
    match language {
        "cpp" | "c" => Some(("clangd", vec!["--background-index"])),
        "python" => Some(("pyright-langserver", vec!["--stdio"])),
        "go" => Some(("gopls", vec!["serve"])),
        "rust" => Some(("rust-analyzer", vec![])),
        "java" => Some(("jdtls", vec![])),
        "typescript" | "javascript" => Some(("typescript-language-server", vec!["--stdio"])),
        _ => None,
    }
}

fn path_to_uri(path: &str) -> String {
    let abs = std::fs::canonicalize(path)
        .unwrap_or_else(|_| std::path::PathBuf::from(path));
    format!("file://{}", abs.display())
}

fn find_trigger_file(root: &str, language: &str) -> Option<String> {
    // For C/C++: grab first file from compile_commands.json
    let cdb = Path::new(root).join("compile_commands.json");
    if cdb.exists() {
        if let Ok(content) = std::fs::read_to_string(&cdb) {
            if let Ok(entries) = serde_json::from_str::<Vec<serde_json::Value>>(&content) {
                if let Some(first) = entries.first() {
                    if let Some(file) = first.get("file").and_then(|f| f.as_str()) {
                        return Some(file.to_string());
                    }
                }
            }
        }
    }
    None
}

pub fn detect_language(root: &str) -> Option<&'static str> {
    let root = Path::new(root);
    if root.join("go.mod").exists() { return Some("go"); }
    if root.join("Cargo.toml").exists() { return Some("rust"); }
    if root.join("CMakeLists.txt").exists() || root.join("compile_commands.json").exists() { return Some("cpp"); }
    if root.join("pyproject.toml").exists() || root.join("setup.py").exists() { return Some("python"); }
    if root.join("package.json").exists() { return Some("typescript"); }
    if root.join("build.gradle").exists() || root.join("pom.xml").exists() { return Some("java"); }
    None
}

/// Find column where symbol starts on a given line.
pub fn find_column(file_path: &str, line: usize, symbol: &str) -> usize {
    std::fs::read_to_string(file_path)
        .ok()
        .and_then(|content| {
            content.lines().nth(line).and_then(|l| {
                l.find(symbol).map(|col| col)
            })
        })
        .unwrap_or(0)
}
