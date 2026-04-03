use std::collections::HashMap;

/// Filter cached cochange data to only requested paths.
pub fn filter_cached(
    cache: &HashMap<String, i64>,
    paths: &[String],
) -> HashMap<(String, String), i64> {
    let path_set: std::collections::HashSet<&str> = paths.iter().map(|p| p.as_str()).collect();
    let mut result = HashMap::new();

    for (key, count) in cache {
        let parts: Vec<&str> = key.split("::").collect();
        if parts.len() != 2 {
            continue;
        }
        if path_set.contains(parts[0]) && path_set.contains(parts[1]) {
            result.insert((parts[0].to_string(), parts[1].to_string()), *count);
        }
    }

    result
}

/// Load cochange cache from JSON file.
pub fn load_cache(root: &str) -> Option<HashMap<String, i64>> {
    let path = std::path::Path::new(root).join(".cortex-cache/cochange.json");
    let content = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&content).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_filter_to_requested_paths() {
        let mut cache = HashMap::new();
        cache.insert("a.py::b.py".to_string(), 5);
        cache.insert("a.py::c.py".to_string(), 3);
        cache.insert("d.py::e.py".to_string(), 1);

        let result = filter_cached(&cache, &["a.py".into(), "b.py".into()]);
        assert_eq!(result.len(), 1);
        assert_eq!(result[&("a.py".to_string(), "b.py".to_string())], 5);
    }

    #[test]
    fn test_empty_cache() {
        let result = filter_cached(&HashMap::new(), &["a.py".into()]);
        assert!(result.is_empty());
    }

    #[test]
    fn test_no_matching_paths() {
        let mut cache = HashMap::new();
        cache.insert("a.py::b.py".to_string(), 5);
        let result = filter_cached(&cache, &["x.py".into(), "y.py".into()]);
        assert!(result.is_empty());
    }

    #[test]
    fn test_skips_malformed_keys() {
        let mut cache = HashMap::new();
        cache.insert("a.py::b.py".to_string(), 5);
        cache.insert("bad_key".to_string(), 2);
        let result = filter_cached(&cache, &["a.py".into(), "b.py".into()]);
        assert_eq!(result.len(), 1);
    }

    #[test]
    fn test_load_cache_from_file() {
        let dir = tempfile::tempdir().unwrap();
        let cache_dir = dir.path().join(".cortex-cache");
        std::fs::create_dir(&cache_dir).unwrap();
        std::fs::write(
            cache_dir.join("cochange.json"),
            "{\"src/a.py::src/b.py\": 10}",
        ).unwrap();

        let cache = load_cache(dir.path().to_str().unwrap()).unwrap();
        assert_eq!(cache["src/a.py::src/b.py"], 10);
    }

    #[test]
    fn test_load_cache_missing() {
        let dir = tempfile::tempdir().unwrap();
        assert!(load_cache(dir.path().to_str().unwrap()).is_none());
    }
}
