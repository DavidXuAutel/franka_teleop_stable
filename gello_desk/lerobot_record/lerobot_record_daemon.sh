#!/usr/bin/env bash
# Start/stop/status for LeRobot record HTTP daemon.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${LOG_DIR:-/home/yao/gello_logs}"
PID_FILE="$LOG_DIR/lerobot_record.pid"
LOG_FILE="$LOG_DIR/lerobot_record.log"
ENV_FILE="${LEROBOT_RECORD_ENV:-$DIR/.env}"

mkdir -p "$LOG_DIR"

activate_env() {
  # Prefer conda lerobot; fall back to current python.
  if [[ -f /home/yao/anaconda3/etc/profile.d/conda.sh ]]; then
    # shellcheck disable=SC1091
    source /home/yao/anaconda3/etc/profile.d/conda.sh
    conda activate lerobot 2>/dev/null || true
  fi
  # Avoid editable workspace package shadowing pip/conda install
  unset PYTHONPATH
  export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"

  # ROS2 for rclpy / messages (ament scripts reference unset vars)
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  # shellcheck disable=SC1091
  source /home/yao/franka_ros2_ws/install/setup.bash
  set -u
  export LD_LIBRARY_PATH="/home/yao/franka_ros2_ws/install/libfranka/lib:/home/yao/genie_sim/source/teleop/app/vendors/lib:/opt/ros/humble/lib/x86_64-linux-gnu:/opt/ros/humble/lib:${LD_LIBRARY_PATH:-}"

  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

cmd_start() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "already running pid=$(cat "$PID_FILE")"
    exit 0
  fi
  activate_env
  cd "$DIR"
  nohup python -u record_server.py >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  sleep 1
  if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "started pid=$(cat "$PID_FILE") log=$LOG_FILE"
  else
    echo "failed to start; see $LOG_FILE" >&2
    exit 1
  fi
}

cmd_stop() {
  if [[ -f "$PID_FILE" ]]; then
    pid=$(cat "$PID_FILE")
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "stopped"
  else
    pkill -f "python -u record_server.py" 2>/dev/null || true
    echo "stopped (no pid file)"
  fi
}

cmd_status() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "daemon running pid=$(cat "$PID_FILE")"
  else
    echo "daemon not running"
  fi
  host="${LEROBOT_RECORD_HOST:-127.0.0.1}"
  port="${LEROBOT_RECORD_PORT:-8765}"
  curl -sS "http://${host}:${port}/record/status" || true
  echo
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) activate_env; cmd_status ;;
  restart) cmd_stop; sleep 1; cmd_start ;;
  *) echo "Usage: $0 {start|stop|status|restart}"; exit 1 ;;
esac
