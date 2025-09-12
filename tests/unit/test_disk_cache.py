import threading
import time

import pytest

from src.preprocessing.adapters.cache.disk_cache import CacheError, DiskCache


def test_put_get_ttl_and_size_guard(tmp_path):
    db = tmp_path / "cache.sqlite"
    c = DiskCache(str(db), max_value_bytes=64)
    c.open()

    assert c.get("k1") is None
    c.put("k1", "v1", ttl_seconds=1)
    assert c.get("k1") == "v1"
    # Expire
    time.sleep(1.2)
    assert c.get("k1") is None

    # Size guard
    with pytest.raises(CacheError):
        c.put("big", "x" * 1000)


def test_lru_eviction_deterministic(tmp_path):
    db = tmp_path / "cache.sqlite"
    c = DiskCache(str(db), max_items=3)
    c.open()
    for i in range(5):
        c.put(f"k{i}", f"v{i}")
    stats = c.stats()
    assert stats["items"] <= 3


def test_concurrent_put_get(tmp_path):
    db = tmp_path / "cache.sqlite"

    def writer():
        c = DiskCache(str(db))
        c.open()
        for i in range(50):
            c.put(f"k{i}", f"v{i}")

    def reader():
        c = DiskCache(str(db))
        c.open()
        hits = 0
        for i in range(50):
            v = c.get(f"k{i}")
            if v is not None:
                hits += 1
        return hits

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # Open a new connection to count items
    c3 = DiskCache(str(db))
    c3.open()
    assert c3.stats()["items"] >= 1
