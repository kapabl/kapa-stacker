use clap::{Parser, Subcommand};

#[derive(Clone, Copy, PartialEq)]
pub enum OutputMode {
    Text,
    Json,
    Briefing,
}

pub fn parse_output_mode(json: bool, brief: bool) -> OutputMode {
    if brief { OutputMode::Briefing }
    else if json { OutputMode::Json }
    else { OutputMode::Text }
}

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
    /// Go to definition of a symbol
    #[command(name = "defs")]
    Defs {
        /// Symbol name
        symbol: String,
        /// JSON output
        #[arg(long)]
        json: bool,
        /// Briefing output
        #[arg(long)]
        brief: bool,
    },
    /// Find all references to a symbol
    Refs {
        /// Fully qualified name(s)
        fqn: Vec<String>,
        /// JSON output
        #[arg(long)]
        json: bool,
        /// Briefing output
        #[arg(long)]
        brief: bool,
    },
    /// Inspect symbol: signature, callers, callees, overrides
    #[command(name = "inspect")]
    Inspect {
        /// Fully qualified name
        fqn: String,
        /// JSON output
        #[arg(long)]
        json: bool,
        /// Briefing output
        #[arg(long)]
        brief: bool,
    },
    /// Reverse dependencies: what breaks if this file or symbol changes
    #[command(name = "rdeps")]
    Rdeps {
        /// File path or symbol FQN
        target: String,
        /// JSON output
        #[arg(long)]
        json: bool,
        /// Briefing output
        #[arg(long)]
        brief: bool,
    },
    /// Transitive dependencies of a file
    Deps {
        /// File path
        target: String,
        /// JSON output
        #[arg(long)]
        json: bool,
        /// Briefing output
        #[arg(long)]
        brief: bool,
    },
    /// Rank files by risk
    Hotspots {
        /// Max results
        #[arg(long, default_value = "20")]
        limit: usize,
        /// JSON output
        #[arg(long)]
        json: bool,
        /// Briefing output
        #[arg(long)]
        brief: bool,
    },
    /// List symbols in a file
    Symbols {
        /// File path
        file: String,
        /// JSON output
        #[arg(long)]
        json: bool,
        /// Briefing output
        #[arg(long)]
        brief: bool,
    },
    /// Trace call path between two symbols
    Trace {
        /// Source symbol
        source: String,
        /// Target symbol
        target: String,
        /// JSON output
        #[arg(long)]
        json: bool,
        /// Briefing output
        #[arg(long)]
        brief: bool,
    },
    /// Analyze branch and propose stacked PRs
    Analyze {
        /// Base branch
        #[arg(long)]
        base: Option<String>,
        /// Max files per PR
        #[arg(long, default_value = "3")]
        max_files: usize,
        /// Max lines per PR
        #[arg(long, default_value = "200")]
        max_lines: i64,
        /// JSON output
        #[arg(long)]
        json: bool,
        /// Briefing output
        #[arg(long)]
        brief: bool,
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
