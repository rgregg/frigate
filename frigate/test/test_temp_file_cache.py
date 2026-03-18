"""Unit tests for TempFileCache."""

import os
import tempfile
import threading
import time
import unittest

from frigate.transcode.temp_file_cache import TempFileCache


class TestTempFileCache(unittest.TestCase):
    def setUp(self):
        self.cache = TempFileCache(ttl_seconds=2, cleanup_interval=1)

    def tearDown(self):
        self.cache.stop()

    def test_get_creates_and_caches_file(self):
        """Generated file is returned and cached on subsequent calls."""

        def generator(output_path):
            with open(output_path, "w") as f:
                f.write("test content")

        path1 = self.cache.get("key1", generator)
        assert os.path.isfile(path1)
        with open(path1) as f:
            assert f.read() == "test content"

        # Second call should return same path (cached)
        call_count = 0

        def counting_generator(output_path):
            nonlocal call_count
            call_count += 1

        path2 = self.cache.get("key1", counting_generator)
        assert path2 == path1
        assert call_count == 0, "Generator should not be called for cached key"

    def test_different_keys_produce_different_files(self):
        def generator(output_path):
            with open(output_path, "w") as f:
                f.write(output_path)

        path1 = self.cache.get("key1", generator)
        path2 = self.cache.get("key2", generator)
        assert path1 != path2

    def test_expired_entries_are_cleaned_up(self):
        """Files are removed after TTL expires."""

        def generator(output_path):
            with open(output_path, "w") as f:
                f.write("temp")

        path = self.cache.get("expiring", generator)
        assert os.path.isfile(path)

        # Wait for TTL + cleanup interval
        time.sleep(4)
        assert not os.path.isfile(path), "File should be cleaned up after TTL"

    def test_stop_cleans_up_all_files(self):
        """stop() removes all cached files."""
        paths = []

        def generator(output_path):
            with open(output_path, "w") as f:
                f.write("temp")

        for i in range(3):
            paths.append(self.cache.get(f"key{i}", generator))

        for p in paths:
            assert os.path.isfile(p)

        self.cache.stop()

        for p in paths:
            assert not os.path.isfile(p), f"File {p} should be removed after stop()"

    def test_generator_failure_does_not_cache(self):
        """If generator raises, nothing is cached and temp file is cleaned up."""

        def failing_generator(output_path):
            raise ValueError("generation failed")

        with self.assertRaises(ValueError):
            self.cache.get("failing", failing_generator)

        # Key should not be in cache — next call should try generator again
        call_count = 0

        def counting_generator(output_path):
            nonlocal call_count
            call_count += 1
            with open(output_path, "w") as f:
                f.write("success")

        path = self.cache.get("failing", counting_generator)
        assert call_count == 1
        assert os.path.isfile(path)

    def test_concurrent_requests_for_same_key(self):
        """Multiple threads requesting the same key only generate once."""
        generation_count = 0
        lock = threading.Lock()

        def slow_generator(output_path):
            nonlocal generation_count
            with lock:
                generation_count += 1
            time.sleep(0.5)
            with open(output_path, "w") as f:
                f.write("generated")

        results = [None] * 5
        threads = []
        for i in range(5):

            def worker(idx=i):
                results[idx] = self.cache.get("concurrent", slow_generator)

            t = threading.Thread(target=worker)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert generation_count == 1, f"Expected 1 generation, got {generation_count}"
        # All threads should get the same path
        assert all(r == results[0] for r in results)

    def test_mkstemp_suffix(self):
        """Generated temp files have .mp4 suffix."""

        def generator(output_path):
            assert output_path.endswith(".mp4")
            with open(output_path, "w") as f:
                f.write("test")

        path = self.cache.get("suffix_test", generator)
        assert path.endswith(".mp4")
