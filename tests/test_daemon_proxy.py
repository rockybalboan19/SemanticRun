"""Daemon proxy freeze/restore/repair round-trip."""

from __future__ import annotations

import tempfile
from pathlib import Path

from semarun.kernel.runtime import DaemonProxyRuntime, InflightBuffer


def test_inflight_buffer_roundtrip():
    buf = InflightBuffer(
        pending_request_ids=["req_1", "req_2"],
        partial_responses={"req_1": b'{"partial": true}'},
    )
    restored = InflightBuffer.from_bytes(buf.to_bytes())
    assert restored.pending_request_ids == buf.pending_request_ids
    assert restored.partial_responses == buf.partial_responses


def test_daemon_proxy_submit_and_response():
    with tempfile.TemporaryDirectory() as tmp:
        proxy = DaemonProxyRuntime.in_memory(base_dir=Path(tmp) / "proxy")
        req_id = proxy.submit("llm_call", {"prompt": "hello"})
        resp = proxy.await_response(req_id, timeout=5.0)
        assert resp is not None
        proxy.close()


def test_freeze_restore_repair():
    with tempfile.TemporaryDirectory() as tmp:
        proxy = DaemonProxyRuntime.in_memory(base_dir=Path(tmp) / "proxy")
        proxy.submit("tool_call", {"tool": "grep"})
        frozen = proxy.freeze()
        assert frozen.pending_request_ids
        proxy.restore(frozen)
        proxy.repair_connections()
        assert not proxy._frozen
        proxy.close()
