#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"
PYTHON_BIN=${PYTHON:-python3}
PLATFORM=$(uname -m 2>/dev/null || true)
NETWORK=zs-net
RUNTIME_MODE=

usage() {
  printf '%s\n' 'Usage: ./run.sh start|demo|replay|reset|status|eval|stop'
  printf '%s\n' 'Set ZSPECTRA_MODE=auto|docker|process (default: auto; process mode is used when Docker is unavailable).'
}

select_runtime() {
  requested=${ZSPECTRA_MODE:-auto}
  case "$requested" in
    docker)
      command -v docker >/dev/null 2>&1 || { printf '%s\n' 'Docker CLI is not installed.' >&2; exit 2; }
      docker info >/dev/null 2>&1 || { printf '%s\n' 'Docker daemon is unavailable.' >&2; exit 2; }
      RUNTIME_MODE=docker
      ;;
    process)
      RUNTIME_MODE=process
      ;;
    auto)
      if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        RUNTIME_MODE=docker
      else
        RUNTIME_MODE=process
      fi
      ;;
    *)
      printf '%s\n' 'ZSPECTRA_MODE must be auto, docker, or process.' >&2
      exit 2
      ;;
  esac
}

prepare() {
  : "${CONSORTIUM_KEY:?Set CONSORTIUM_KEY in the shell before starting bank services}"
  if [ -z "${REPLAY_TOKEN:-}" ]; then
    token_file="$ROOT_DIR/demo-data/replay-token"
    if [ -r "$token_file" ]; then
      REPLAY_TOKEN=$(cat "$token_file")
    else
      mkdir -p "$ROOT_DIR/demo-data"
      umask 077
      REPLAY_TOKEN=$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')
      printf '%s' "$REPLAY_TOKEN" > "$token_file"
    fi
  fi
  export REPLAY_TOKEN
  if ! "$PYTHON_BIN" -m tools.prepare_demo --check \
       --events "${DEMO_EVENTS:-20000}" --seed "${DEMO_SEED:-7}" \
       --output-dir "$ROOT_DIR/demo-data" --model-dir "$ROOT_DIR/models" >/dev/null 2>&1; then
    "$PYTHON_BIN" -m tools.prepare_demo \
      --events "${DEMO_EVENTS:-20000}" \
      --seed "${DEMO_SEED:-7}" \
      --output-dir "$ROOT_DIR/demo-data" \
      --model-dir "$ROOT_DIR/models"
  fi
}

configure_compression() {
  case "${ZSPECTRA_DFLTCC:-0}" in
    0)
      unset DFLTCC DFLTCC_LEVEL_MASK
      ;;
    1)
      if [ "$PLATFORM" != s390x ]; then
        printf '%s\n' 'ZSPECTRA_DFLTCC=1 is supported only on an s390x host.' >&2
        exit 2
      fi
      DFLTCC=1
      DFLTCC_LEVEL_MASK=${ZSPECTRA_DFLTCC_LEVEL_MASK:-0x007e}
      export DFLTCC DFLTCC_LEVEL_MASK
      if ! "$PYTHON_BIN" "$ROOT_DIR/tools/platform_proof.py" --require-dfltcc >/dev/null; then
        printf '%s\n' 'DFLTCC requested, but CPU or loaded zlib support was not verified.' >&2
        exit 2
      fi
      ;;
    *)
      printf '%s\n' 'ZSPECTRA_DFLTCC must be 0 or 1.' >&2
      exit 2
      ;;
  esac
}

build_images() {
  docker pull python:3.11-slim
  if [ "$PLATFORM" = s390x ]; then
    docker build --platform linux/s390x -f "$ROOT_DIR/docker/Dockerfile.bank" -t zs-bank "$ROOT_DIR"
    docker build --platform linux/s390x -f "$ROOT_DIR/docker/Dockerfile.mesh" -t zs-mesh "$ROOT_DIR"
    docker build --platform linux/s390x -f "$ROOT_DIR/docker/Dockerfile.dash" -t zs-dash "$ROOT_DIR"
    docker build --platform linux/s390x -f "$ROOT_DIR/docker/Dockerfile.replayer" -t zs-replayer "$ROOT_DIR"
  else
    docker build -f "$ROOT_DIR/docker/Dockerfile.bank" -t zs-bank "$ROOT_DIR"
    docker build -f "$ROOT_DIR/docker/Dockerfile.mesh" -t zs-mesh "$ROOT_DIR"
    docker build -f "$ROOT_DIR/docker/Dockerfile.dash" -t zs-dash "$ROOT_DIR"
    docker build -f "$ROOT_DIR/docker/Dockerfile.replayer" -t zs-replayer "$ROOT_DIR"
  fi
}

