"""Background checkpoint writer (CRAB R2: non-blocking agent thread)."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class CheckpointJob:
    run_id: str
    create_fn: Callable[[], Any]


class CheckpointWorker:
    def __init__(self) -> None:
        self._queue: queue.Queue[CheckpointJob | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def enqueue(self, run_id: str, create_fn: Callable[[], Any]) -> None:
        self._queue.put(CheckpointJob(run_id=run_id, create_fn=create_fn))

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                break
            try:
                job.create_fn()
            finally:
                self._queue.task_done()

    def stop(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=2.0)

    def drain(self) -> None:
        self._queue.join()
