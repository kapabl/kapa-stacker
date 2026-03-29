#!/usr/bin/env bash
# Check if .cortex-cache/ is fresh relative to latest commit.
# Exit 0 + "fresh" if cache is up to date.
# Exit 1 + "stale" if cache is missing or outdated.

CACHE_DIR=".cortex-cache"
CACHE_FILE="$CACHE_DIR/tags.json"

if [ ! -f "$CACHE_FILE" ]; then
    echo "stale"
    exit 1
fi

LATEST_COMMIT=$(git log -1 --format=%ct 2>/dev/null || echo 0)

# Cross-platform stat: GNU (Linux) vs BSD (macOS)
if stat --version >/dev/null 2>&1; then
    CACHE_MTIME=$(stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)
else
    CACHE_MTIME=$(stat -f %m "$CACHE_FILE" 2>/dev/null || echo 0)
fi

if [ "$LATEST_COMMIT" -gt "$CACHE_MTIME" ]; then
    echo "stale"
    exit 1
fi

echo "fresh"
exit 0
