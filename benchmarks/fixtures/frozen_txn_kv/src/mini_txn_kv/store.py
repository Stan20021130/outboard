"""High-performance in-memory ACID transactional key-value store.

Implements Snapshot Isolation (MVCC) with Optimistic Concurrency Control (OCC)
for write-write conflict detection at commit time.
"""

from __future__ import annotations

from typing import Any, Dict

__all__ = [
    "TransactionalStore",
    "Transaction",
    "WriteConflictError",
    "TransactionClosedError",
]


class WriteConflictError(Exception):
    """Raised when a transaction commits after a concurrent write-write conflict."""


class TransactionClosedError(Exception):
    """Raised when operating on a transaction that is already closed."""


class Transaction:
    """A snapshot-isolated, optimistic transaction.

    Reads are served from a point-in-time snapshot of the committed store,
    overlaid with the transaction's own local write set (read-your-own-writes).
    Writes are buffered locally until :meth:`commit` succeeds.
    """

    __slots__ = ("_store", "_snapshot", "_version", "_writes", "_deletes", "_closed")

    def __init__(self, store: "TransactionalStore") -> None:
        """Create a new active transaction bound to ``store``.

        Captures the current committed state and version as its snapshot.
        """
        self._store: "TransactionalStore" = store
        self._snapshot: Dict[str, Any] = store._data.copy()
        self._version: int = store._version
        self._writes: Dict[str, Any] = {}
        self._deletes: set[str] = set()
        self._closed: bool = False

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _ensure_open(self) -> None:
        """Raise :class:`TransactionClosedError` if this transaction is closed."""
        if self._closed:
            raise TransactionClosedError("transaction is already closed")

    @property
    def closed(self) -> bool:
        """Whether the transaction has been committed or rolled back."""
        return self._closed

    @property
    def active(self) -> bool:
        """Whether the transaction is still open."""
        return not self._closed

    @staticmethod
    def _check_key(key: str) -> None:
        """Validate that ``key`` is a string, raising :class:`TypeError` otherwise."""
        if not isinstance(key, str):
            raise TypeError(f"key must be str, not {type(key).__name__}")

    # ------------------------------------------------------------------ #
    # Write operations
    # ------------------------------------------------------------------ #
    def set(self, key: str, value: Any) -> None:
        """Buffer ``key`` -> ``value`` in the transaction's local write set."""
        self._ensure_open()
        self._check_key(key)
        self._deletes.discard(key)
        self._writes[key] = value

    def delete(self, key: str) -> bool:
        """Mark ``key`` as deleted locally.

        Returns ``True`` if the key exists in the snapshot or local write set,
        ``False`` otherwise.
        """
        self._ensure_open()
        self._check_key(key)
        if key in self._writes or key in self._snapshot:
            self._writes.pop(key, None)
            self._deletes.add(key)
            return True
        return False

    # ------------------------------------------------------------------ #
    # Read operations
    # ------------------------------------------------------------------ #
    def get(self, key: str, default: Any = None) -> Any:
        """Return the transaction-visible value for ``key``, else ``default``.

        Local writes take precedence, then tombstones, then the snapshot.
        """
        self._ensure_open()
        self._check_key(key)
        if key in self._writes:
            return self._writes[key]
        if key in self._deletes:
            return default
        return self._snapshot.get(key, default)

    def has(self, key: str) -> bool:
        """Return whether ``key`` is visible (present and not deleted)."""
        self._ensure_open()
        if key in self._writes:
            return True
        if key in self._deletes:
            return False
        return key in self._snapshot

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def commit(self) -> None:
        """Atomically apply local writes, or raise on write-write conflict.

        On conflict the transaction is aborted (closed) and
        :class:`WriteConflictError` is raised.
        """
        self._ensure_open()
        store = self._store
        try:
            # Conflict detection: any key we touched must not have been
            # committed by another transaction after our snapshot was taken.
            if store._version != self._version:
                for key in self._writes.keys() | self._deletes:
                    if store._key_version.get(key, 0) > self._version:
                        raise WriteConflictError(
                            f"write-write conflict on key {key!r}"
                        )

            store._apply(self._writes, self._deletes)
        finally:
            self._closed = True
            self._writes.clear()
            self._deletes.clear()

    def rollback(self) -> None:
        """Discard all local modifications and close the transaction."""
        self._ensure_open()
        self._closed = True
        self._writes.clear()
        self._deletes.clear()

    # ------------------------------------------------------------------ #
    # Dunder helpers
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "Transaction":
        """Enter a context manager, returning this transaction."""
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        """Commit on clean exit, roll back on exception."""
        if not self._closed:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        return False

    def __repr__(self) -> str:
        """Return a debug representation of the transaction."""
        state = "closed" if self._closed else "active"
        return (
            f"<Transaction {state} writes={len(self._writes)} "
            f"deletes={len(self._deletes)}>"
        )


class TransactionalStore:
    """In-memory key-value store supporting snapshot-isolated transactions."""

    __slots__ = ("_data", "_version", "_key_version")

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._data: Dict[str, Any] = {}
        self._version: int = 0
        self._key_version: Dict[str, int] = {}

    def begin(self) -> Transaction:
        """Create and return a new active transaction capturing a snapshot."""
        return Transaction(self)

    def dump(self) -> Dict[str, Any]:
        """Return a shallow copy of the globally committed key-values."""
        return self._data.copy()

    # ------------------------------------------------------------------ #
    # Internal commit application
    # ------------------------------------------------------------------ #
    def _apply(self, writes: Dict[str, Any], deletes: set[str]) -> None:
        """Atomically apply a committed write set and tombstone set."""
        if not writes and not deletes:
            return
        self._version += 1
        version = self._version
        for key, value in writes.items():
            self._data[key] = value
            self._key_version[key] = version
        for key in deletes:
            self._data.pop(key, None)
            self._key_version[key] = version
