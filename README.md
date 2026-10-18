# Z-SPECTRA v2

Z-SPECTRA is a synthetic cross-bank mule-cascade demonstrator. Each bank service processes synthetic raw events locally, uses HMAC-SHA256 to create pseudonymous BIDs, and sends only the frozen bank-to-mesh fields. The mesh links blind features, scores chains, and sends a hold recommendation for operator review. Rollback counts are simulated rollback telemetry.

The replay controller is a test harness with no consortium key. Generated canaries and raw replay data stay under `demo-data/`; the mesh receives only the digest-only canary index. The raw replay directory is not mounted into the mesh or dashboard. BIDs are pseudonymous and linkable, not anonymous.

## LinuxONE preflight

Run these commands on the L1CC VM. They report the host architecture, byte order, whether NumPy, scikit-learn, Flask, and requests import, and whether the Docker CLI and daemon respond:

```sh
python3 tools/linuxone_preflight.py
```

To test a clean pip installation without changing the VM's system packages, run:

```sh
python3 tools/linuxone_preflight.py --install-check
```

The clean install uses a temporary directory and removes it afterward. The Docker CLI/daemon check and clean pip check are separate: successful Python imports alone do not prove that all four images build. `./run.sh start` performs the actual image builds when Docker is selected.

The mesh image needs NumPy and scikit-learn. The bank, dashboard, and replay images install only Flask and requests. The default runner mode is `auto`: it uses Docker when the CLI and daemon are available, otherwise it starts isolated localhost-bound processes. Force a mode with `ZSPECTRA_MODE=docker` or `ZSPECTRA_MODE=process`.

Process mode is a functional fallback, not container-equivalent isolation: each service gets a separate environment, the consortium key is passed only to bank processes, and ports bind to loopback, but the services run under the invoking OS account. Use Docker when the demo needs container isolation.

For x86 local development, set `ZSPECTRA_REQUIRE_S390X=0`; this allows a development run but does not establish LinuxONE compatibility.

## Run the demo

Set a fresh key in the shell and start the scripted heist. The default preparation size is `DEMO_EVENTS=20000`.

```sh
export CONSORTIUM_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
./run.sh demo
```

Open `http://127.0.0.1:8080`. Use `./run.sh status` to check the services, `./run.sh reset` to clear demo state, and `./run.sh stop` to stop the services. Process-mode logs are under `.run/logs/`.

`./run.sh eval` writes observed measurements to `results.json`; its default evaluation uses `DEMO_EVENTS=20000`, three seeds, and ten live repeats at each replay speed (1x, 5x, and 10x). It reports alert decision latency and end-to-end hold-delivery latency at each speed, plus remaining transfer value and alert timing from held-out ground truth. Override the data size, seed count, and repeats with `DEMO_EVENTS`, `EVAL_SEEDS`, and `EVAL_LIVE_REPEATS`. Ten repeats give a basic spread estimate; p99 is still rough at this sample count. The evaluation is a synthetic demonstration, not a production efficacy result.

## LLM brief

The mesh calls the OpenAI-compatible chat endpoint at `LLM_URL` with `LLM_MODEL` and optional `LLM_API_KEY`. The default endpoint is the Lab 2 service on the host at `http://host.docker.internal:8082/v1/chat/completions` in Docker mode, and `http://127.0.0.1:8082/v1/chat/completions` in process mode. The request uses a 60-second timeout, `max_tokens=150`, and `temperature=0.2`; failures use a labeled template brief.

```sh
export LLM_URL=http://127.0.0.1:8082/v1/chat/completions
export LLM_MODEL=your-loaded-granite-model
ZSPECTRA_MODE=process ./run.sh demo
```

The Docker mesh container gets `--add-host=host.docker.internal:host-gateway`, and `run.sh` sets `LLM_URL` to that host endpoint. The exact compact blind JSON shown in the dashboard is sent as the user message; the separate system message contains the safety instructions. The payload is validated and scanned before sending, and the response is scanned before display. `llm_mode` is `local`, `api`, or `template`; the dashboard shows a pending alert immediately and fills in the brief when generation completes. `LLM_API_KEY` is passed only to the mesh process/container.

The endpoint must expose `POST /v1/chat/completions` with the OpenAI chat-completions request shape.

## Results and limits

No `results.json` is checked in; the dashboard therefore shows no measured evaluation until `./run.sh eval` creates it. The zlib hardware toggle is not configured by default. No DFLTCC acceleration claim is made; a DFLTCC flag alone is not evidence that the toggle changed compression behavior.

The generator's features and labels are near-separable by construction. A near-perfect score would show that this synthetic task is too easy, not that the detector is ready for banking traffic. Results need harder independent data before they can support a real-world performance claim.

For evidence from the target VM, run `python3 tools/platform_proof.py` there. For the frozen wire contracts, privacy zones, evaluation protocol, and acceptance criteria, read [SPEC.md](SPEC.md).
