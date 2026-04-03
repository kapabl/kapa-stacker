use std::fs;

/// Hash file content using blake3. Returns hex string.
pub fn hash_file(file_path: &str) -> Result<String, String> {
    let content = fs::read(file_path).map_err(|e| format!("Cannot read {}: {}", file_path, e))?;
    let hash = blake3::hash(&content);
    Ok(hash.to_hex().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_hash_file() {
        let mut f = tempfile::NamedTempFile::new().unwrap();
        f.write_all(b"hello world").unwrap();
        let hash = hash_file(f.path().to_str().unwrap()).unwrap();
        assert_eq!(hash.len(), 64);
    }

    #[test]
    fn test_same_content_same_hash() {
        let mut f1 = tempfile::NamedTempFile::new().unwrap();
        let mut f2 = tempfile::NamedTempFile::new().unwrap();
        f1.write_all(b"identical").unwrap();
        f2.write_all(b"identical").unwrap();
        assert_eq!(
            hash_file(f1.path().to_str().unwrap()).unwrap(),
            hash_file(f2.path().to_str().unwrap()).unwrap()
        );
    }

    #[test]
    fn test_different_content_different_hash() {
        let mut f1 = tempfile::NamedTempFile::new().unwrap();
        let mut f2 = tempfile::NamedTempFile::new().unwrap();
        f1.write_all(b"aaa").unwrap();
        f2.write_all(b"bbb").unwrap();
        assert_ne!(
            hash_file(f1.path().to_str().unwrap()).unwrap(),
            hash_file(f2.path().to_str().unwrap()).unwrap()
        );
    }

    #[test]
    fn test_nonexistent_file() {
        assert!(hash_file("/nonexistent/path").is_err());
    }
}
