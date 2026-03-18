import logging
import os
import tempfile
import threading
import time

logger = logging.getLogger(__name__)


class TempFileCache:
    def __init__(self, ttl_seconds=300, cleanup_interval=60):
        self.ttl = ttl_seconds
        self.cleanup_interval = cleanup_interval

        self.cache = {}  # key -> (path, timestamp)
        self.pending = set()  # keys being generated
        self.lock = threading.Condition()
        self._stop = False

        # Start background cleanup thread
        self.thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.thread.start()

    def _remove_file(self, path):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    def _cleanup_expired(self):
        now = time.time()
        expired_keys = [k for k, (_, ts) in self.cache.items() if now - ts > self.ttl]

        for key in expired_keys:
            path, _ = self.cache.pop(key, (None, None))
            self._remove_file(path)

    def _cleanup_loop(self):
        while not self._stop:
            with self.lock:
                self._cleanup_expired()
            time.sleep(self.cleanup_interval)

    def stop(self):
        """Stop the cleanup thread and clean up all cached files."""
        self._stop = True
        self.thread.join(timeout=2)
        with self.lock:
            for path, _ in self.cache.values():
                self._remove_file(path)
            self.cache.clear()

    def get(self, key, generator_fn):
        with self.lock:
            # Return cached file if fresh
            if key in self.cache:
                path, ts = self.cache[key]
                # refresh timestamp
                self.cache[key] = (path, time.time())
                return path

            # If another thread is generating this file, wait
            while key in self.pending:
                self.lock.wait()

            # Check again after waiting — another thread may have populated it
            if key in self.cache:
                path, ts = self.cache[key]
                self.cache[key] = (path, time.time())
                return path

            # Mark this key as pending generation
            self.pending.add(key)

        # Outside lock: generate the file
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)

        try:
            generator_fn(path)
        except Exception:
            self._remove_file(path)
            with self.lock:
                self.pending.discard(key)
                self.lock.notify_all()
            raise

        # Store file and notify waiters
        with self.lock:
            self.cache[key] = (path, time.time())
            self.pending.discard(key)
            self.lock.notify_all()

        return path
