import hashlib
import json

from audit.logging import ScannerLogHandler
from audit.scanner import CanaryScanner, NEGATIVE_CONTROL_TOKEN


def test_digest_index_scans_without_retaining_source_canaries(tmp_path):
    canary = "FAKE-CUSTOMER-NAME"
    raw = canary.encode("utf-8")
    prefix = raw[:8]
    index = tmp_path / "canary-index.json"
    index.write_text(
        json.dumps(
            {
                "version": 2,
                "entries": [
                    {
                        "length": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "prefix_length": len(prefix),
                        "prefix_sha256": hashlib.sha256(prefix).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    scanner = CanaryScanner.from_index_file(index)

    assert scanner.scan("mesh_to_llm", f"payload contains {canary}") == 1
    assert canary not in repr(scanner.__dict__)
    assert NEGATIVE_CONTROL_TOKEN not in repr(scanner.__dict__)


def test_negative_control_is_always_caught_and_reset_clears_counters():
    scanner = CanaryScanner()

    assert scanner.scan("negative_control", NEGATIVE_CONTROL_TOKEN) == 1
    assert scanner.hits == 1
    scanner.reset()
    assert (scanner.messages, scanner.bytes_scanned, scanner.hits) == (0, 0, 0)


def test_logging_handler_scans_formatted_log_line():
    scanner = CanaryScanner(["FAKE-LOG-CANARY"])
    handler = ScannerLogHandler(scanner)
    import logging

    record = logging.LogRecord("bank", logging.WARNING, "service.py", 1, "leaked %s", ("FAKE-LOG-CANARY",), None)
    handler.emit(record)

    assert scanner.messages == 1
    assert scanner.hits == 1
