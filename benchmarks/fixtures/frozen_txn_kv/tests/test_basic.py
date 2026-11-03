"""Basic smoke test for frozen MiniTransactionalKV."""
from mini_txn_kv import TransactionalStore

def test_smoke():
    store = TransactionalStore()
    txn = store.begin()
    txn.set("hello", "world")
    txn.commit()
    assert store.dump() == {"hello": "world"}
