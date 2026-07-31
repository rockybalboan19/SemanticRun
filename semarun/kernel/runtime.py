"""DeltaBox NPD daemon-proxy interception layer."""

from __future__ import annotations

import json
import os
import platform
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4


@dataclass
class InflightBuffer:
    """Serialized in-flight requests/responses at checkpoint freeze."""

    pending_request_ids: list[str] = field(default_factory=list)
    partial_responses: dict[str, bytes] = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        payload = {
            "pending_request_ids": self.pending_request_ids,
            "partial_responses": {
                k: v.decode("utf-8", errors="replace")
                for k, v in self.partial_responses.items()
            },
        }
        return json.dumps(payload).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> InflightBuffer:
        payload = json.loads(data.decode("utf-8"))
        return cls(
            pending_request_ids=list(payload.get("pending_request_ids", [])),
            partial_responses={
                k: v.encode("utf-8") if isinstance(v, str) else v
                for k, v in payload.get("partial_responses", {}).items()
            },
        )


class Transport(Protocol):
    def send_request(self, payload: bytes) -> str: ...

    def await_notify(self, timeout: float = 30.0) -> str | None: ...

    def read_response(self, request_id: str) -> bytes | None: ...

    def repair(self) -> None: ...

    def close(self) -> None: ...


class FifoTransport:
    """Unix FIFO request/notify transport."""

    def __init__(self, control_dir: Path) -> None:
        self._dir = control_dir
        self._req_fifo = control_dir / "request.fifo"
        self._notify_fifo = control_dir / "notify.fifo"
        self._resp_dir = control_dir / "responses"
        self._resp_dir.mkdir(parents=True, exist_ok=True)
        for fifo in (self._req_fifo, self._notify_fifo):
            if not fifo.exists():
                os.mkfifo(fifo)

    def send_request(self, payload: bytes) -> str:
        request_id = f"req_{uuid4().hex[:12]}"
        req_path = self._dir / f"{request_id}.json"
        req_path.write_bytes(payload)
        with open(self._req_fifo, "wb") as fifo:
            fifo.write(request_id.encode("utf-8") + b"\n")
        return request_id

    def await_notify(self, timeout: float = 30.0) -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._notify_fifo.exists():
                with open(self._notify_fifo, "rb") as fifo:
                    line = fifo.readline()
                    if line:
                        return line.decode("utf-8").strip()
            time.sleep(0.01)
        return None

    def read_response(self, request_id: str) -> bytes | None:
        path = self._resp_dir / f"{request_id}.json"
        if not path.exists():
            return None
        return path.read_bytes()

    def repair(self) -> None:
        pass

    def close(self) -> None:
        pass


