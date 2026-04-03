use crate::domain::entities::{AnalysisResult, ChangedFile, ProposedPr};
use crate::infrastructure::{git, llm};
use std::collections::HashMap;

/// Analyze a branch and propose stacked PRs.
pub fn analyze_branch(base: &str, max_files: usize, max_lines: i64) -> Result<AnalysisResult, String> {
    let branch = git::current_branch()?;
    let files = git::diff_stat(base)?;

    if files.is_empty() {
        return Ok(AnalysisResult {
            branch,
            base: base.to_string(),
            files: Vec::new(),
            prs: Vec::new(),
        });
    }

    let prs = group_into_prs(&files, max_files, max_lines);

    Ok(AnalysisResult {
        branch,
        base: base.to_string(),
        files,
        prs,
    })
}

fn group_into_prs(files: &[ChangedFile], max_files: usize, max_lines: i64) -> Vec<ProposedPr> {
    // Group by module (top-level directory)
    let mut groups: HashMap<String, Vec<&ChangedFile>> = HashMap::new();
    for file in files {
        let key = file.module_key();
        groups.entry(key).or_default().push(file);
    }

    let mut prs = Vec::new();
    let mut order: i64 = 1;

    // Text/docs files go first
    let doc_files: Vec<String> = files
        .iter()
        .filter(|f| f.is_text_or_docs())
        .map(|f| f.path.clone())
        .collect();
    if !doc_files.is_empty() {
        prs.push(ProposedPr {
            title: "Documentation updates".to_string(),
            description: llm::rule_based_description(&doc_files),
            files: doc_files,
            order,
            risk_level: "low".to_string(),
            depends_on: Vec::new(),
        });
        order += 1;
    }

    // Split remaining files by module, respecting max_files and max_lines
    for (module, module_files) in &groups {
        let code_files: Vec<&ChangedFile> = module_files
            .iter()
            .filter(|f| !f.is_text_or_docs())
            .cloned()
            .collect();
        if code_files.is_empty() {
            continue;
        }

        let mut current_files: Vec<String> = Vec::new();
        let mut current_lines: i64 = 0;

        for file in &code_files {
            if (current_files.len() >= max_files || current_lines + file.code_lines() > max_lines)
                && !current_files.is_empty()
            {
                prs.push(ProposedPr {
                    title: format!("{} changes (part {})", module, order),
                    description: llm::rule_based_description(&current_files),
                    files: current_files.clone(),
                    order,
                    risk_level: risk_level(&code_files),
                    depends_on: if order > 1 { vec![order - 1] } else { Vec::new() },
                });
                order += 1;
                current_files.clear();
                current_lines = 0;
            }
            current_files.push(file.path.clone());
            current_lines += file.code_lines();
        }

        if !current_files.is_empty() {
            prs.push(ProposedPr {
                title: format!("{} changes", module),
                description: llm::rule_based_description(&current_files),
                files: current_files,
                order,
                risk_level: risk_level(&code_files),
                depends_on: if order > 1 { vec![order - 1] } else { Vec::new() },
            });
            order += 1;
        }
    }

    prs
}

fn risk_level(files: &[&ChangedFile]) -> String {
    let total_lines: i64 = files.iter().map(|f| f.code_lines()).sum();
    let max_complexity: i64 = files.iter().map(|f| f.complexity).max().unwrap_or(0);

    if total_lines > 500 || max_complexity > 50 {
        "high".to_string()
    } else if total_lines > 200 || max_complexity > 20 {
        "medium".to_string()
    } else {
        "low".to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn file(path: &str, added: i64, removed: i64) -> ChangedFile {
        ChangedFile {
            path: path.to_string(), added, removed,
            status: "M".to_string(), diff_text: String::new(),
            complexity: 0, structural_ratio: 1.0,
        }
    }

    #[test]
    fn test_empty_files_no_prs() {
        let prs = group_into_prs(&[], 3, 200);
        assert!(prs.is_empty());
    }

    #[test]
    fn test_docs_go_first() {
        let files = vec![file("src/main.rs", 50, 10), file("README.md", 5, 0)];
        let prs = group_into_prs(&files, 3, 200);
        assert_eq!(prs[0].title, "Documentation updates");
        assert!(prs[0].files.contains(&"README.md".to_string()));
    }

    #[test]
    fn test_split_by_max_files() {
        let files = vec![
            file("src/a.rs", 10, 0),
            file("src/b.rs", 10, 0),
            file("src/c.rs", 10, 0),
            file("src/d.rs", 10, 0),
        ];
        let prs = group_into_prs(&files, 2, 1000);
        assert!(prs.len() >= 2);
    }

    #[test]
    fn test_split_by_max_lines() {
        let files = vec![
            file("src/a.rs", 150, 0),
            file("src/b.rs", 150, 0),
        ];
        let prs = group_into_prs(&files, 10, 200);
        assert!(prs.len() >= 2);
    }

    #[test]
    fn test_risk_level_low() {
        let f = file("a.rs", 10, 5);
        assert_eq!(risk_level(&[&f]), "low");
    }

    #[test]
    fn test_risk_level_medium() {
        let f = file("a.rs", 150, 100);
        assert_eq!(risk_level(&[&f]), "medium");
    }

    #[test]
    fn test_risk_level_high() {
        let f = file("a.rs", 300, 300);
        assert_eq!(risk_level(&[&f]), "high");
    }

    #[test]
    fn test_risk_level_high_complexity() {
        let mut f = file("a.rs", 10, 0);
        f.complexity = 60;
        assert_eq!(risk_level(&[&f]), "high");
    }

    #[test]
    fn test_rule_based_description() {
        let files = vec!["a.rs".to_string(), "b.rs".to_string()];
        let desc = llm::rule_based_description(&files);
        assert!(desc.contains("a.rs"));
        assert!(desc.contains("b.rs"));
    }

    #[test]
    fn test_rule_based_description_many_files() {
        let files: Vec<String> = (0..10).map(|i| format!("file{}.rs", i)).collect();
        let desc = llm::rule_based_description(&files);
        assert!(desc.contains("5 more"));
    }
}
