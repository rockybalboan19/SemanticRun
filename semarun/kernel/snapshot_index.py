"""Copy-on-write snapshot index tree with background GC (DeltaBox-style)."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import TYPE_CHECKING, Any

from semarun.models.state import new_id

if TYPE_CHECKING:
    from semarun.storage.ledger_store import LedgerStorage


class SnapshotIndex:
    """Content-addressed incremental checkpoint storage."""

    def __init__(
        self,
        storage: LedgerStorage,
        *,
        gc_interval_sec: float = 30.0,
        start_gc: bool = True,
    ) -> None:
        self._storage = storage
        self._gc_interval = gc_interval_sec
        self._active_roots: set[str] = set()
        self._stop_gc = threading.Event()
        self._gc_thread: threading.Thread | None = None
        if start_gc:
            self._gc_thread = threading.Thread(target=self._gc_loop, daemon=True)
            self._gc_thread.start()

    def store_checkpoint_blob(self, run_id: str, payload: dict[str, Any]) -> tuple[str, str]:
        """Return (node_id, content_hash). Reuses blob if content unchanged."""
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if self._storage.get_blob(content_hash) is None:
            self._storage.put_blob(content_hash, raw.encode("utf-8"))
        node_id = new_id("snap")
        parent_id = self._latest_node_for_run(run_id)
        if parent_id:
            parent = self._storage.get_snapshot_node(parent_id)
            if parent and parent.get("content_hash") == content_hash:
                self._storage.increment_snapshot_ref(parent_id)
                self._active_roots.add(parent_id)
                return parent_id, content_hash
        self._storage.put_snapshot_node(node_id, parent_id, content_hash, run_id)
        self._storage.increment_snapshot_ref(node_id)
        self._active_roots.add(node_id)
        return node_id, content_hash

    def load_checkpoint_blob(self, node_id: str) -> dict[str, Any] | None:
        node = self._storage.get_snapshot_node(node_id)
        if node is None:
            return None
        raw = self._storage.get_blob(node["content_hash"])
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8"))

    def pin(self, node_id: str) -> None:
        self._active_roots.add(node_id)
        self._storage.increment_snapshot_ref(node_id)

    def unpin(self, node_id: str) -> None:
        self._active_roots.discard(node_id)
        self._storage.decrement_snapshot_ref(node_id)

    def stop(self) -> None:
        self._stop_gc.set()
        if self._gc_thread and self._gc_thread.is_alive():
            self._gc_thread.join(timeout=2.0)

    def gc_once(self, run_id: str | None = None) -> int:
        """Prune unreachable snapshot subtrees. Returns nodes removed."""
        reachable = set(self._active_roots)
        nodes = self._storage.list_snapshot_nodes(run_id) if run_id else []
        if not run_id:
            return 0
        by_id = {n["node_id"]: n for n in nodes}
        for root in list(reachable):
            cur: str | None = root
            while cur and cur in by_id:
                reachable.add(cur)
                cur = by_id[cur].get("parent_id")
        removed = 0
        for node in nodes:
            nid = node["node_id"]
            if nid in reachable:
                continue
            if node.get("ref_count", 0) > 0:
                continue
            chash = node["content_hash"]
            self._storage.delete_snapshot_node(nid)
            if not any(n["content_hash"] == chash for n in nodes if n["node_id"] != nid):
                self._storage.delete_blob(chash)
            removed += 1
        return removed

    def _latest_node_for_run(self, run_id: str) -> str | None:
        nodes = self._storage.list_snapshot_nodes(run_id)
        if not nodes:
            return None
        return nodes[-1]["node_id"]

    def _gc_loop(self) -> None:
        while not self._stop_gc.wait(self._gc_interval):
            seen_runs: set[str] = set()
            for node_id in list(self._active_roots):
                node = self._storage.get_snapshot_node(node_id)
                if node and node.get("run_id"):
                    seen_runs.add(node["run_id"])
            for run_id in seen_runs:
                self.gc_once(run_id)
