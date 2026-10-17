"""Scanner for planted synthetic canaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


NEGATIVE_CONTROL_TOKEN = "SYNTHETIC_CANARY_NEGATIVE_CONTROL"
_PREFIX_BYTES = 16


class CanaryScanner:
    """Scan text using only canary digests and UTF-8 byte lengths.

    Index files contain hashes and byte lengths rather than source strings.
    Input messages are discarded after each scan. A short-prefix digest filters
    candidate windows before a full digest comparison. The built-in
    negative-control token makes the audit path self-checking even when no
    generated canary index is configured.
    """

    def __init__(self, canaries: Iterable[str] = ()) -> None:
        entries: list[tuple[int, bytes, int, bytes]] = []
        for canary in canaries:
            if type(canary) is not str:
                raise TypeError("canaries must be strings")
            raw = canary.encode("utf-8")
            if raw:
                prefix_length = min(_PREFIX_BYTES, len(raw))
                entries.append(
                    (
                        len(raw),
                        hashlib.sha256(raw).digest(),
                        prefix_length,
                        hashlib.sha256(raw[:prefix_length]).digest(),
                    )
                )
        self._initialize(entries)

    def _initialize(self, entries: Iterable[tuple[int, bytes, int, bytes]]) -> None:
        negative_raw = NEGATIVE_CONTROL_TOKEN.encode("utf-8")
        prefix_length = min(_PREFIX_BYTES, len(negative_raw))
        normalized = set(entries)
        normalized.add(
            (
                len(negative_raw),
                hashlib.sha256(negative_raw).digest(),
                prefix_length,
                hashlib.sha256(negative_raw[:prefix_length]).digest(),
            )
        )
        self._entries = frozenset(normalized)
        prefixes: dict[int, dict[bytes, list[tuple[int, bytes]]]] = {}
        for length, digest, prefix_size, prefix_digest in self._entries:
            prefixes.setdefault(prefix_size, {}).setdefault(prefix_digest, []).append((length, digest))
        self._prefixes = {
            prefix_size: {digest: tuple(matches) for digest, matches in candidates.items()}
            for prefix_size, candidates in prefixes.items()
        }
        self._candidate_prefix_lengths = tuple(sorted(self._prefixes))
        self.messages = 0
        self.bytes_scanned = 0
        self.hits = 0

    @classmethod
    def from_index_file(cls, path: str | Path) -> "CanaryScanner":
        """Load a digest-only JSON index produced by the harness."""
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, Mapping) or set(document) != {"version", "entries"}:
            raise ValueError("canary index must contain exactly version and entries")
        if type(document["version"]) is not int or document["version"] != 2:
            raise ValueError("unsupported canary index version")
        if type(document["entries"]) is not list:
            raise ValueError("canary index entries must be a list")
        entries: list[tuple[int, bytes, int, bytes]] = []
        for index, entry in enumerate(document["entries"]):
            if not isinstance(entry, Mapping) or set(entry) != {
                "length", "sha256", "prefix_length", "prefix_sha256"
            }:
                raise ValueError(f"canary index entry {index} has an invalid shape")
            length, digest_text = entry["length"], entry["sha256"]
            prefix_length, prefix_text = entry["prefix_length"], entry["prefix_sha256"]
            if type(length) is not int or length < 1:
                raise ValueError(f"canary index entry {index} has an invalid length")
            if type(digest_text) is not str or len(digest_text) != 64:
                raise ValueError(f"canary index entry {index} has an invalid SHA-256 digest")
            try:
                digest = bytes.fromhex(digest_text)
            except ValueError as exc:
                raise ValueError(f"canary index entry {index} has an invalid SHA-256 digest") from exc
            if digest.hex() != digest_text.lower():
                raise ValueError(f"canary index entry {index} has an invalid SHA-256 digest")
            if type(prefix_length) is not int or not 1 <= prefix_length <= min(_PREFIX_BYTES, length):
                raise ValueError(f"canary index entry {index} has an invalid prefix length")
            if type(prefix_text) is not str or len(prefix_text) != 64:
                raise ValueError(f"canary index entry {index} has an invalid prefix digest")
            try:
                prefix_digest = bytes.fromhex(prefix_text)
            except ValueError as exc:
                raise ValueError(f"canary index entry {index} has an invalid prefix digest") from exc
            if prefix_digest.hex() != prefix_text.lower():
                raise ValueError(f"canary index entry {index} has an invalid prefix digest")
            entries.append((length, digest, prefix_length, prefix_digest))
        scanner = cls()
        scanner._initialize(entries)
        return scanner

    @property
    def messages_scanned(self) -> int:
        return self.messages

    def reset(self) -> None:
        """Clear observed counters while preserving the digest-only index."""
        self.messages = 0
        self.bytes_scanned = 0
        self.hits = 0

    def scan(self, channel_or_message: str, text: str | None = None) -> int:
        """Scan using ``scan(channel, text)`` or the text-only shorthand."""
        if text is None:
            message = channel_or_message
        else:
            if type(channel_or_message) is not str:
                raise TypeError("channel must be a string")
            message = text
        if type(message) is not str:
            raise TypeError("message must be a string")
        raw = message.encode("utf-8")
        self.messages += 1
        self.bytes_scanned += len(raw)

        found = 0
        for prefix_length in self._candidate_prefix_lengths:
            if prefix_length > len(raw):
                continue
            candidates_by_prefix = self._prefixes[prefix_length]
            for start in range(len(raw) - prefix_length + 1):
                candidates = candidates_by_prefix.get(
                    hashlib.sha256(raw[start : start + prefix_length]).digest(), ()
                )
                for length, digest in candidates:
                    if start + length <= len(raw):
                        found += hashlib.sha256(raw[start : start + length]).digest() == digest

        self.hits += found
        return found
