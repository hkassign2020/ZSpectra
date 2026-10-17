# Z-SPECTRA v2

Z-SPECTRA is a synthetic cross-bank mule-cascade demonstrator. Each bank service processes raw synthetic events locally, uses an HMAC-SHA256 consortium key to create pseudonymous BIDs, and sends only the frozen bank-to-mesh fields. The mesh links blind features, scores a chain, and sends a hold recommendation for operator review. Rollback counts are simulated rollback telemetry.

The replay controller is a test harness: it supplies synthetic events to bank APIs and has no consortium key. Generated canaries and raw replay data stay under `demo-data/`; the replay controller reads them, while the mesh receives only the digest-only canary index and the dashboard receives blind mesh state. The raw replay directory must not be mounted into the mesh or dashboard.

The containers are intended to be built on IBM LinuxONE (`s390x`). The project uses Python, NumPy, scikit-learn, Flask, requests, Docker, and plain HTML/CSS/JavaScript. The template brief is identified in the dashboard. Benchmark results are shown only when an evaluation-generated `results.json` is present.

## Run the demo

On the target LinuxONE host, install the dependencies, set a fresh key in the shell, and run the demo command:

```sh
python3 -m pip install -r requirements-dev.txt
export CONSORTIUM_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
./run.sh demo
```

The dashboard opens at `http://127.0.0.1:8080`. `./run.sh status` checks it, `./run.sh reset` clears the live demo state, and `./run.sh stop` removes the containers. `./run.sh eval` runs the evaluation and writes `results.json` from observed measurements. `python3 tools/platform_proof.py` captures the host evidence; run it on LinuxONE for target evidence.

For a local development run on a different architecture, set `ZSPECTRA_REQUIRE_S390X=0`. That mode does not establish LinuxONE compatibility or platform results. Build the target images on LinuxONE and inspect the output of `docker pull python:3.11-slim` before relying on the base image there.

Run the unit suite with `python3 -m pytest -q`. Read [SPEC.md](SPEC.md) for the frozen wire contracts, trust zones, evaluation protocol, and acceptance criteria.
