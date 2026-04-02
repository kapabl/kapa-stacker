use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "kapa-cortex", about = "Local code intelligence engine")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand)]
pub enum Command {
    /// Start the daemon
    #[command(name = "daemon")]
    Daemon {
        #[command(subcommand)]
        action: DaemonAction,
    },
    /// Index the repository
    Index {
        /// Root directory (default: current dir)
        root: Option<String>,
    },
    /// Find all definitions of a symbol
    Lookup {
        /// Symbol name
        symbol: String,
        /// JSON output
        #[arg(long)]
        json: bool,
    },
    /// Find LSP references for a symbol
    Refs {
        /// Fully qualified name(s)
        fqn: Vec<String>,
        /// JSON output
        #[arg(long)]
        json: bool,
    },
    /// Compact symbol summary
    Explain {
        /// Fully qualified name
        fqn: String,
        /// JSON output
        #[arg(long)]
        json: bool,
    },
    /// What breaks if this changes
    Impact {
        /// File path or symbol FQN
        target: String,
        /// JSON output
        #[arg(long)]
        json: bool,
    },
    /// Transitive dependencies
    Deps {
        /// File path
        target: String,
        /// JSON output
        #[arg(long)]
        json: bool,
    },
    /// Rank files by risk
    Hotspots {
        /// Max results
        #[arg(long, default_value = "20")]
        limit: usize,
        /// JSON output
        #[arg(long)]
        json: bool,
    },
    /// List symbols in a file
    Symbols {
        /// File path
        file: String,
        /// JSON output
        #[arg(long)]
        json: bool,
    },
    /// Trace call path between two symbols
    Trace {
        /// Source FQN
        source: String,
        /// Target FQN
        target: String,
        /// JSON output
        #[arg(long)]
        json: bool,
    },
    /// Check status
    Status,
    /// Re-index specific files
    Reindex {
        /// Files to re-index (all if omitted)
        files: Vec<String>,
    },
    /// Install Claude Code skill
    InstallSkill,
}

#[derive(Subcommand)]
pub enum DaemonAction {
    /// Start daemon
    Start,
    /// Stop daemon
    Stop,
    /// Check daemon status
    Status,
}
