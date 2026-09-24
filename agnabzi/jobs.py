"""Background jobs for long-running tools (speed test, traceroute) with pollable progress."""

from __future__ import annotations

import itertools
import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

MAX_FINISHED_JOBS = 20


class Job:
    def __init__(self, job_id: str, kind: str):
        self.id = job_id
        self.kind = kind
        self.state = "running"
        self.progress: dict[str, Any] = {}
        self.items: list[Any] = []
        self.result: Any = None
        self.error: str | None = None
        self.created = time.time()
        self._lock = threading.Lock()
        self._cancel_hooks: list[Callable[[], None]] = []
        self.cancelled = threading.Event()

    def update(self, **progress: Any) -> None:
        with self._lock:
            self.progress.update(progress)

    def append(self, item: Any) -> None:
        with self._lock:
            self.items.append(item)

    def on_cancel(self, hook: Callable[[], None]) -> None:
        self._cancel_hooks.append(hook)

    def cancel(self) -> None:
        self.cancelled.set()
        for hook in self._cancel_hooks:
            hook()

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "state": self.state,
                "progress": dict(self.progress),
                "items": list(self.items),
                "result": self.result,
                "error": self.error,
            }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def start(self, kind: str, work: Callable[[Job], Any], exclusive: bool = False) -> Job:
        with self._lock:
            if exclusive:
                for job in self._jobs.values():
                    if job.kind == kind and job.state == "running":
                        return job
            job = Job(f"{kind}-{next(self._ids)}", kind)
            self._jobs[job.id] = job
            self._prune()
        threading.Thread(target=self._execute, args=(job, work), name=f"job-{job.id}", daemon=True).start()
        return job

    def _execute(self, job: Job, work: Callable[[Job], Any]) -> None:
        try:
            job.result = work(job)
            job.state = "cancelled" if job.cancelled.is_set() else "done"
        except Exception as exc:
            log.info("job %s failed: %s", job.id, exc)
            job.error = getattr(exc, "code", None) or type(exc).__name__
            job.state = "error"

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _prune(self) -> None:
        finished = [j for j in self._jobs.values() if j.state != "running"]
        for job in sorted(finished, key=lambda j: j.created)[:-MAX_FINISHED_JOBS]:
            del self._jobs[job.id]
