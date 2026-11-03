"""Independent Acceptance Oracle Grader for MiniDocDB greenfield evaluation.
Tests a comprehensive, multi-module embedded document database:
  1. CRUD (insert_one, insert_many, find, find_one, update_one, update_many, delete_one, delete_many, count)
  2. MongoDB-style query filters ($eq, $ne, $gt, $gte, $lt, $lte, $in, $nin, $exists, $and, $or)
  3. Update operators ($set, $unset, $inc)
  4. Indexing (create_index, unique index constraint with DuplicateKeyError, index-assisted lookup)
  5. Persistence & Recovery (dump to disk and reload in a new Database instance)
  6. Multi-collection management (list_collections, drop_collection, db['col'])
"""
import sys
import tempfile
from pathlib import Path
import pytest

def get_db_cls():
    for mod in list(sys.modules.keys()):
        if "mini_docdb" in mod:
            del sys.modules[mod]
    try:
        from mini_docdb import Database, Collection, DuplicateKeyError
        return Database, Collection, DuplicateKeyError
    except ImportError:
        try:
            from src.mini_docdb import Database, Collection, DuplicateKeyError
            return Database, Collection, DuplicateKeyError
        except ImportError:
            raise ImportError("Could not find Database in mini_docdb or src.mini_docdb")

def test_01_insert_and_find_one():
    Database, _, _ = get_db_cls()
    db = Database()
    users = db.get_collection("users")
    doc_id = users.insert_one({"name": "Alice", "age": 30, "city": "Beijing"})
    assert isinstance(doc_id, str)
    doc = users.find_one({"name": "Alice"})
    assert doc is not None
    assert doc["_id"] == doc_id
    assert doc["age"] == 30

def test_02_insert_many_and_count():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["items"]
    ids = col.insert_many([{"item": "apple", "qty": 10}, {"item": "banana", "qty": 20}, {"item": "orange", "qty": 30}])
    assert len(ids) == 3
    assert col.count() == 3
    assert col.count({"item": "banana"}) == 1

def test_03_query_comparison_operators():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["scores"]
    col.insert_many([{"name": "A", "score": 60}, {"name": "B", "score": 85}, {"name": "C", "score": 95}, {"name": "D", "score": 40}])
    
    # $gt and $lt
    res_gt = col.find({"score": {"$gt": 80}})
    assert len(res_gt) == 2
    names_gt = {d["name"] for d in res_gt}
    assert names_gt == {"B", "C"}

    res_lte = col.find({"score": {"$lte": 60}})
    assert len(res_lte) == 2
    names_lte = {d["name"] for d in res_lte}
    assert names_lte == {"A", "D"}

def test_04_query_in_and_nin():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["tags"]
    col.insert_many([{"tag": "python"}, {"tag": "rust"}, {"tag": "go"}, {"tag": "java"}])
    res = col.find({"tag": {"$in": ["python", "rust", "cpp"]}})
    assert len(res) == 2
    res_nin = col.find({"tag": {"$nin": ["python", "rust"]}})
    assert len(res_nin) == 2

def test_05_query_exists():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["profile"]
    col.insert_one({"user": "u1", "email": "u1@test.com"})
    col.insert_one({"user": "u2"})  # no email
    
    has_email = col.find({"email": {"$exists": True}})
    assert len(has_email) == 1
    assert has_email[0]["user"] == "u1"

    no_email = col.find({"email": {"$exists": False}})
    assert len(no_email) == 1
    assert no_email[0]["user"] == "u2"

def test_06_query_logical_or_and():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["products"]
    col.insert_many([
        {"p": "laptop", "price": 1000, "cat": "tech"},
        {"p": "mouse", "price": 20, "cat": "tech"},
        {"p": "apple", "price": 2, "cat": "food"},
    ])
    # $or query: cat == 'food' OR price >= 1000
    res = col.find({"$or": [{"cat": "food"}, {"price": {"$gte": 1000}}]})
    assert len(res) == 2
    names = {d["p"] for d in res}
    assert names == {"laptop", "apple"}

def test_07_update_one_set():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["users"]
    col.insert_one({"name": "Bob", "status": "pending"})
    updated = col.update_one({"name": "Bob"}, {"$set": {"status": "active", "level": 1}})
    assert updated is True
    doc = col.find_one({"name": "Bob"})
    assert doc["status"] == "active"
    assert doc["level"] == 1

def test_08_update_inc_and_unset():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["counters"]
    col.insert_one({"k": "v1", "views": 10, "temp": "remove_me"})
    col.update_one({"k": "v1"}, {"$inc": {"views": 5}, "$unset": {"temp": ""}})
    doc = col.find_one({"k": "v1"})
    assert doc["views"] == 15
    assert "temp" not in doc

def test_09_update_many():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["logs"]
    col.insert_many([{"level": "info", "handled": False}, {"level": "info", "handled": False}, {"level": "error", "handled": False}])
    count = col.update_many({"level": "info"}, {"$set": {"handled": True}})
    assert count == 2
    assert col.count({"handled": True}) == 2
    assert col.count({"handled": False}) == 1

