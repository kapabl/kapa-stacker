"""Tests for IncrementalIndexer."""

import os
import tempfile
import unittest

from src.infrastructure.indexer.incremental_indexer import (
    build_full, index_file, update_file, find_source_files,
)
from src.infrastructure.indexer.index_store import IndexStore


class TestIndexFile(unittest.TestCase):

    def test_indexes_python_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as tmp_file:
            tmp_file.write("import os\n\nclass Foo:\n    pass\n")
            path = tmp_file.name

        try:
            store = IndexStore()
            index_file(store, path)

            self.assertEqual(store.file_count, 1)
            self.assertIn(path, store.files)
            self.assertEqual(store.files[path].language, "python")
        finally:
            os.unlink(path)

    def test_skips_unknown_language(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xyz", delete=False
        ) as tmp_file:
            tmp_file.write("unknown content")
            path = tmp_file.name

        try:
            store = IndexStore()
            index_file(store, path)
            self.assertEqual(store.file_count, 0)
        finally:
            os.unlink(path)


class TestUpdateFile(unittest.TestCase):

    def test_update_changed_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as tmp_file:
            tmp_file.write("class Foo:\n    pass\n")
            path = tmp_file.name

        try:
            store = IndexStore()
            index_file(store, path)
            old_hash = store.files[path].file_hash

            # Modify the file
            with open(path, "w") as modified_file:
                modified_file.write("class Bar:\n    pass\n")

            update_file(store, path)
            self.assertNotEqual(store.files[path].file_hash, old_hash)
        finally:
            os.unlink(path)

    def test_update_deleted_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as tmp_file:
            tmp_file.write("class Foo:\n    pass\n")
            path = tmp_file.name

        store = IndexStore()
        index_file(store, path)
        self.assertEqual(store.file_count, 1)

        os.unlink(path)
        update_file(store, path)
        self.assertEqual(store.file_count, 0)

    def test_skip_unchanged_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as tmp_file:
            tmp_file.write("class Foo:\n    pass\n")
            path = tmp_file.name

        try:
            store = IndexStore()
            index_file(store, path)
            old_hash = store.files[path].file_hash

            # Update without changing — should be a no-op
            update_file(store, path)
            self.assertEqual(store.files[path].file_hash, old_hash)
        finally:
            os.unlink(path)


class TestFindSourceFiles(unittest.TestCase):

    def test_finds_python_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            py_file = os.path.join(tmpdir, "test.py")
            txt_file = os.path.join(tmpdir, "readme.txt")
            with open(py_file, "w") as file_handle:
                file_handle.write("pass")
            with open(txt_file, "w") as file_handle:
                file_handle.write("hello")

            files = find_source_files(tmpdir)
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].endswith(".py"))

    def test_skips_pycache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "__pycache__")
            os.makedirs(cache_dir)
            cached_file = os.path.join(cache_dir, "test.py")
            with open(cached_file, "w") as file_handle:
                file_handle.write("pass")

            files = find_source_files(tmpdir)
            self.assertEqual(len(files), 0)


if __name__ == "__main__":
    unittest.main()
