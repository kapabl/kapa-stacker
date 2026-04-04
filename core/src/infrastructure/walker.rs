use std::path::Path;

const SOURCE_EXTENSIONS: &[&str] = &[
    ".py", ".pyi", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
    ".java", ".kt", ".kts", ".go", ".rs",
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
];

const SKIP_DIRS: &[&str] = &[
    ".git", "node_modules", "__pycache__", ".mypy_cache",
    "venv", ".venv", "env", ".env", "dist", "build",
    ".cortex-cache", ".tox", ".pytest_cache", ".cache",
];

const BUCK_FILENAMES: &[&str] = &["TARGETS", "BUCK", "TARGETS.v2"];

pub fn find_buck_files(root: &str) -> Result<Vec<String>, String> {
    let mut files = Vec::new();
    walk_dir_buck(std::path::Path::new(root), &mut files)?;
    Ok(files)
}

fn walk_dir_buck(dir: &Path, files: &mut Vec<String>) -> Result<(), String> {
    let entries = std::fs::read_dir(dir).map_err(|e| format!("Cannot read {}: {}", dir.display(), e))?;
    for entry in entries {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        if path.is_dir() {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if !SKIP_DIRS.contains(&name_str.as_ref()) {
                walk_dir_buck(&path, files)?;
            }
        } else {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if BUCK_FILENAMES.contains(&name_str.as_ref()) {
                files.push(path.to_string_lossy().to_string());
            }
        }
    }
    Ok(())
}

pub fn find_source_files(root: &str) -> Result<Vec<String>, String> {
    let mut files = Vec::new();
    walk_dir(Path::new(root), &mut files)?;
    Ok(files)
}

fn walk_dir(dir: &Path, files: &mut Vec<String>) -> Result<(), String> {
    let entries = std::fs::read_dir(dir).map_err(|e| format!("Cannot read {}: {}", dir.display(), e))?;

    for entry in entries {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();

        if path.is_dir() {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if !SKIP_DIRS.contains(&name_str.as_ref()) {
                walk_dir(&path, files)?;
            }
        } else if let Some(ext) = path.extension() {
            let ext_with_dot = format!(".{}", ext.to_string_lossy());
            if SOURCE_EXTENSIONS.contains(&ext_with_dot.as_str()) {
                files.push(path.to_string_lossy().to_string());
            }
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_find_source_files() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("main.py"), "print()").unwrap();
        fs::write(dir.path().join("lib.rs"), "fn main() {}").unwrap();
        fs::write(dir.path().join("readme.md"), "# Hello").unwrap();
        fs::write(dir.path().join("data.csv"), "a,b").unwrap();

        let files = find_source_files(dir.path().to_str().unwrap()).unwrap();
        assert_eq!(files.len(), 2); // .py and .rs, not .md or .csv
    }

    #[test]
    fn test_skip_dirs() {
        let dir = tempfile::tempdir().unwrap();
        let git_dir = dir.path().join(".git");
        fs::create_dir(&git_dir).unwrap();
        fs::write(git_dir.join("config.py"), "x").unwrap();
        fs::write(dir.path().join("main.py"), "y").unwrap();

        let files = find_source_files(dir.path().to_str().unwrap()).unwrap();
        assert_eq!(files.len(), 1); // only main.py, not .git/config.py
    }

    #[test]
    fn test_empty_dir() {
        let dir = tempfile::tempdir().unwrap();
        let files = find_source_files(dir.path().to_str().unwrap()).unwrap();
        assert!(files.is_empty());
    }
}
