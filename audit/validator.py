"""Strict allowlist validation for messages crossing zone boundaries."""

from __future__ import annotations

import math
import re
from typing import Any

from contracts.schema import CHANNEL_ALLOWLISTS, MESH_FEATURES, SENSOR_FEATURES


BID_PATTERN = re.compile(r"^BID_[0-9a-f]{12}$")
ALERT_PATTERN = re.compile(r"^AL_[0-9a-fA-F]{8}$")
BANKS = frozenset({"A", "B", "C"})


class AuditValidationError(ValueError):
    """Raised when an outbound message violates a frozen wire contract."""


BoundaryViolation = AuditValidationError


def _fail(path: str, reason: str) -> None:
    raise AuditValidationError(f"{path}: {reason}")


def _exact_fields(channel: str, payload: Any) -> dict[str, Any]:
    if type(payload) is not dict:
        _fail(channel, "payload must be a dictionary")
    expected = frozenset(CHANNEL_ALLOWLISTS[channel])
    actual = frozenset(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        _fail(channel, f"field set mismatch (missing={missing}, extra={extra})")
    return payload


def _integer(value: Any, path: str, *, minimum: int | None = None) -> None:
    if type(value) is not int:
        _fail(path, "must be an integer (booleans are not accepted)")
    if minimum is not None and value < minimum:
        _fail(path, f"must be at least {minimum}")


def _number(value: Any, path: str, *, finite: bool = True, minimum: float | None = None,
            maximum: float | None = None, require_float: bool = False) -> None:
    accepted = (float,) if require_float else (int, float)
    if type(value) not in accepted:
        _fail(path, "must be a number (booleans are not accepted)")
    if finite and type(value) is float and not math.isfinite(value):
        _fail(path, "must be finite")
    if minimum is not None and value < minimum:
        _fail(path, f"must be at least {minimum}")
    if maximum is not None and value > maximum:
        _fail(path, f"must be at most {maximum}")


def _bank(value: Any, path: str) -> None:
    if type(value) is not str or value not in BANKS:
        _fail(path, "must be one of A, B, or C")


def _bid(value: Any, path: str) -> None:
    if type(value) is not str or BID_PATTERN.fullmatch(value) is None:
        _fail(path, "must match BID_ followed by 12 lowercase hexadecimal digits")


def _walk_string_limits(value: Any, path: str) -> None:
    if type(value) is str:
        if len(value) > 32:
            _fail(path, "strings may contain at most 32 characters")
    elif type(value) is list:
        for index, item in enumerate(value):
            _walk_string_limits(item, f"{path}[{index}]")
    elif type(value) is dict:
        for key, item in value.items():
            _walk_string_limits(key, f"{path}.<key>")
            _walk_string_limits(item, f"{path}.{key}")


def _validate_bank_to_mesh(payload: dict[str, Any]) -> None:
    _bank(payload["bank"], "bank")
    _integer(payload["seq"], "seq", minimum=0)
    _integer(payload["t_ms"], "t_ms")
    _bid(payload["src_bid"], "src_bid")
    _bid(payload["dst_bid"], "dst_bid")
    amount = payload["amt_bin"]
    _number(amount, "amt_bin", require_float=True)
    if abs(amount * 4 - round(amount * 4)) > 1e-9:
        _fail("amt_bin", "must be a multiple of 0.25")
    features = payload["f"]
    if type(features) is not list or len(features) != 8:
        _fail("f", "must be a list containing exactly 8 floats")
    for index, value in enumerate(features):
        _number(value, f"f[{index}]", require_float=True)
    _number(payload["sensor_us"], "sensor_us", minimum=0)


def _validate_mesh_to_llm(payload: dict[str, Any]) -> None:
    chain = payload["chain"]
    if type(chain) is not list:
        _fail("chain", "must be a list")
    for index, hop in enumerate(chain):
        path = f"chain[{index}]"
        if type(hop) is not list or len(hop) != 4:
            _fail(path, "must be a four-item list [bank, bid, dt_ms, amt_bin]")
        _bank(hop[0], f"{path}[0]")
        _bid(hop[1], f"{path}[1]")
        _integer(hop[2], f"{path}[2]", minimum=0)
        _number(hop[3], f"{path}[3]")
    _number(payload["p_cascade"], "p_cascade", minimum=0, maximum=1)
    _number(payload["anom"], "anom", minimum=0, maximum=1)

    evidence = payload["evidence"]
    if type(evidence) is not list or len(evidence) > 3:
        _fail("evidence", "must be a list with at most 3 entries")
    known_features = frozenset(SENSOR_FEATURES) | frozenset(MESH_FEATURES)
    for index, item in enumerate(evidence):
        path = f"evidence[{index}]"
        if type(item) is not list or len(item) != 2:
            _fail(path, "must be a two-item list [feature_name, percentile]")
        name, percentile = item
        if type(name) is not str or name not in known_features:
            _fail(f"{path}[0]", "must name a sensor or mesh feature")
        _number(percentile, f"{path}[1]", minimum=0, maximum=100)


def _validate_mesh_to_bank(payload: dict[str, Any]) -> None:
    _bid(payload["bid"], "bid")
    alert_id = payload["alert_id"]
    if type(alert_id) is not str or ALERT_PATTERN.fullmatch(alert_id) is None:
        _fail("alert_id", "must match AL_ followed by 8 hexadecimal digits")


class Auditor:
    """Validate outbound messages and count rejected sends."""

    def __init__(self) -> None:
        self.blocked_count = 0

    def reset(self) -> None:
        self.blocked_count = 0

    def check(self, channel: str, payload: Any) -> bool:
        try:
            if type(channel) is not str or channel not in CHANNEL_ALLOWLISTS:
                _fail("channel", "must be bank_to_mesh, mesh_to_llm, or mesh_to_bank")
            checked = _exact_fields(channel, payload)
            if channel == "bank_to_mesh":
                _validate_bank_to_mesh(checked)
            elif channel == "mesh_to_llm":
                _validate_mesh_to_llm(checked)
            else:
                _validate_mesh_to_bank(checked)
            _walk_string_limits(checked, channel)
        except AuditValidationError:
            self.blocked_count += 1
            raise
        return True


AuditValidator = Auditor
_default_auditor = Auditor()
auditor = _default_auditor


def check(channel: str, payload: Any) -> bool:
    """Module-level check function for call sites that share the default auditor."""
    return _default_auditor.check(channel, payload)


def get_blocked_count() -> int:
    return _default_auditor.blocked_count
