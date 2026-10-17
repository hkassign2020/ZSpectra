#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"
PYTHON_BIN=${PYTHON:-python3}
PLATFORM=$(uname -m 2>/dev/null || true)
NETWORK=zs-net

usage() {
  printf '%s\n' 'Usage: ./run.sh start|demo|replay|reset|status|eval|stop'
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
       --output-dir "$ROOT_DIR/demo-data" --model-dir "$ROOT_DIR/models" >/dev/null 2>&1; then
    "$PYTHON_BIN" -m tools.prepare_demo \
      --events "${DEMO_EVENTS:-60000}" \
      --output-dir "$ROOT_DIR/demo-data" \
      --model-dir "$ROOT_DIR/models"
  fi
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
  prepare
  build_images
  docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null

  for name in zs-bank-A zs-bank-B zs-bank-C zs-mesh zs-dash zs-replayer; do
    docker rm -f "$name" >/dev/null 2>&1 || true
  done

  docker run -d --name zs-mesh --network "$NETWORK" --network-alias mesh --restart unless-stopped \
    -e MODEL_FILE=/models/fraud_models.pkl \
    -e THRESHOLDS_FILE=/models/thresholds.json \
    -e CANARY_INDEX_FILE=/data/canary-index.json \
    -e LLM_API_URL -e LLM_API_KEY \
    -e BANK_A_URL=http://bank-a:8001 -e BANK_B_URL=http://bank-b:8002 -e BANK_C_URL=http://bank-c:8003 \
    -v "$ROOT_DIR/models:/models:ro" \
    -v "$ROOT_DIR/demo-data/canary-index.json:/data/canary-index.json:ro" \
    zs-mesh >/dev/null

  for bank in A B C; do
    case "$bank" in A) port=8001 ;; B) port=8002 ;; C) port=8003 ;; esac
    bank_alias=$(printf '%s' "$bank" | tr '[:upper:]' '[:lower:]')
    docker run -d --name "zs-bank-$bank" --network "$NETWORK" --network-alias "bank-$bank_alias" --restart unless-stopped \
      -e BANK_CODE="$bank" -e CONSORTIUM_KEY \
      -e MESH_URL=http://mesh:8000 \
      -e PORT="$port" \
      -e ACCOUNTS_FILE=/data/accounts.json \
      -e CANARY_INDEX_FILE=/data/canary-index.json \
      -e OPERATOR_TOKEN \
      -v "$ROOT_DIR/demo-data/accounts/accounts-$bank.json:/data/accounts.json:ro" \
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
    "$PYTHON_BIN" -m bench.bench
    start_dashboard
    capture_platform
    ;;
  stop)
    for name in zs-bank-A zs-bank-B zs-bank-C zs-mesh zs-dash zs-replayer; do
      docker rm -f "$name" >/dev/null 2>&1 || true
    done
    ;;
  *) usage; exit 2 ;;
esac
