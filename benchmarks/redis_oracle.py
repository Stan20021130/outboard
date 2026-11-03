"""Independent Acceptance Oracle Grader for Mini-Redis Greenfield Evaluation.
Tests a comprehensive, multi-module in-memory Redis-compatible engine & RESP protocol:
  1. RESP Encoding (Simple Strings, Errors, Integers, Bulk Strings, Nulls, Arrays)
  2. RESP Decoding (Streaming parsing, multi-command separation, type mapping)
  3. String Operations (SET, GET, DEL, MSET, MGET, INCR, DECR, SET NX/XX)
  4. TTL & Expiration (EXPIRE, TTL, PERSIST, millisecond accuracy, passive/active eviction)
  5. Hash Operations (HSET, HGET, HDEL, HGETALL, HEXISTS, HLEN)
  6. List Operations (LPUSH, RPUSH, LPOP, RPOP, LRANGE, LLEN)
  7. Type Enforcement (Wrong type operation raises appropriate Redis ERR)
  8. Command Dispatch & Pipeline (RESP bytes in -> RESP bytes out)
"""
import sys
import time
from pathlib import Path
import pytest

def get_redis_modules():
    for mod in list(sys.modules.keys()):
        if "mini_redis" in mod or mod.startswith("mini_redis."):
            del sys.modules[mod]
    server_cls, storage_cls, parser_cls = None, None, None
    try:
        from mini_redis.resp import RESPParser
        parser_cls = RESPParser
    except Exception:
        pass
    try:
        from mini_redis.storage import RedisStorage
        storage_cls = RedisStorage
    except Exception:
        pass
    try:
        from mini_redis.server import RedisServer
        server_cls = RedisServer
    except Exception:
        pass
    return server_cls, storage_cls, parser_cls

# =========================================================================
# 1. RESP Protocol Encoding & Decoding
# =========================================================================
def test_01_resp_encode_primitive_types():
    _, _, RESPParser = get_redis_modules()
    parser = RESPParser()
    assert parser.encode_simple_string("OK") == b"+OK\r\n"
    assert parser.encode_error("ERR unknown command") == b"-ERR unknown command\r\n"
    assert parser.encode_integer(100) == b":100\r\n"
    assert parser.encode_integer(-5) == b":-5\r\n"

def test_02_resp_encode_bulk_string_and_null():
    _, _, RESPParser = get_redis_modules()
    parser = RESPParser()
    assert parser.encode_bulk_string("hello") == b"$5\r\nhello\r\n"
    assert parser.encode_bulk_string("") == b"$0\r\n\r\n"
    assert parser.encode_bulk_string(None) == b"$-1\r\n"

def test_03_resp_encode_arrays():
    _, _, RESPParser = get_redis_modules()
    parser = RESPParser()
    # Array of bulk strings
    arr = ["SET", "key", "val"]
    encoded = parser.encode_array(arr)
    expected = b"*3\r\n$3\r\nSET\r\n$3\r\nkey\r\n$3\r\nval\r\n"
    assert encoded == expected
    assert parser.encode_array([]) == b"*0\r\n"
    assert parser.encode_array(None) == b"*-1\r\n"

def test_04_resp_decode_simple_and_integer():
    _, _, RESPParser = get_redis_modules()
    parser = RESPParser()
    val, rest = parser.decode(b"+PONG\r\n")
    assert val == "PONG"
    assert rest == b""
    val_int, rest_int = parser.decode(b":42\r\nEXTRA")
    assert val_int == 42
    assert rest_int == b"EXTRA"

def test_05_resp_decode_bulk_string():
    _, _, RESPParser = get_redis_modules()
    parser = RESPParser()
    val, rest = parser.decode(b"$5\r\nworld\r\n")
    assert val == "world" or val == b"world"
    assert rest == b""
    val_null, rest_null = parser.decode(b"$-1\r\n")
    assert val_null is None

def test_06_resp_decode_array_and_multicommand():
    _, _, RESPParser = get_redis_modules()
    parser = RESPParser()
    raw = b"*3\r\n$3\r\nSET\r\n$1\r\na\r\n$1\r\nb\r\n"
    val, rest = parser.decode(raw)
    assert len(val) == 3
    # items can be decoded as str or bytes
    str_items = [x.decode("utf-8") if isinstance(x, bytes) else str(x) for x in val]
    assert str_items == ["SET", "a", "b"]
    assert rest == b""

# =========================================================================
# 2. String Storage Operations
# =========================================================================
def test_07_strings_get_set_del():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    assert store.get("key1") is None
    assert store.set("key1", "val1") is True
    assert store.get("key1") == "val1"
    assert store.delete("key1") == 1
    assert store.get("key1") is None
    assert store.delete("key1") == 0

def test_08_strings_mset_mget():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    assert store.mset({"k1": "v1", "k2": "v2", "k3": "v3"}) is True
    vals = store.mget(["k1", "k2", "nonexistent", "k3"])
    assert vals == ["v1", "v2", None, "v3"]