start_dashboard() {
  if [ "$RUNTIME_MODE" = process ]; then
    return 0
  fi
  docker rm -f zs-dash >/dev/null 2>&1 || true
  if [ -f "$ROOT_DIR/results.json" ]; then
    docker run -d --name zs-dash --network "$NETWORK" --network-alias dash --restart unless-stopped \
      -e MESH_URL=http://mesh:8000 -e REPLAYER_URL=http://replayer:8081 -e REPLAY_TOKEN \
      -e RESULTS_FILE=/data/results.json \
      -e PLATFORM_EVIDENCE_FILE=/data/platform-evidence.json \
      -e BANK_A_URL=http://bank-a:8001 -e BANK_B_URL=http://bank-b:8002 -e BANK_C_URL=http://bank-c:8003 \
      -p 127.0.0.1:8080:8080 \
      -v "$ROOT_DIR/demo-data/platform-evidence.json:/data/platform-evidence.json:ro" \
      -v "$ROOT_DIR/results.json:/data/results.json:ro" \
      zs-dash >/dev/null
  else
    docker run -d --name zs-dash --network "$NETWORK" --network-alias dash --restart unless-stopped \
      -e MESH_URL=http://mesh:8000 -e REPLAYER_URL=http://replayer:8081 -e REPLAY_TOKEN \
      -e RESULTS_FILE=/data/results.json \
      -e PLATFORM_EVIDENCE_FILE=/data/platform-evidence.json \
      -e BANK_A_URL=http://bank-a:8001 -e BANK_B_URL=http://bank-b:8002 -e BANK_C_URL=http://bank-c:8003 \
      -p 127.0.0.1:8080:8080 \
      -v "$ROOT_DIR/demo-data/platform-evidence.json:/data/platform-evidence.json:ro" \
      zs-dash >/dev/null
  fi
}

capture_platform() {
  "$PYTHON_BIN" "$ROOT_DIR/tools/platform_proof.py" > "$ROOT_DIR/demo-data/platform-evidence.json" || true
}

