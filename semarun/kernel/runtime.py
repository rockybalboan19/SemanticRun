"""NPD-style daemon-proxy interception layer (DeltaBox pattern).

Runs model/tool SDK clients in a separate control-plane process so the agent
process holds no live SDK sockets. Communication uses FIFOs (Unix) or named
pipes (Windows) plus a shared request-response directory.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from semarun.models.state import new_id


class RequestKind(str, Enum):
    MODEL = "model"
    TOOL = "tool"
    FREEZE = "freeze"
    RESTORE = "restore"


@dataclass
class ProxyRequest:
    request_id: str
    kind: RequestKind
    payload: dict[str, Any]
    created_at: float = field(default_factory=time.time)


@dataclass
class ProxyResponse:
    request_id: str
    ok: bool
    result: Any = None
    error: str = ""


class InFlightBuffer:
    """Buffer in-flight model/tool responses across checkpoint freeze."""

    def __init__(self) -> None:
        self._pending: dict[str, ProxyRequest] = {}
        self._responses: dict[str, ProxyResponse] = {}
        self._frozen = False
        self._lock = threading.Lock()

    @property
    def frozen(self) -> bool:
        return self._frozen

    def track(self, request: ProxyRequest) -> None:
        with self._lock:
            self._pending[request.request_id] = request

    def complete(self, response: ProxyResponse) -> None:
        with self._lock:
            self._pending.pop(response.request_id, None)
            self._responses[response.request_id] = response

    def freeze(self) -> list[ProxyRequest]:
        with self._lock:
            self._frozen = True
            return list(self._pending.values())

    def thaw(self) -> None:
        with self._lock:
            self._frozen = False

    def drain_responses(self) -> list[ProxyResponse]:
        with self._lock:
            out = list(self._responses.values())
            self._responses.clear()
            return out

    def get_response(self, request_id: str) -> ProxyResponse | None:
        with self._lock:
            return self._responses.get(request_id)


class _PipePair:
    """Portable bidirectional notify channel (FIFO on Unix, temp files on Windows)."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.agent_to_daemon = workdir / "notify_agent_to_daemon"
        self.daemon_to_agent = workdir / "notify_daemon_to_agent"
        self._agent_queue: queue.Queue[str] = queue.Queue()
        self._daemon_queue: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for path in (self.agent_to_daemon, self.daemon_to_agent):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
        self._threads = [
            threading.Thread(
                target=self._watch_file,
                args=(self.agent_to_daemon, self._daemon_queue),
                daemon=True,
            ),
            threading.Thread(
                target=self._watch_file,
                args=(self.daemon_to_agent, self._agent_queue),
                daemon=True,
            ),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()

    def notify_daemon(self, message: str) -> None:
        self._append(self.agent_to_daemon, message)
        self._daemon_queue.put(message)

    def notify_agent(self, message: str) -> None:
        self._append(self.daemon_to_agent, message)
        self._agent_queue.put(message)

    def wait_agent(self, timeout: float = 30.0) -> str | None:
        try:
            return self._agent_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def wait_daemon(self, timeout: float = 30.0) -> str | None:
        try:
            return self._daemon_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _append(self, path: Path, message: str) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")

    def _watch_file(self, path: Path, target: queue.Queue) -> None:
        last_size = 0
        while not self._stop.is_set():
            try:
                size = path.stat().st_size
                if size > last_size:
                    text = path.read_text(encoding="utf-8")
                    lines = text.strip().splitlines()
                    for line in lines[last_size and len(lines) - 1 or 0 :]:
                        if line:
                            target.put(line)
                    last_size = size
            except OSError:
                pass
            time.sleep(0.05)


def _daemon_main(
    workdir: str,
    handler: Callable[[ProxyRequest], Any],
) -> None:
    root = Path(workdir)
    req_dir = root / "requests"
    resp_dir = root / "responses"
    req_dir.mkdir(parents=True, exist_ok=True)
    resp_dir.mkdir(parents=True, exist_ok=True)
    pipes = _PipePair(root)
    pipes.start()
    while True:
        msg = pipes.wait_daemon(timeout=1.0)
        if msg is None:
            continue
        if msg == "__shutdown__":
            break
        req_path = req_dir / f"{msg}.json"
        if not req_path.exists():
            continue
        data = json.loads(req_path.read_text(encoding="utf-8"))
        request = ProxyRequest(
            request_id=data["request_id"],
            kind=RequestKind(data["kind"]),
            payload=data.get("payload", {}),
        )
        if request.kind == RequestKind.FREEZE:
            resp = ProxyResponse(request_id=request.request_id, ok=True, result={"frozen": True})
        elif request.kind == RequestKind.RESTORE:
            resp = ProxyResponse(request_id=request.request_id, ok=True, result={"restored": True})
        else:
            try:
                result = handler(request)
                resp = ProxyResponse(request_id=request.request_id, ok=True, result=result)
            except Exception as exc:
                resp = ProxyResponse(
                    request_id=request.request_id, ok=False, error=str(exc)
                )
        out = resp_dir / f"{request.request_id}.json"
        out.write_text(
            json.dumps(
                {
                    "request_id": resp.request_id,
                    "ok": resp.ok,
                    "result": resp.result,
                    "error": resp.error,
                },
                default=str,
            ),
            encoding="utf-8",
        )
        pipes.notify_agent(request.request_id)


class DaemonProxyRuntime:
    """
    Agent-side bridge to control-plane daemon.

    Agent process never opens SDK sockets; all model/tool calls route through
    FIFO notify + shared request/response directory.
    """

    def __init__(
        self,
        workdir: str | Path | None = None,
        *,
        handler: Callable[[ProxyRequest], Any] | None = None,
        start_daemon: bool = True,
    ) -> None:
        self.workdir = Path(workdir or Path.cwd() / ".semarun_proxy")
        self.req_dir = self.workdir / "requests"
        self.resp_dir = self.workdir / "responses"
        self.req_dir.mkdir(parents=True, exist_ok=True)
        self.resp_dir.mkdir(parents=True, exist_ok=True)
        self.inflight = InFlightBuffer()
        self._pipes = _PipePair(self.workdir)
        self._pipes.start()
        self._handler = handler or self._default_handler
        self._process: Any = None
        self._daemon_thread: threading.Thread | None = None
        if start_daemon:
            self.start_daemon()

    def _default_handler(self, request: ProxyRequest) -> Any:
        return {"echo": request.payload, "kind": request.kind.value}

    def start_daemon(self) -> None:
        if sys.platform == "win32":
            self._daemon_thread = threading.Thread(
                target=_daemon_main,
                args=(str(self.workdir), self._handler),
                daemon=True,
            )
            self._daemon_thread.start()
        else:
            import multiprocessing

            self._process = multiprocessing.Process(
                target=_daemon_main,
                args=(str(self.workdir), self._handler),
                daemon=True,
            )
            self._process.start()

    def invoke(self, kind: RequestKind, payload: dict[str, Any]) -> ProxyResponse:
        request_id = new_id("req")
        request = ProxyRequest(request_id=request_id, kind=kind, payload=payload)
        self.inflight.track(request)
        req_path = self.req_dir / f"{request_id}.json"
        req_path.write_text(
            json.dumps(
                {
                    "request_id": request_id,
                    "kind": kind.value,
                    "payload": payload,
                },
                default=str,
            ),
            encoding="utf-8",
        )
        self._pipes.notify_daemon(request_id)
        notify = self._pipes.wait_agent(timeout=30.0)
        if notify != request_id:
            return ProxyResponse(
                request_id=request_id, ok=False, error="daemon response timeout"
            )
        resp_path = self.resp_dir / f"{request_id}.json"
        if not resp_path.exists():
            return ProxyResponse(
                request_id=request_id, ok=False, error="missing response file"
            )
        data = json.loads(resp_path.read_text(encoding="utf-8"))
        response = ProxyResponse(
            request_id=data["request_id"],
            ok=data["ok"],
            result=data.get("result"),
            error=data.get("error", ""),
        )
        self.inflight.complete(response)
        return response

    def freeze_for_checkpoint(self) -> list[ProxyRequest]:
        pending = self.inflight.freeze()
        self.invoke(RequestKind.FREEZE, {"pending": [p.request_id for p in pending]})
        return pending

    def restore_after_fork(self, pending: list[ProxyRequest]) -> None:
        """Re-pair agent-side connection and replay buffered in-flight state."""
        self.invoke(
            RequestKind.RESTORE,
            {"pending": [p.request_id for p in pending]},
        )
        self.inflight.thaw()
        for req in pending:
            resp = self.inflight.get_response(req.request_id)
            if resp is None:
                self._pipes.notify_daemon(req.request_id)

    def shutdown(self) -> None:
        self._pipes.notify_daemon("__shutdown__")
        self._pipes.stop()
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2.0)

    def cleanup_workdir(self) -> None:
        for path in self.req_dir.glob("*.json"):
            path.unlink(missing_ok=True)
        for path in self.resp_dir.glob("*.json"):
            path.unlink(missing_ok=True)
