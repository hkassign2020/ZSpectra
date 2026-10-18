"""Blind, bank-local feature extraction for Z-SPECTRA.

Raw transaction fields are read only inside this sensor.  The returned mapping
contains only the frozen bank-to-mesh contract fields.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict, deque
import hashlib
import hmac
import math
import os
from pathlib import Path
import time
import unicodedata
import zlib
from typing import Any, Mapping


SENSOR_FEATURES = (
    "log_amount_bin",
    "log1p_gap_ms",
    "aborts_5s",
    "tx_count_60s",
    "distinct_dest_60s",
    "window_ncd",
    "compression_ratio",
    "window_entropy",
)

BANK_TO_MESH_KEYS = (
    "bank",
    "seq",
    "t_ms",
    "src_bid",
    "dst_bid",
    "amt_bin",
    "f",
    "sensor_us",
)

_BANKS = frozenset(("A", "B", "C"))
_TX_TYPES = frozenset(("TRANSFER", "PAYMENT", "CASHOUT"))
_CHANNELS = frozenset(("APP", "WEB", "API"))
_WINDOW_SIZE = 32
_HISTORY_MS = 60_000
_FIRST_GAP_MS = 60_000


def compression_profile() -> dict[str, str | int | None]:
    """Describe zlib settings that affect the sensor's compression features.

    Prepared models are invalidated when this profile changes so training and
    inference use the same compression behavior.
    """
    return {
        "algorithm": "zlib",
        "level": 6,
        "zlib_compile_version": zlib.ZLIB_VERSION,
        "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION,
        "dfltcc": os.environ.get("DFLTCC"),
        "dfltcc_level_mask": os.environ.get("DFLTCC_LEVEL_MASK"),
    }

# These are lower-bound thresholds: a value exactly on a threshold enters
# that bucket. Values above the final threshold stay in the final bucket.
GAP_BIN_BOUNDARIES_MS = (0, 100, 500, 1_000, 5_000, 15_000, 60_000)
ABORT_BIN_BOUNDARIES = (0, 1, 2, 4, 8, 16, 32)


def _canonical_account(account: str) -> str:
    if not isinstance(account, str):
        raise TypeError("account identifiers must be strings")
    canonical = unicodedata.normalize("NFKC", account).strip()
    if not canonical:
        raise ValueError("account identifier cannot be empty")
    return canonical


def bid_for(account: str, key: bytes) -> str:
    """Return the consortium-key HMAC pseudonym for a canonical account."""
    if not isinstance(key, bytes) or not key:
        raise ValueError("consortium key must be non-empty bytes")
    canonical = _canonical_account(account).encode("utf-8")
    digest = hmac.new(key, canonical, hashlib.sha256).hexdigest()[:12]
    return "BID_" + digest


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for value in data:
        counts[value] += 1
    size = len(data)
    return -sum(
        (count / size) * math.log2(count / size)
        for count in counts
        if count
    )


def _compressed_size(data: bytes) -> int:
    return len(zlib.compress(data, level=6))


def _bucket_index(value: int, boundaries: tuple[int, ...]) -> int:
    return min(bisect_right(boundaries[1:], value), len(boundaries) - 1)


def _normalized_compression_distance(window: bytes, reference: bytes) -> float:
    if not window or not reference:
        return 0.0
    window_size = _compressed_size(window)
    reference_size = _compressed_size(reference)
    denominator = max(window_size, reference_size)
    if denominator == 0:
        return 0.0
    joined_size = _compressed_size(window + b"\n" + reference)
    distance = (joined_size - min(window_size, reference_size)) / denominator
    # zlib framing can make small inputs fall outside the ideal NCD interval.
    return min(1.0, max(0.0, distance))


def _compression_features(
    window: bytes,
    reference: bytes,
    reference_compressed_size: int,
) -> tuple[float, float]:
    """Return level-six NCD and compression ratio with one window compression."""
    window_size = _compressed_size(window)
    ratio = window_size / max(len(window), 1)
    if not window or not reference:
        return 0.0, ratio
    denominator = max(window_size, reference_compressed_size)
    if denominator == 0:
        return 0.0, ratio
    joined_size = _compressed_size(window + b"\n" + reference)
    distance = (joined_size - min(window_size, reference_compressed_size)) / denominator
    return min(1.0, max(0.0, distance)), ratio


def _default_reference_window() -> bytes:
    return Path(__file__).with_name("reference_window.txt").read_bytes()


class BlindSensor:
    """Maintain bank-local rolling state and emit blind mesh messages.

    Args:
        key: Consortium HMAC key, kept inside the bank service.
        bank: Owning bank code (``A``, ``B``, or ``C``).
        ref_window: Fixed reference bytes used for normalized compression
            distance. If omitted, the checked-in deterministic reference window
            is loaded from ``bank/reference_window.txt``.
    """

    def __init__(self, key: bytes, bank: str, ref_window: bytes | None = None):
        if not isinstance(key, bytes) or not key:
            raise ValueError("consortium key must be non-empty bytes")
        if bank not in _BANKS:
            raise ValueError("bank must be A, B, or C")
        if ref_window is None:
            ref_window = _default_reference_window()
        if not isinstance(ref_window, bytes) or not ref_window:
            raise ValueError("reference window must be non-empty bytes")

        self._key = key
        self.bank = bank
        self._reference = ref_window
        self._reference_compressed_size = _compressed_size(ref_window)
        if not ref_window.strip():
            raise ValueError("reference window must contain non-whitespace bytes")
        self._sequence = 0
        self._last_timestamp: dict[str, int] = {}
        self._history: dict[str, deque[tuple[int, str]]] = defaultdict(deque)
        self._history_dest_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self._windows: dict[str, deque[str]] = defaultdict(
            lambda: deque(maxlen=_WINDOW_SIZE)
        )
        self._window_buffers: dict[str, bytearray] = defaultdict(bytearray)

    def reset(self) -> None:
        """Clear bank-local rolling state for the synthetic demo reset."""
        self._sequence = 0
        self._last_timestamp.clear()
        self._history.clear()
        self._history_dest_counts.clear()
        self._windows.clear()
        self._window_buffers.clear()

    def process(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Consume one raw event and return exactly the bank-to-mesh fields."""
        started_ns = time.perf_counter_ns()
        if not isinstance(raw, Mapping):
            raise TypeError("raw event must be a mapping")

        bank = raw.get("bank")
        if bank != self.bank:
            raise ValueError("event bank does not match this sensor")
        ts_ms = raw.get("ts_ms")
        if isinstance(ts_ms, bool) or not isinstance(ts_ms, int):
            raise TypeError("ts_ms must be an integer")
        src_account = _canonical_account(raw.get("src_account"))
        dst_account = _canonical_account(raw.get("dst_account"))
        tx_type = raw.get("tx_type")
        channel = raw.get("channel")
        if tx_type not in _TX_TYPES:
            raise ValueError("tx_type must be TRANSFER, PAYMENT, or CASHOUT")
        if channel not in _CHANNELS:
            raise ValueError("channel must be APP, WEB, or API")

        try:
            amount = float(raw.get("amount"))
        except (TypeError, ValueError) as exc:
            raise TypeError("amount must be numeric") from exc
        if not math.isfinite(amount):
            raise ValueError("amount must be finite")
        amt_bin = math.floor(4 * math.log10(max(amount, 1.0))) / 4

        aborts = raw.get("aborted_retries_last_5s")
        if isinstance(aborts, bool) or not isinstance(aborts, int) or aborts < 0:
            raise ValueError("aborted_retries_last_5s must be a non-negative integer")

        src_bid = bid_for(src_account, self._key)
        dst_bid = bid_for(dst_account, self._key)

        history = self._history[src_account]
        destination_counts = self._history_dest_counts[src_account]
        cutoff = ts_ms - _HISTORY_MS
        if history and ts_ms < history[-1][0]:
            # Keep out-of-order inputs correct; the common in-order path below
            # only removes expired events from the left side of the deque.
            recent = sorted(
                (item for item in history if cutoff <= item[0] <= ts_ms),
                key=lambda item: item[0],
            )
            history.clear()
            history.extend(recent)
            destination_counts.clear()
            destination_counts.update(destination for _, destination in recent)
        else:
            while history and history[0][0] < cutoff:
                _expired_ts, expired_destination = history.popleft()
                destination_counts[expired_destination] -= 1
                if destination_counts[expired_destination] == 0:
                    del destination_counts[expired_destination]
        previous_ts = self._last_timestamp.get(src_account)
        gap_ms = _FIRST_GAP_MS if previous_ts is None else max(0, ts_ms - previous_ts)
        history.append((ts_ms, dst_account))
        destination_counts[dst_account] += 1
        self._last_timestamp[src_account] = ts_ms
        tx_count_60s = len(history)
        distinct_dest_60s = len(destination_counts)

        # Keep the text window scoped to one source account. Its rows contain
        # only the frozen coarse representation, never raw IDs or full bids.
        gap_bin = _bucket_index(gap_ms, GAP_BIN_BOUNDARIES_MS)
        abort_bin = _bucket_index(aborts, ABORT_BIN_BOUNDARIES)
        window_record = f"{tx_type}|{amt_bin:.2f}|{gap_bin}|{abort_bin}|{dst_bid[:6]}\n"
        window = self._windows[src_account]
        window_buffer = self._window_buffers[src_account]
        if len(window) == _WINDOW_SIZE:
            evicted = window.popleft().encode("utf-8")
            del window_buffer[: len(evicted)]
        window.append(window_record)
        window_buffer.extend(window_record.encode("utf-8"))
        window_bytes = bytes(window_buffer)
        window_ncd, compression_ratio = _compression_features(
            window_bytes,
            self._reference,
            self._reference_compressed_size,
        )

        feature_values = [
            float(amt_bin),
            float(math.log1p(gap_ms)),
            float(aborts),
            float(tx_count_60s),
            float(distinct_dest_60s),
            float(window_ncd),
            float(compression_ratio),
            float(_entropy(window_bytes)),
        ]
        self._sequence += 1
        sensor_us = (time.perf_counter_ns() - started_ns) / 1_000.0

        # Keep this construction explicit so accidental raw fields cannot be
        # added to the outbound wire object.
        return {
            "bank": self.bank,
            "seq": self._sequence,
            "t_ms": ts_ms,
            "src_bid": src_bid,
            "dst_bid": dst_bid,
            "amt_bin": float(amt_bin),
            "f": feature_values,
            "sensor_us": float(sensor_us),
        }
