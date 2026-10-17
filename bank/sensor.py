"""Blind, bank-local feature extraction for Z-SPECTRA.

Raw transaction fields are read only inside this sensor.  The returned mapping
contains only the frozen bank-to-mesh contract fields.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict, deque
import hashlib
import hmac
import math
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
        if not ref_window.strip():
            raise ValueError("reference window must contain non-whitespace bytes")
        self._sequence = 0
        self._last_timestamp: dict[str, int] = {}
        self._history: dict[str, deque[tuple[int, str]]] = defaultdict(deque)
        self._windows: dict[str, deque[str]] = defaultdict(
            lambda: deque(maxlen=_WINDOW_SIZE)
        )

    def reset(self) -> None:
        """Clear bank-local rolling state for the synthetic demo reset."""
        self._sequence = 0
        self._last_timestamp.clear()
        self._history.clear()
        self._windows.clear()

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
        cutoff = ts_ms - _HISTORY_MS
        recent = [item for item in history if cutoff <= item[0] <= ts_ms]
        history.clear()
        history.extend(recent)
        previous_ts = self._last_timestamp.get(src_account)
        gap_ms = _FIRST_GAP_MS if previous_ts is None else max(0, ts_ms - previous_ts)
        history.append((ts_ms, dst_account))
        self._last_timestamp[src_account] = ts_ms
        tx_count_60s = len(history)
        distinct_dest_60s = len({dest for _, dest in history})

        # Keep the text window scoped to one source account. Its rows contain
        # only the frozen coarse representation, never raw IDs or full bids.
        gap_bin = _bucket_index(gap_ms, GAP_BIN_BOUNDARIES_MS)
        abort_bin = _bucket_index(aborts, ABORT_BIN_BOUNDARIES)
        window_record = f"{tx_type}|{amt_bin:.2f}|{gap_bin}|{abort_bin}|{dst_bid[:6]}\n"
        window = self._windows[src_account]
        window.append(window_record)
        window_bytes = "".join(window).encode("utf-8")

        feature_values = [
            float(amt_bin),
            float(math.log1p(gap_ms)),
            float(aborts),
            float(tx_count_60s),
            float(distinct_dest_60s),
            float(_normalized_compression_distance(window_bytes, self._reference)),
            float(_compressed_size(window_bytes) / max(len(window_bytes), 1)),
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
