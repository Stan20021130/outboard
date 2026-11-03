"""Independent Acceptance Oracle Grader for MiniCacheKV greenfield evaluation.
This test suite is NOT revealed to agents during construction.
It acts as the strict external ground-truth oracle.
"""
import sys
import time
import pytest

def get_cache_cls():
    # Dynamically import CacheStore from the workspace
    for mod in list(sys.modules.keys()):
        if "cache_kv" in mod:
            del sys.modules[mod]
    try:
        from cache_kv.cache import CacheStore
        return CacheStore
    except ImportError:
        try:
            from src.cache_kv.cache import CacheStore
            return CacheStore
        except ImportError:
            raise ImportError("Could not find CacheStore in cache_kv.cache or src.cache_kv.cache")

def test_01_basic_set_get():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=5)
    cache.set("a", 100)
    assert cache.get("a") == 100
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 0
    assert stats["size"] == 1

def test_02_default_value_and_misses():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=5)
    assert cache.get("non_existent") is None
    assert cache.get("non_existent", default="fallback") == "fallback"
    stats = cache.stats()
    assert stats["misses"] == 2
    assert stats["hits"] == 0

def test_03_type_error_key():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=5)
    with pytest.raises(TypeError):
        cache.set(123, "val")  # key must be str
    with pytest.raises(TypeError):
        cache.get(None)

def test_04_invalid_ttl():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=5)
    with pytest.raises(ValueError):
        cache.set("a", "val", ttl=0)
    with pytest.raises(ValueError):
        cache.set("a", "val", ttl=-1.5)

def test_05_ttl_expiration():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=5)
    cache.set("temp", "data", ttl=0.04)
    assert cache.get("temp") == "data"
    time.sleep(0.06)
    assert cache.get("temp") is None  # expired
    assert cache.has("temp") is False
    stats = cache.stats()
    assert stats["misses"] == 1

def test_06_has_does_not_affect_lru_or_stats():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=2)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.has("a") is True
    stats_before = cache.stats()
    assert stats_before["hits"] == 0
    assert stats_before["misses"] == 0
    # Now insert 'c'. Since 'has("a")' should not make 'a' most recently used,
    # 'a' was inserted before 'b', so 'a' should be evicted.
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3

def test_07_delete():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=5)
    cache.set("k", "v")
    assert cache.delete("k") is True
    assert cache.delete("k") is False  # already deleted
    assert cache.get("k") is None

def test_08_lru_eviction_order():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=3)
    cache.set("k1", 1)
    cache.set("k2", 2)
    cache.set("k3", 3)
    # Access k1 to make k2 the least recently used
    assert cache.get("k1") == 1
    # Insert k4, should evict k2
    cache.set("k4", 4)
    assert cache.get("k2") is None
    assert cache.get("k1") == 1
    assert cache.get("k3") == 3
    assert cache.get("k4") == 4
    assert cache.stats()["evictions"] == 1

def test_09_expired_priority_eviction():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=2)
    cache.set("k1", 1, ttl=0.03)  # will expire
    cache.set("k2", 2, ttl=10.0)  # active
    time.sleep(0.05)  # k1 is now expired
    # k2 was inserted after k1, but k1 is expired.
    # Inserting k3 should evict the expired k1, NOT k2.
    cache.set("k3", 3)
    assert cache.get("k2") == 2
    assert cache.get("k3") == 3
    assert cache.get("k1") is None

def test_10_cleanup_expired():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=5)
    cache.set("e1", 1, ttl=0.03)
    cache.set("e2", 2, ttl=0.03)
    cache.set("alive", 3, ttl=10.0)
    time.sleep(0.05)
    cleaned = cache.cleanup_expired()
    assert cleaned == 2
    assert cache.stats()["size"] == 1
    assert cache.stats()["expired"] == 2

def test_11_clear():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=5)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")
    cache.clear()
    assert cache.stats()["size"] == 0
    assert cache.get("a") is None
    assert cache.stats()["hits"] == 1  # historical stats preserved

def test_12_dump_dict():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=5)
    cache.set("a", 10)
    cache.set("b", 20, ttl=0.03)
    time.sleep(0.05)
    dumped = cache.dump_dict()
    assert dumped == {"a": 10}  # expired 'b' is excluded

def test_13_load_dict():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=2)
    cache.load_dict({"x": 1, "y": 2, "z": 3})
    assert cache.stats()["size"] == 2
    # Should contain last 2 entries
    assert cache.get("x") is None
    assert cache.get("y") == 2
    assert cache.get("z") == 3

def test_14_invalid_max_size():
    CacheStore = get_cache_cls()
    with pytest.raises(ValueError):
        CacheStore(max_size=0)
    with pytest.raises(ValueError):
        CacheStore(max_size=-5)

def test_15_update_existing_key():
    CacheStore = get_cache_cls()
    cache = CacheStore(max_size=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("a", 100)  # updates existing key
    assert cache.stats()["size"] == 2
    assert cache.stats()["evictions"] == 0
    # Now 'b' is least recently used because 'a' was updated
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 100
    assert cache.get("c") == 3