def test_10_delete_one_and_many():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["orders"]
    col.insert_many([{"id": 1, "state": "cancelled"}, {"id": 2, "state": "cancelled"}, {"id": 3, "state": "paid"}])
    del_one = col.delete_one({"state": "cancelled"})
    assert del_one is True
    assert col.count() == 2
    del_many = col.delete_many({"state": "cancelled"})
    assert del_many == 1
    assert col.count() == 1
    assert col.find_one()["id"] == 3

def test_11_sort_skip_limit():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["ranking"]
    col.insert_many([{"n": 10}, {"n": 50}, {"n": 30}, {"n": 20}, {"n": 40}])
    # Sort ascending, skip 1, limit 2 -> [20, 30]
    res = col.find(sort=("n", True), skip=1, limit=2)
    assert len(res) == 2
    assert res[0]["n"] == 20
    assert res[1]["n"] == 30

def test_12_unique_index_constraint():
    Database, _, DuplicateKeyError = get_db_cls()
    db = Database()
    col = db["accounts"]
    col.create_index("username", unique=True)
    col.insert_one({"username": "john"})
    with pytest.raises(DuplicateKeyError):
        col.insert_one({"username": "john"})

def test_13_index_lookup_and_update():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["records"]
    col.create_index("code")
    col.insert_one({"code": "C101", "name": "Chemistry"})
    col.insert_one({"code": "M201", "name": "Math"})
    doc = col.find_one({"code": "M201"})
    assert doc is not None and doc["name"] == "Math"
    # Update indexed field
    col.update_one({"code": "M201"}, {"$set": {"code": "M202"}})
    assert col.find_one({"code": "M201"}) is None
    assert col.find_one({"code": "M202"})["name"] == "Math"

def test_14_index_cleanup_on_delete():
    Database, _, DuplicateKeyError = get_db_cls()
    db = Database()
    col = db["devices"]
    col.create_index("sn", unique=True)
    col.insert_one({"sn": "SN001"})
    col.delete_one({"sn": "SN001"})
    # Re-inserting same unique key should succeed after deletion
    new_id = col.insert_one({"sn": "SN001", "renewed": True})
    assert new_id is not None
    assert col.count() == 1

def test_15_persistence_and_reload():
    Database, _, _ = get_db_cls()
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "mydb"
        # Create and persist data
        db1 = Database(path=str(db_path))
        c1 = db1.get_collection("notes")
        c1.insert_one({"title": "Note 1", "content": "Important memo"})
        c1.create_index("title", unique=True)
        db1.flush()
        db1.close()

        # Reload in a fresh database instance
        db2 = Database(path=str(db_path))
        assert "notes" in db2.list_collections()
        c2 = db2.get_collection("notes")
        doc = c2.find_one({"title": "Note 1"})
        assert doc is not None
        assert doc["content"] == "Important memo"
        # Unique index constraint must persist
        _, _, DuplicateKeyError = get_db_cls()
        with pytest.raises(DuplicateKeyError):
            c2.insert_one({"title": "Note 1", "content": "Conflict duplicate"})
        db2.close()

def test_16_multi_collections_and_drop():
    Database, _, _ = get_db_cls()
    db = Database()
    c1 = db["col1"]
    c2 = db["col2"]
    c1.insert_one({"a": 1})
    c2.insert_one({"b": 2})
    cols = db.list_collections()
    assert "col1" in cols and "col2" in cols
    assert db.drop_collection("col1") is True
    assert "col1" not in db.list_collections()
    assert db["col2"].count() == 1

def test_17_context_manager_support():
    Database, _, _ = get_db_cls()
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "ctx_db"
        with Database(path=str(db_path)) as db:
            db["items"].insert_one({"x": 100})
        # After exit, check it was flushed
        with Database(path=str(db_path)) as db2:
            assert db2["items"].find_one({"x": 100}) is not None

def test_18_immutable_id():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["items"]
    doc_id = col.insert_one({"val": 1})
    # Attempt to modify _id should be ignored or forbidden
    col.update_one({"_id": doc_id}, {"$set": {"_id": "hacked", "val": 2}})
    doc = col.find_one({"_id": doc_id})
    assert doc is not None
    assert doc["_id"] == doc_id
    assert doc["val"] == 2

def test_19_empty_and_no_match_operations():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["empty_col"]
    assert col.count() == 0
    assert col.find() == []
    assert col.find_one({"non_existent": True}) is None
    assert col.update_one({"non_existent": True}, {"$set": {"a": 1}}) is False
    assert col.update_many({"non_existent": True}, {"$set": {"a": 1}}) == 0
    assert col.delete_one({"non_existent": True}) is False
    assert col.delete_many({"non_existent": True}) == 0

def test_20_nested_field_query():
    Database, _, _ = get_db_cls()
    db = Database()
    col = db["nested"]
    col.insert_one({"user": {"profile": {"city": "Shanghai"}}, "active": True})
    col.insert_one({"user": {"profile": {"city": "Shenzhen"}}, "active": False})
    res = col.find({"user.profile.city": "Shanghai"})
    assert len(res) == 1
    assert res[0]["active"] is True
