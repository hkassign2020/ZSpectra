import pytest

from audit import AuditValidationError, Auditor, BoundaryViolation, CanaryScanner


def bank_to_mesh_payload():
    return {
        "bank": "A",
        "seq": 1,
        "t_ms": 1,
        "src_bid": "BID_" + "a" * 12,
        "dst_bid": "BID_" + "b" * 12,
        "amt_bin": 1.25,
        "f": [0.0] * 8,
        "sensor_us": 1,
    }


def test_valid_bank_to_mesh_payload_passes():
    auditor = Auditor()

    assert auditor.check("bank_to_mesh", bank_to_mesh_payload()) is True
    assert auditor.blocked_count == 0


def test_boundary_violation_is_a_compatibility_alias():
    assert BoundaryViolation is AuditValidationError


def test_valid_mesh_to_llm_payload_passes():
    auditor = Auditor()
    payload = {
        "chain": [["A", "BID_" + "a" * 12, 20, 1.25]],
        "p_cascade": 0.75,
        "anom": 0.1,
        "evidence": [["log_amount_bin", 90.0], ["hop_depth", 80.0]],
    }

    assert auditor.check("mesh_to_llm", payload) is True
    assert auditor.blocked_count == 0


def test_valid_mesh_to_bank_payload_passes():
    assert Auditor().check(
        "mesh_to_bank", {"bid": "BID_" + "c" * 12, "alert_id": "AL_0123aBcD"}
    ) is True


@pytest.mark.parametrize("channel", ["bank_to_mesh", "mesh_to_llm", "mesh_to_bank"])
def test_unknown_or_wrong_payload_is_blocked_and_counted(channel):
    auditor = Auditor()

    with pytest.raises(AuditValidationError):
        auditor.check(channel, {"unexpected": "payload"})

    assert auditor.blocked_count == 1


def test_extra_keys_are_rejected():
    payload = bank_to_mesh_payload()
    payload["raw_account"] = "sensitive"
    auditor = Auditor()

    with pytest.raises(AuditValidationError):
        auditor.check("bank_to_mesh", payload)

    assert auditor.blocked_count == 1


def test_non_finite_feature_is_rejected():
    payload = bank_to_mesh_payload()
    payload["f"][0] = float("nan")
    auditor = Auditor()

    with pytest.raises(AuditValidationError):
        auditor.check("bank_to_mesh", payload)


def test_long_strings_are_rejected():
    payload = bank_to_mesh_payload()
    payload["bank"] = "A" * 33

    with pytest.raises(AuditValidationError):
        Auditor().check("bank_to_mesh", payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("seq", True),
        ("t_ms", 1.5),
        ("src_bid", "BID_" + "A" * 12),
        ("amt_bin", 1.1),
        ("amt_bin", True),
        ("f", [0.0] * 7),
        ("f", [0.0] * 7 + [float("inf")]),
        ("sensor_us", -0.1),
    ],
)
def test_invalid_bank_to_mesh_fields_are_blocked(field, value):
    payload = bank_to_mesh_payload()
    payload[field] = value

    with pytest.raises(AuditValidationError):
        Auditor().check("bank_to_mesh", payload)


def test_unknown_channel_is_blocked():
    auditor = Auditor()

    with pytest.raises(AuditValidationError):
        auditor.check("somewhere_else", {})

    assert auditor.blocked_count == 1


def test_canary_scanner_does_not_retain_source_strings_and_counts_hits():
    scanner = CanaryScanner(["CANARY-NAME-01", "CANARY-ACCOUNT-02"])
    assert "CANARY-NAME-01" not in repr(scanner.__dict__)

    message = "message mentions CANARY-NAME-01"
    assert scanner.scan("mesh_to_llm", message) == 1
    assert scanner.messages == 1
    assert scanner.bytes_scanned == len(message.encode("utf-8"))
    assert scanner.hits == 1

    # The direct text-only form remains useful for isolated scanner checks.
    assert scanner.scan("nothing to see here") == 0
    assert scanner.messages == 2


def test_auditor_reset_clears_blocked_counter():
    auditor = Auditor()
    with pytest.raises(AuditValidationError):
        auditor.check("bank_to_mesh", {})
    auditor.reset()
    assert auditor.blocked_count == 0