def test_09_strings_incr_decr():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    assert store.incrby("counter", 1) == 1
    assert store.incrby("counter", 5) == 6
    assert store.decrby("counter", 2) == 4
    assert store.get("counter") == "4" or store.get("counter") == 4

    store.set("bad_num", "abc")
    with pytest.raises((ValueError, TypeError)):
        store.incrby("bad_num", 1)

def test_10_strings_set_options_nx_xx():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    # NX: only set if not exists
    assert store.set("uniq", "first", nx=True) is True
    assert store.set("uniq", "second", nx=True) is False
    assert store.get("uniq") == "first"

    # XX: only set if already exists
    assert store.set("another", "val", xx=True) is False
    assert store.get("another") is None
    store.set("another", "old")
    assert store.set("another", "new", xx=True) is True
    assert store.get("another") == "new"

# =========================================================================
# 3. TTL & Expiration
# =========================================================================
def test_11_ttl_and_expire():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    store.set("temp", "val")
    assert store.ttl("temp") == -1  # no TTL

    # Set expire in 0.2 seconds
    assert store.expire("temp", 0.2) is True
    assert store.ttl("temp") >= 0
    assert store.get("temp") == "val"

    time.sleep(0.3)
    # Key should now be expired
    assert store.get("temp") is None
    assert store.ttl("temp") == -2  # does not exist

def test_12_persist_clears_ttl():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    store.set("foo", "bar", ex=10)
    assert store.ttl("foo") > 0
    assert store.persist("foo") is True
    assert store.ttl("foo") == -1

# =========================================================================
# 4. Hash Storage Operations
# =========================================================================
def test_13_hash_hset_hget_hexists():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    assert store.hset("user:1", "name", "Alice") == 1
    assert store.hset("user:1", "age", "30") == 1
    assert store.hget("user:1", "name") == "Alice"
    assert store.hget("user:1", "missing") is None
    assert store.hexists("user:1", "name") is True
    assert store.hexists("user:1", "email") is False

def test_14_hash_hgetall_hdel_hlen():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    store.hset("cfg", "theme", "dark")
    store.hset("cfg", "font", "sans")
    store.hset("cfg", "lang", "zh")

    assert store.hlen("cfg") == 3
    all_fields = store.hgetall("cfg")
    assert all_fields == {"theme": "dark", "font": "sans", "lang": "zh"}
    assert store.hdel("cfg", "font") == 1
    assert store.hlen("cfg") == 2
    assert store.hget("cfg", "font") is None

# =========================================================================
# 5. List Storage Operations
# =========================================================================
def test_15_list_lpush_rpush_llen():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    assert store.lpush("mylist", "b", "a") == 2  # pushed b then a -> [a, b]
    assert store.rpush("mylist", "c") == 3       # [a, b, c]
    assert store.llen("mylist") == 3
    assert store.lrange("mylist", 0, -1) == ["a", "b", "c"]

def test_16_list_lpop_rpop():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    store.rpush("queue", "task1", "task2", "task3")
    assert store.lpop("queue") == "task1"
    assert store.rpop("queue") == "task3"
    assert store.llen("queue") == 1
    assert store.lpop("queue") == "task2"
    assert store.lpop("queue") is None

# =========================================================================
# 6. Type Enforcement & Multi-Type Isolation
# =========================================================================
def test_17_type_mismatch_error():
    _, RedisStorage, _ = get_redis_modules()
    store = RedisStorage()
    store.set("str_key", "string_value")
    # Calling HSET or LPUSH on string key should raise TypeError/ValueError
    with pytest.raises((TypeError, ValueError)):
        store.hset("str_key", "field", "value")
    with pytest.raises((TypeError, ValueError)):
        store.lpush("str_key", "val")

# =========================================================================
# 7. Server & Command Dispatch
# =========================================================================
def test_18_command_dispatch_ping_echo():
    RedisServer, _, _ = get_redis_modules()
    server = RedisServer()
    assert server.execute_command("PING") == "PONG"
    assert server.execute_command("ECHO", "hello world") == "hello world"

def test_19_command_pipeline_resp_integration():
    RedisServer, _, RESPParser = get_redis_modules()
    server = RedisServer()
    parser = RESPParser()

    # Client sends RESP: SET mykey 12345
    raw_cmd = parser.encode_array(["SET", "mykey", "12345"])
    response_bytes = server.handle_raw_resp(raw_cmd)
    assert response_bytes == b"+OK\r\n"

    # Client sends RESP: GET mykey
    raw_get = parser.encode_array(["GET", "mykey"])
    get_res_bytes = server.handle_raw_resp(raw_get)
    assert get_res_bytes == b"$5\r\n12345\r\n"

def test_20_flushdb_and_keys():
    RedisServer, _, _ = get_redis_modules()
    server = RedisServer()
    server.execute_command("SET", "alpha", "1")
    server.execute_command("SET", "beta", "2")
    server.execute_command("HSET", "gamma", "f", "3")
    
    keys = server.execute_command("KEYS", "*")
    assert set(keys) == {"alpha", "beta", "gamma"}

    server.execute_command("FLUSHDB")
    assert server.execute_command("KEYS", "*") == []
    assert server.execute_command("GET", "alpha") is None
