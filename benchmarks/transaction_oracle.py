"""Independent Acceptance Oracle Grader for MiniTransactionalKV greenfield evaluation.
Tests ACID transaction semantics:
  1. Atomicity & Rollback
  2. Snapshot Isolation (No Dirty Reads, Repeatable Reads)
  3. Read-Your-Own-Writes
  4. Write-Write Conflict Detection (OCC)
  5. Tombstone Deletions & Rollback
  6. Lifecycle guards (TransactionClosedError)
"""
import sys
import pytest

def get_txn_store_cls():
    for mod in list(sys.modules.keys()):
        if "mini_txn_kv" in mod:
            del sys.modules[mod]
    try:
        from mini_txn_kv.store import TransactionalStore, WriteConflictError, TransactionClosedError
        return TransactionalStore, WriteConflictError, TransactionClosedError
    except ImportError:
        try:
            from src.mini_txn_kv.store import TransactionalStore, WriteConflictError, TransactionClosedError
            return TransactionalStore, WriteConflictError, TransactionClosedError
        except ImportError:
            raise ImportError("Could not find TransactionalStore in mini_txn_kv.store or src.mini_txn_kv.store")

def test_01_basic_committed_read_write():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    # A transaction commits basic data
    txn = db.begin()
    txn.set("k1", "v1")
    txn.set("k2", 100)
    txn.commit()
    # Direct read or new transaction can read committed values
    t2 = db.begin()
    assert t2.get("k1") == "v1"
    assert t2.get("k2") == 100
    assert t2.get("k3") is None
    t2.commit()

def test_02_isolation_no_dirty_read():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    t1 = db.begin()
    t1.set("secret", "uncommitted_value")
    # Another transaction t2 must NOT see t1's uncommitted write
    t2 = db.begin()
    assert t2.get("secret") is None
    t1.commit()
    # After t1 commits, a NEW transaction t3 can see it
    t3 = db.begin()
    assert t3.get("secret") == "uncommitted_value"
    t2.commit()
    t3.commit()

def test_03_read_your_own_writes():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    t1 = db.begin()
    t1.set("x", 42)
    assert t1.get("x") == 42
    t1.set("x", 99)
    assert t1.get("x") == 99
    t1.commit()

def test_04_rollback_atomicity():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    # Setup initial state
    init_txn = db.begin()
    init_txn.set("balance", 100)
    init_txn.commit()

    # Rollback transaction
    t = db.begin()
    t.set("balance", 500)
    t.set("new_key", "temporary")
    t.delete("balance")
    t.rollback()

    # State must be unchanged
    t_verify = db.begin()
    assert t_verify.get("balance") == 100
    assert t_verify.get("new_key") is None
    t_verify.commit()

def test_05_write_write_conflict_detection():
    Store, WriteConflictError, _ = get_txn_store_cls()
    db = Store()
    init_txn = db.begin()
    init_txn.set("account", 100)
    init_txn.commit()

    # Two concurrent transactions start with initial snapshot
    t1 = db.begin()
    t2 = db.begin()

    t1.set("account", 150)
    t2.set("account", 200)

    # t1 commits first -> succeeds
    t1.commit()

    # t2 attempts to commit -> must detect write conflict and raise WriteConflictError
    with pytest.raises(WriteConflictError):
        t2.commit()

    # Final committed state must be t1's value
    t3 = db.begin()
    assert t3.get("account") == 150
    t3.commit()

def test_06_concurrent_independent_writes_succeed():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    t1 = db.begin()
    t2 = db.begin()

    t1.set("key_a", "val_a")
    t2.set("key_b", "val_b")

    t1.commit()
    t2.commit()  # No conflict since keys are disjoint

    t3 = db.begin()
    assert t3.get("key_a") == "val_a"
    assert t3.get("key_b") == "val_b"
    t3.commit()

def test_07_transaction_closed_guards():
    Store, _, TransactionClosedError = get_txn_store_cls()
    db = Store()
    t1 = db.begin()
    t1.set("k", "v")
    t1.commit()

    # Operating on already committed transaction raises TransactionClosedError
    with pytest.raises(TransactionClosedError):
        t1.set("k", "v2")
    with pytest.raises(TransactionClosedError):
        t1.get("k")
    with pytest.raises(TransactionClosedError):
        t1.commit()
    with pytest.raises(TransactionClosedError):
        t1.rollback()

    t2 = db.begin()
    t2.rollback()
    with pytest.raises(TransactionClosedError):
        t2.get("k")

def test_08_tombstone_delete_and_commit():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    init = db.begin()
    init.set("item", "exists")
    init.commit()

    t1 = db.begin()
    assert t1.has("item") is True
    assert t1.delete("item") is True
    assert t1.has("item") is False
    assert t1.get("item") is None

    # Before commit, another concurrent txn still sees it (Repeatable Read)
    t2 = db.begin()
    assert t2.get("item") == "exists"

    t1.commit()
    # After commit, new txn sees item is deleted
    t3 = db.begin()
    assert t3.get("item") is None
    assert t3.has("item") is False
    t2.commit()
    t3.commit()

def test_09_tombstone_delete_and_rollback():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    init = db.begin()
    init.set("safe_key", 999)
    init.commit()

    t1 = db.begin()
    t1.delete("safe_key")
    assert t1.get("safe_key") is None
    t1.rollback()

    # Rollback restores the deleted key
    t2 = db.begin()
    assert t2.get("safe_key") == 999
    t2.commit()

def test_10_repeatable_read_snapshot():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    init = db.begin()
    init.set("val", 1)
    init.commit()

    # t1 starts
    t1 = db.begin()
    assert t1.get("val") == 1

    # Concurrent t2 updates and commits
    t2 = db.begin()
    t2.set("val", 2)
    t2.set("brand_new", "hello")
    t2.commit()

    # t1 MUST still read its original snapshot (val=1, brand_new=None)
    assert t1.get("val") == 1
    assert t1.get("brand_new") is None
    t1.commit()

def test_11_delete_write_conflict():
    Store, WriteConflictError, _ = get_txn_store_cls()
    db = Store()
    init = db.begin()
    init.set("target", "orig")
    init.commit()

    t1 = db.begin()
    t2 = db.begin()

    t1.delete("target")
    t2.set("target", "modified")

    t1.commit()
    # t2 tried to modify target which was deleted by concurrent committed t1
    with pytest.raises(WriteConflictError):
        t2.commit()

def test_12_type_error_key():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    t = db.begin()
    with pytest.raises(TypeError):
        t.set(123, "val")
    with pytest.raises(TypeError):
        t.get(None)
    with pytest.raises(TypeError):
        t.delete([])
    t.rollback()

def test_13_empty_transaction():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    t = db.begin()
    t.commit()  # empty transaction commit is a valid no-op

def test_14_has_and_delete_nonexistent():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    t = db.begin()
    assert t.has("not_there") is False
    assert t.delete("not_there") is False
    t.commit()

def test_15_dump_state():
    Store, _, _ = get_txn_store_cls()
    db = Store()
    t1 = db.begin()
    t1.set("a", 1)
    t1.set("b", 2)
    t1.commit()
    assert db.dump() == {"a": 1, "b": 2}
