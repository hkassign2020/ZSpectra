"""Host-side commands for the local dashboard and replay-control APIs."""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("heist", "replay", "reset", "status", "negative-control"))
    parser.add_argument("--dashboard", default=os.environ.get("DASH_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--replayer", default=os.environ.get("REPLAYER_URL_HOST", "http://127.0.0.1:8081"))
    args = parser.parse_args()

    try:
        if args.command in {"heist", "replay"}:
            token = os.environ.get("REPLAY_TOKEN", "")
            if not token:
                raise RuntimeError("REPLAY_TOKEN is required for replay controls")
            response = requests.post(
                f"{args.replayer}/{args.command}",
                headers={"X-Replay-Token": token},
                json={},
                timeout=120,
            )
        else:
            endpoint = {
                "reset": "/api/reset",
                "status": "/api/health",
                "negative-control": "/api/negative-control",
            }[args.command]
            if args.command in {"reset", "negative-control"}:
                response = requests.post(f"{args.dashboard}{endpoint}", json={}, timeout=15)
            else:
                response = requests.get(f"{args.dashboard}{endpoint}", timeout=15)
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2, sort_keys=True))
        return 0
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
