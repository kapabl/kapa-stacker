use std::os::unix::net::UnixListener;
use std::path::Path;
use std::sync::Arc;
use crate::infrastructure::sqlite::Database;
use crate::infrastructure::lsp::LspClient;
use super::handler;

pub const SOCKET_PATH: &str = "/tmp/kapa-cortex.sock";

pub struct DaemonState {
    pub db: Database,
    pub lsp: std::sync::Mutex<Option<LspClient>>,
}

pub fn run(db: Arc<Database>) -> std::io::Result<()> {
    let socket_path = Path::new(SOCKET_PATH);
    if socket_path.exists() {
        std::fs::remove_file(socket_path)?;
    }

    // Boot LSP in background
    let language = crate::infrastructure::lsp::detect_language(".");
    let lsp: std::sync::Mutex<Option<LspClient>> = std::sync::Mutex::new(None);
    if let Some(lang) = language {
        eprintln!("  \x1b[36mBooting LSP for {}...\x1b[0m", lang);
        match LspClient::start(lang, ".") {
            Some(client) => {
                eprintln!("  \x1b[32m✓\x1b[0m LSP ready");
                *lsp.lock().unwrap() = Some(client);
            }
            None => {
                eprintln!("  \x1b[33mLSP not available\x1b[0m");
            }
        }
    }

    let state = Arc::new(DaemonState {
        db: Arc::try_unwrap(db).unwrap_or_else(|arc| {
            // Can't unwrap, open a new connection
            Database::open(&Path::new(".cortex-cache/index.db")).unwrap()
        }),
        lsp,
    });

    let listener = UnixListener::bind(socket_path)?;
    eprintln!("  \x1b[32m✓\x1b[0m Daemon listening on {}", SOCKET_PATH);

    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                let state = Arc::clone(&state);
                std::thread::spawn(move || {
                    if let Err(err) = handler::handle_connection_v2(stream, &state) {
                        let msg = err.to_string();
                        // Silently ignore probe connections (health checks, ensure_daemon)
                        if !msg.contains("fill whole buffer") && !msg.contains("broken pipe") {
                            eprintln!("  \x1b[33mHandler error: {}\x1b[0m", msg);
                        }
                    }
                });
            }
            Err(err) => {
                eprintln!("  \x1b[33mAccept error: {}\x1b[0m", err);
            }
        }
    }

    Ok(())
}
