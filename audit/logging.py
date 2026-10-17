"""Logging handler that scans formatted lines without storing their contents."""

from __future__ import annotations

import logging
from pathlib import Path


class ScannerLogHandler(logging.Handler):
    """Send each formatted log line to a canary scanner, then discard it."""

    def __init__(self, scanner, path: str | Path | None = None) -> None:
        super().__init__()
        self.scanner = scanner
        self.path = Path(path) if path is not None else None

    def reset(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def record(self, line: str) -> None:
        if type(line) is not str:
            raise TypeError("audit log lines must be strings")
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        self.scanner.scan("log", line)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.record(self.format(record))
        except Exception:
            self.handleError(record)