start() {
  if [ "${ZSPECTRA_REQUIRE_S390X:-1}" = 1 ] && [ "$PLATFORM" != s390x ]; then
    printf '%s\n' 'Run this command on the target LinuxONE s390x host. Set ZSPECTRA_REQUIRE_S390X=0 only for a local development run.' >&2
    exit 2
  fi
  select_runtime
  configure_compression
  "$PYTHON_BIN" "$ROOT_DIR/tools/linuxone_preflight.py"
  prepare
  if [ "$RUNTIME_MODE" = process ]; then
    "$PYTHON_BIN" -m tools.process_runner start
    capture_platform
    printf '%s\n' 'Z-SPECTRA services started in isolated-process mode. Open http://127.0.0.1:8080. Logs are under .run/logs/.'
    return 0
  fi
  build_images
  docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null

  for name in zs-bank-A zs-bank-B zs-bank-C zs-mesh zs-dash zs-replayer; do
    docker rm -f "$name" >/dev/null 2>&1 || true
  done

  docker run -d --name zs-mesh --network "$NETWORK" --network-alias mesh --add-host=host.docker.internal:host-gateway --restart unless-stopped \
    -e MODEL_FILE=/models/fraud_models.pkl \
    -e THRESHOLDS_FILE=/models/thresholds.json \
    -e CANARY_INDEX_FILE=/data/canary-index.json \
    -e LLM_URL="${LLM_URL:-http://host.docker.internal:8082/v1/chat/completions}" \
    -e LLM_MODEL="${LLM_MODEL:-local-model}" -e LLM_REASONING_EFFORT="${LLM_REASONING_EFFORT:-}" -e LLM_API_KEY \
    -e BANK_A_URL=http://bank-a:8001 -e BANK_B_URL=http://bank-b:8002 -e BANK_C_URL=http://bank-c:8003 \
    -v "$ROOT_DIR/models:/models:ro" \
    -v "$ROOT_DIR/demo-data/canary-index.json:/data/canary-index.json:ro" \
    zs-mesh >/dev/null

  for bank in A B C; do
    case "$bank" in A) port=8001 ;; B) port=8002 ;; C) port=8003 ;; esac
    bank_alias=$(printf '%s' "$bank" | tr '[:upper:]' '[:lower:]')
    docker run -d --name "zs-bank-$bank" --network "$NETWORK" --network-alias "bank-$bank_alias" --restart unless-stopped \
      -e BANK_CODE="$bank" -e CONSORTIUM_KEY -e DFLTCC -e DFLTCC_LEVEL_MASK \
      -e MESH_URL=http://mesh:8000 \
      -e PORT="$port" \
      -e ACCOUNTS_FILE=/data/accounts.json \
      -e TRAINING_METADATA_FILE=/data/training.json \
      -e CANARY_INDEX_FILE=/data/canary-index.json \
      -e OPERATOR_TOKEN \
      -v "$ROOT_DIR/demo-data/accounts/accounts-$bank.json:/data/accounts.json:ro" \
      -v "$ROOT_DIR/models/training.json:/data/training.json:ro" \
      -v "$ROOT_DIR/demo-data/canary-index.json:/data/canary-index.json:ro" \
      zs-bank >/dev/null
  done

  docker run -d --name zs-replayer --network "$NETWORK" --network-alias replayer --restart unless-stopped \
    -e REPLAY_TOKEN -e REPLAY_SPEED="${REPLAY_SPEED:-1}" \
    -e HEIST_ACCOUNTS_FILE=/data/heist-accounts.json \
    -e EVENTS_FILE=/data/events.jsonl \
    -e BANK_A_URL=http://bank-a:8001 -e BANK_B_URL=http://bank-b:8002 -e BANK_C_URL=http://bank-c:8003 \
    -p 127.0.0.1:8081:8081 \
    -v "$ROOT_DIR/demo-data/heist-accounts.json:/data/heist-accounts.json:ro" \
    -v "$ROOT_DIR/demo-data/events.jsonl:/data/events.jsonl:ro" \
    zs-replayer >/dev/null

  mkdir -p "$ROOT_DIR/demo-data"
  if [ ! -e "$ROOT_DIR/demo-data/platform-evidence.json" ]; then
    printf '{}\n' > "$ROOT_DIR/demo-data/platform-evidence.json"
  fi
  start_dashboard
  capture_platform

  printf '%s\n' 'Z-SPECTRA services started. Open http://127.0.0.1:8080.'
}

control() {
  if [ -z "${REPLAY_TOKEN:-}" ] && [ -r "$ROOT_DIR/demo-data/replay-token" ]; then
    REPLAY_TOKEN=$(cat "$ROOT_DIR/demo-data/replay-token")
    export REPLAY_TOKEN
  fi
  "$PYTHON_BIN" "$ROOT_DIR/tools/control.py" "$1"
}

command=${1:-}
case "$command" in
  start) start ;;
  demo)
    start
    control heist
    ;;
  replay)
    control replay
    ;;
  reset) control reset ;;
  status)
    if [ -d "$ROOT_DIR/demo-data" ]; then
      capture_platform
    fi
    control status
    ;;
  eval)
    start
    "$PYTHON_BIN" -m bench.bench \
      --events "${DEMO_EVENTS:-20000}" \
      --seeds "${EVAL_SEEDS:-3}" \
      --live-repeats "${EVAL_LIVE_REPEATS:-10}"
    start_dashboard
    capture_platform
    ;;
  stop)
    "$PYTHON_BIN" -m tools.process_runner stop >/dev/null 2>&1 || true
    for name in zs-bank-A zs-bank-B zs-bank-C zs-mesh zs-dash zs-replayer; do
      docker rm -f "$name" >/dev/null 2>&1 || true
    done
    ;;
  *) usage; exit 2 ;;
esac
