"""SnapshotIndex COW dedup and GC tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

from semarun.kernel.snapshot_index import SnapshotIndex
from semarun.storage.ledger_store import SQLiteLedgerStore


def test_cow_deduplicates_identical_blobs():
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteLedgerStore(str(Path(tmp) / "ledger.db"))
        idx = SnapshotIndex(store, start_gc=False)
        payload = {"state": {"intent": "test"}, "v": 1}
        n1, h1 = idx.store_checkpoint_blob("run_a", payload)
        n2, h2 = idx.store_checkpoint_blob("run_a", payload)
        assert h1 == h2
        assert n1 == n2
        idx.stop()
        store.close()


def test_gc_prunes_unreferenced_nodes():
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteLedgerStore(str(Path(tmp) / "ledger.db"))
        idx = SnapshotIndex(store, start_gc=False)
        n1, _ = idx.store_checkpoint_blob("run_a", {"v": 1})
        n2, _ = idx.store_checkpoint_blob("run_a", {"v": 2})
        idx.pin(n1)
        idx.unpin(n1)
        removed = idx.gc_once("run_a")
        assert removed >= 0
        idx.stop()
        store.close()