class SocketTransport:
    """Windows/loopback TCP fallback with identical API surface."""

    def __init__(self, control_dir: Path, host: str = "127.0.0.1", port: int = 0) -> None:
        self._dir = control_dir
        self._resp_dir = control_dir / "responses"
        self._resp_dir.mkdir(parents=True, exist_ok=True)
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, port))
        self._server.listen(5)
        self._port = self._server.getsockname()[1]
        (control_dir / "transport.port").write_text(str(self._port), encoding="utf-8")
        self._notify_queue: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self._client: socket.socket | None = None

    @property
    def port(self) -> int:
        return self._port

    def _serve(self) -> None:
        self._server.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
                self._client = conn
                data = self._recv_frame(conn)
                if data:
                    self._notify_queue.put(data.decode("utf-8"))
            except socket.timeout:
                continue
            except OSError:
                break

    def _recv_frame(self, sock: socket.socket) -> bytes | None:
        header = sock.recv(4)
        if len(header) < 4:
            return None
        length = struct.unpack("!I", header)[0]
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            chunk = sock.recv(min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send_frame(self, sock: socket.socket, payload: bytes) -> None:
        sock.sendall(struct.pack("!I", len(payload)) + payload)

    def send_request(self, payload: bytes) -> str:
        request_id = f"req_{uuid4().hex[:12]}"
        req_path = self._dir / f"{request_id}.json"
        req_path.write_bytes(payload)
        if self._client is None:
            client = socket.create_connection(("127.0.0.1", self._port), timeout=5.0)
            self._send_frame(client, request_id.encode("utf-8"))
            client.close()
        else:
            self._send_frame(self._client, request_id.encode("utf-8"))
        return request_id

    def await_notify(self, timeout: float = 30.0) -> str | None:
        try:
            return self._notify_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def read_response(self, request_id: str) -> bytes | None:
        path = self._resp_dir / f"{request_id}.json"
        if not path.exists():
            return None
        return path.read_bytes()

    def repair(self) -> None:
        if self._client:
            try:
                self._client.close()
            except OSError:
                pass
            self._client = None

    def close(self) -> None:
        self._stop.set()
        self._server.close()
        if self._client:
            self._client.close()


def create_transport(control_dir: Path) -> Transport:
    if platform.system() == "Windows":
        return SocketTransport(control_dir)
    return FifoTransport(control_dir)


class MockControlPlane:
    """In-process control plane for tests (no subprocess/fork)."""

    def __init__(self, control_dir: Path) -> None:
        self._dir = control_dir
        self._resp_dir = control_dir / "responses"
        self._resp_dir.mkdir(parents=True, exist_ok=True)
        self._handlers: dict[str, Any] = {}

    def register_handler(self, kind: str, handler: Any) -> None:
        self._handlers[kind] = handler

    def process_request(self, request_id: str) -> None:
        req_path = self._dir / f"{request_id}.json"
        if not req_path.exists():
            return
        payload = json.loads(req_path.read_bytes())
        kind = payload.get("kind", "")
        handler = self._handlers.get(kind)
        if handler:
            result = handler(payload)
        else:
            result = {"status": "ok", "echo": payload}
        resp_path = self._resp_dir / f"{request_id}.json"
        resp_path.write_bytes(json.dumps(result).encode("utf-8"))


class DaemonProxyRuntime:
    """Agent-side NPD proxy: no live SDK sockets in agent process."""

    def __init__(
        self,
        control_dir: str | Path,
        *,
        transport: Transport | None = None,
        mock_control: MockControlPlane | None = None,
    ) -> None:
        self._dir = Path(control_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._transport = transport or create_transport(self._dir)
        self._mock_control = mock_control
        self._inflight = InflightBuffer()
        self._frozen = False

    @property
    def control_dir(self) -> Path:
        return self._dir

    def submit(self, kind: str, payload: dict[str, Any]) -> str:
        if self._frozen:
            raise RuntimeError("Cannot submit while frozen")
        body = json.dumps({"kind": kind, **payload}).encode("utf-8")
        request_id = self._transport.send_request(body)
        self._inflight.pending_request_ids.append(request_id)
        if self._mock_control is not None:
            self._mock_control.process_request(request_id)
        return request_id

    def await_response(self, request_id: str, timeout: float = 30.0) -> bytes | None:
        notify = self._transport.await_notify(timeout=timeout)
        if notify and notify != request_id:
            partial = self._transport.read_response(notify)
            if partial:
                self._inflight.partial_responses[notify] = partial
        data = self._transport.read_response(request_id)
        if data and request_id in self._inflight.pending_request_ids:
            self._inflight.pending_request_ids.remove(request_id)
        return data

    def freeze(self) -> InflightBuffer:
        self._frozen = True
        for req_id in list(self._inflight.pending_request_ids):
            partial = self._transport.read_response(req_id)
            if partial:
                self._inflight.partial_responses[req_id] = partial
        return InflightBuffer(
            pending_request_ids=list(self._inflight.pending_request_ids),
            partial_responses=dict(self._inflight.partial_responses),
        )

    def restore(self, buffer: InflightBuffer) -> None:
        self._inflight = InflightBuffer(
            pending_request_ids=list(buffer.pending_request_ids),
            partial_responses=dict(buffer.partial_responses),
        )
        self._frozen = False

    def repair_connections(self) -> None:
        self._transport.repair()

    def close(self) -> None:
        self._transport.close()

    @classmethod
    def in_memory(cls, base_dir: str | Path | None = None) -> DaemonProxyRuntime:
        path = Path(base_dir or (Path.cwd() / ".semarun_proxy"))
        mock = MockControlPlane(path)
        transport = SocketTransport(path)
        return cls(path, transport=transport, mock_control=mock)


class ControlPlaneDaemon:
    """Subprocess entrypoint for holding SDK client handles."""

    def __init__(self, control_dir: Path) -> None:
        self._dir = control_dir
        self._transport = create_transport(control_dir)

    @staticmethod
    def spawn(control_dir: Path) -> subprocess.Popen[Any]:
        module = "semarun.kernel.runtime"
        return subprocess.Popen(
            [sys.executable, "-m", module, str(control_dir), "--daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def run(self) -> None:
        while True:
            notify = self._transport.await_notify(timeout=60.0)
            if notify is None:
                continue
            req_path = self._dir / f"{notify}.json"
            if not req_path.exists():
                continue
            payload = json.loads(req_path.read_bytes())
            result = {"status": "ok", "received": payload}
            resp_path = self._dir / "responses" / f"{notify}.json"
            resp_path.parent.mkdir(parents=True, exist_ok=True)
            resp_path.write_bytes(json.dumps(result).encode("utf-8"))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("control_dir")
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()
    if args.daemon:
        ControlPlaneDaemon(Path(args.control_dir)).run()


if __name__ == "__main__":
    main()
