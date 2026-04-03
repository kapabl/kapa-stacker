use std::process::Command;
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct LizardOutput {
    #[serde(rename = "filename")]
    file: String,
    #[serde(rename = "nloc")]
    lines: i64,
    #[serde(rename = "function_list")]
    functions: Vec<LizardFunction>,
}

#[derive(Debug, Deserialize)]
struct LizardFunction {
    #[serde(rename = "cyclomatic_complexity")]
    complexity: i64,
}

pub struct FileComplexity {
    pub path: String,
    pub lines: i64,
    pub complexity: i64,
}

/// Run lizard on a directory recursively, return complexity per file.
pub fn analyze_directory(root: &str) -> Vec<FileComplexity> {
    let output = match Command::new("lizard")
        .args(["--csv", root])
        .output()
    {
        Ok(o) => o,
        Err(_) => return Vec::new(),
    };

    if !output.status.success() {
        return Vec::new();
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    parse_lizard_csv(&stdout)
}

/// Run lizard on a single file.
pub fn analyze_file(file_path: &str) -> Option<FileComplexity> {
    let output = Command::new("lizard")
        .args(["--csv", "--", file_path])
        .output()
        .ok()?;

    if !output.status.success() {
        return None;
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let results = parse_lizard_csv(&stdout);

    // Sum complexity across all functions in the file
    let total_complexity: i64 = results.iter().map(|r| r.complexity).sum();
    let lines: i64 = results.first().map(|r| r.lines).unwrap_or(0);

    if total_complexity > 0 || lines > 0 {
        Some(FileComplexity {
            path: file_path.to_string(),
            lines,
            complexity: total_complexity,
        })
    } else {
        None
    }
}

/// Load complexity from a JSON cache file.
pub fn load_complexity_cache(root: &str) -> Option<std::collections::HashMap<String, FileComplexity>> {
    let path = std::path::Path::new(root).join(".cortex-cache/complexity.json");
    let content = std::fs::read_to_string(path).ok()?;
    let raw: serde_json::Value = serde_json::from_str(&content).ok()?;
    let obj = raw.as_object()?;
    let mut result = std::collections::HashMap::new();
    for (file_path, data) in obj {
        let complexity = data.get("complexity").and_then(|v| v.as_i64()).unwrap_or(0);
        let lines = data.get("lines").and_then(|v| v.as_i64()).unwrap_or(0);
        result.insert(file_path.clone(), FileComplexity {
            path: file_path.clone(), lines, complexity,
        });
    }
    Some(result)
}

fn parse_lizard_csv(csv: &str) -> Vec<FileComplexity> {
    // lizard CSV columns: NLOC,CCN,Token,PARAM,Length,Location,File,Function,LongName,StartLine,EndLine
    // Column 1 = CCN, Column 6 = File (0-indexed)
    use std::collections::HashMap;

    let mut by_file: HashMap<String, (i64, i64)> = HashMap::new();

    for line in csv.lines() {
        let fields: Vec<&str> = line.split(',').collect();
        if fields.len() < 8 {
            continue;
        }
        let nloc: i64 = match fields[0].trim().parse() {
            Ok(n) => n,
            Err(_) => continue, // skip header
        };
        let ccn: i64 = fields[1].trim().parse().unwrap_or(0);
        let file = fields[6].trim().trim_matches('"').trim_start_matches("./").to_string();

        let entry = by_file.entry(file).or_insert((0, 0));
        entry.0 += ccn;
        entry.1 = entry.1.max(nloc);
    }

    by_file
        .into_iter()
        .map(|(path, (complexity, lines))| FileComplexity { path, lines, complexity })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_load_complexity_cache() {
        let dir = tempfile::tempdir().unwrap();
        let cache_dir = dir.path().join(".cortex-cache");
        std::fs::create_dir(&cache_dir).unwrap();
        std::fs::write(
            cache_dir.join("complexity.json"),
            r#"{"src/foo.py": {"complexity": 5, "lines": 100, "language": "Python"}}"#,
        ).unwrap();

        let cache = load_complexity_cache(dir.path().to_str().unwrap()).unwrap();
        assert!(cache.contains_key("src/foo.py"));
        assert_eq!(cache["src/foo.py"].complexity, 5);
        assert_eq!(cache["src/foo.py"].lines, 100);
    }

    #[test]
    fn test_load_complexity_cache_missing() {
        let dir = tempfile::tempdir().unwrap();
        assert!(load_complexity_cache(dir.path().to_str().unwrap()).is_none());
    }

    #[test]
    fn test_load_complexity_cache_empty() {
        let dir = tempfile::tempdir().unwrap();
        let cache_dir = dir.path().join(".cortex-cache");
        std::fs::create_dir(&cache_dir).unwrap();
        std::fs::write(cache_dir.join("complexity.json"), "{}").unwrap();

        let cache = load_complexity_cache(dir.path().to_str().unwrap()).unwrap();
        assert!(cache.is_empty());
    }
}
