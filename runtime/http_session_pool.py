"""Bounded, exclusive-use pool for persistent requests sessions."""

from __future__ import annotations

from queue import Empty, LifoQueue
from threading import Lock
from typing import Any, Callable


class HttpSessionPool:
    """Reuse HTTP connection pools across short lived request threads.

    A checked-out Session is owned by one caller until its request completes;
    sessions are never used concurrently. The pool grows on demand and caps the
    number of open connection pools.
    """

    def __init__(self, factory: Callable[[], Any], max_size: int = 32):
        if type(max_size) is not int or max_size < 1:
            raise ValueError("max_size must be a positive integer")
        self._factory = factory
        self._max_size = max_size
        self._available: LifoQueue[Any] = LifoQueue(maxsize=max_size)
        self._created = 0
        self._lock = Lock()

    def _acquire(self) -> Any:
        try:
            return self._available.get_nowait()
        except Empty:
            pass

        with self._lock:
            if self._created < self._max_size:
                self._created += 1
                try:
                    return self._factory()
                except BaseException:
                    self._created -= 1
                    raise
        return self._available.get()

    def post(self, url: str, *, json: Any, timeout: float) -> Any:
        session = self._acquire()
        try:
            return session.post(url, json=json, timeout=timeout)
        finally:
            self._available.put(session)
