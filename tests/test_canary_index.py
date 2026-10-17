import hashlib
import json

from audit.scanner import CanaryScanner
from tools.prepare_canary_index import build_digest_index, write_digest_index


def test_index_contains_digests_and_scanner_finds_source_value(tmp_path):
    canaries = ["Fake Example", "ID-FAKE-001", "memo token"]
    index = build_digest_index(canaries)
    serialized = json.dumps(index)

    assert all(value not in serialized for value in canaries)
    assert {entry["sha256"] for entry in index["entries"]} == {
        hashlib.sha256(value.encode("utf-8")).hexdigest() for value in canaries
    }

    path = tmp_path / "index.json"
    write_digest_index(canaries, path)
    scanner = CanaryScanner.from_index_file(path)
    assert scanner.scan("message", "a memo token escaped") == 1
