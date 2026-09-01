#!/usr/bin/env bash
# Per-episode start/stop/status for LeRobot recorder (HTTP client).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${LEROBOT_RECORD_ENV:-$DIR/.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

HOST="${LEROBOT_RECORD_HOST:-127.0.0.1}"
PORT="${LEROBOT_RECORD_PORT:-8765}"
BASE="http://${HOST}:${PORT}"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") start [--repo NAME] [--task TEXT]
  $(basename "$0") stop
  $(basename "$0") status
EOF
}

cmd_status() {
  curl -sS "$BASE/record/status"
  echo
}

cmd_stop() {
  curl -sS -X POST "$BASE/record/stop"
  echo
}

cmd_start() {
  repo="${DEFAULT_REPO:-fr3_gello_teleop}"
  task="${DEFAULT_TASK:-franka gello teleop}"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo) repo="$2"; shift 2 ;;
      --task) task="$2"; shift 2 ;;
      *) echo "unknown arg: $1" >&2; usage; exit 1 ;;
    esac
  done
  payload=$(python3 -c 'import json,sys; print(json.dumps({"repo":sys.argv[1],"task":sys.argv[2]}))' "$repo" "$task")
  curl -sS -X POST "$BASE/record/start" -H 'Content-Type: application/json' -d "$payload"
  echo
}

case "${1:-}" in
  start) shift; cmd_start "$@" ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  *) usage; exit 1 ;;
esac
