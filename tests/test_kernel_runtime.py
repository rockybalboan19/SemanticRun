"""Tests for NPD daemon-proxy runtime."""

from __future__ import annotations

import tempfile
from pathlib import Path

from semarun.kernel.runtime import DaemonProxyRuntime, RequestKind


def test_daemon_proxy_invoke_echo():
    with tempfile.TemporaryDirectory() as tmp:
        proxy = DaemonProxyRuntime(workdir=Path(tmp) / "proxy")
        resp = proxy.invoke(RequestKind.TOOL, {"tool": "grep", "args": {"pattern": "x"}})
        assert resp.ok is True
        assert resp.result is not None
        proxy.shutdown()


def test_freeze_and_restore_repair():
    with tempfile.TemporaryDirectory() as tmp:
        proxy = DaemonProxyRuntime(workdir=Path(tmp) / "proxy")
        pending = proxy.freeze_for_checkpoint()
        proxy.restore_after_fork(pending)
        assert proxy.inflight.frozen is False
        proxy.shutdown()
