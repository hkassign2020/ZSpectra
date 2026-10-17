"""Boundary validation and synthetic canary auditing."""

from .scanner import CanaryScanner
from .validator import (
    AuditValidationError,
    AuditValidator,
    Auditor,
    BoundaryViolation,
    auditor,
    check,
    get_blocked_count,
)

__all__ = [
    "AuditValidationError",
    "AuditValidator",
    "Auditor",
    "BoundaryViolation",
    "CanaryScanner",
    "auditor",
    "check",
    "get_blocked_count",
]
