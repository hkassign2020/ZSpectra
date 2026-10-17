"""Build a digest-only canary index for the blind mesh scanner."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable


_PREFIX_BYTES = 16


def build_digest_index(canaries: Iterable[str]) -> dict[str, object]:
    entries: set[tuple[int, str, int, str]] = set()
    for canary in canaries:
        if type(canary) is not str:
            raise TypeError("canaries must be strings")
        raw = canary.encode("utf-8")
        if raw:
            prefix_length = min(_PREFIX_BYTES, len(raw))
            entries.add(
                (
                    len(raw),
                    hashlib.sha256(raw).hexdigest(),
                    prefix_length,
                    hashlib.sha256(raw[:prefix_length]).hexdigest(),
                )
            )
    return {
        "version": 2,
        "entries": [
            {
                "length": length,
                "sha256": digest,
                "prefix_length": prefix_length,
                "prefix_sha256": prefix_digest,
            }
            for length, digest, prefix_length, prefix_digest in sorted(entries)
        ],
    }


def write_digest_index(canaries: Iterable[str], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_digest_index(canaries), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON file containing a list of raw synthetic canaries")
    parser.add_argument("output", type=Path, help="digest-only JSON output mounted into the mesh")
    args = parser.parse_args()
    canaries = json.loads(args.input.read_text(encoding="utf-8"))
    if type(canaries) is not list:
        raise SystemExit("canary input must be a JSON list")
    write_digest_index(canaries, args.output)
    print(f"Wrote digest-only canary index to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
