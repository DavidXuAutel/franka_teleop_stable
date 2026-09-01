#!/usr/bin/env bash
# One-shot launcher: FCI + GELLO teleop + MuJoCo + dual RealSense ROS +
# live dual-cam viewer + LeRobot record daemon.
#
# Usage:
#   bash start_teleop_lerobot_all.sh start
#   bash start_teleop_lerobot_all.sh stop
#   bash start_teleop_lerobot_all.sh status
#   bash start_teleop_lerobot_all.sh restart
#   bash start_teleop_lerobot_all.sh episode-start [--repo NAME] [--task TEXT]
#   bash start_teleop_lerobot_all.sh episode-stop
#   bash start_teleop_lerobot_all.sh episode-status
set -eo pipefail

DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROBOT_IP="${ROBOT_IP:-10.229.66.91}"
LOG_DIR="${LOG_DIR:-/home/yao/gello_logs}"
DISPLAY="${DISPLAY:-:1}"
DESK_PASSWORD="${DESK_PASSWORD:-franka123}"
export DESK_PASSWORD ROBOT_IP DISPLAY LOG_DIR

mkdir -p "$LOG_DIR"

usage() {
  cat <<EOF
Usage: $(basename "$0") <command>

  start            Teleop + MuJoCo + dual cams + image viewer + record daemon
  stop             Stop all of the above
  status           Process / FCI / stream summary
  restart          stop && start

  episode-start    Start one LeRobot episode  [--repo NAME] [--task TEXT]
  episode-stop     Stop current episode and sync remote
  episode-status   Recorder HTTP status

Environment:
  ROBOT_IP       default 10.229.66.91
  DISPLAY        default :1  (MuJoCo + OpenCV viewer)
  LOG_DIR        default /home/yao/gello_logs
  DESK_PASSWORD  Desk API password
EOF
}

source_ros() {
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  # shellcheck disable=SC1091
  source /home/yao/franka_ros2_ws/install/setup.bash
  set -u
  export LD_LIBRARY_PATH="/home/yao/franka_ros2_ws/install/libfranka/lib:/home/yao/genie_sim/source/teleop/app/vendors/lib:/opt/ros/humble/lib/x86_64-linux-gnu:/opt/ros/humble/lib:${LD_LIBRARY_PATH:-}"
  # shellcheck disable=SC1091
  source /home/yao/gello_desk/env.sh 2>/dev/null || true
}

stop_all() {
  echo "[stop] LeRobot record daemon..."
  bash "$DIR/lerobot_record_daemon.sh" stop 2>/dev/null || true

  echo "[stop] dual cam viewer..."
  pkill -f "cam_view_dual.py" 2>/dev/null || true
  pkill -f "rqt_image_view" 2>/dev/null || true

  echo "[stop] dual RealSense..."
  bash "$DIR/start_dual_realsense.sh" stop 2>/dev/null || true

  echo "[stop] MuJoCo mirrors + replay windows..."
  pkill -f mujoco_ros_mirror.py 2>/dev/null || true
  pkill -f mujoco_replay.py 2>/dev/null || true
  pkill -f cam_replay.py 2>/dev/null || true
  sleep 1
  pkill -9 -f mujoco_ros_mirror.py 2>/dev/null || true
  pkill -9 -f mujoco_replay.py 2>/dev/null || true
  pkill -9 -f cam_replay.py 2>/dev/null || true

  echo "[stop] teleop error watchdog..."
  pkill -f teleop_error_watchdog.py 2>/dev/null || true

  echo "[stop] GELLO teleop stack..."
  bash /home/yao/gello_launch.sh stop 2>/dev/null || true

  # leftover single-camera launches from older scripts
  pkill -f "ros2 bag record" 2>/dev/null || true
  sleep 2
  echo "[stop] Done."
}

wait_topic() {
  local topic="$1"
  local n="${2:-25}"
  for _ in $(seq 1 "$n"); do
    # ros2 topic list can hang forever when ros2cli/DDS is wedged
    if timeout 3 ros2 topic list 2>/dev/null | grep -qx "$topic"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

cmd_start() {
  source_ros

  if ! ls /dev/serial/by-id/usb-FTDI_* >/dev/null 2>&1; then
    echo "ERROR: GELLO USB not found."
    ls -la /dev/serial/by-id/ 2>/dev/null || true
    exit 1
  fi

  echo "[0/7] Link preflight (mandatory before teleop)..."
  if ! bash /home/yao/gello_desk/link_preflight.sh; then
    echo "ABORT: link preflight failed — not starting teleop."
    echo "中止：链路预检失败，未启动遥操。"
    exit 1
  fi

  echo "[1/7] Desk API: FCI + unlock..."
  python3 /home/yao/gello_desk/desk_prep.py --host "$ROBOT_IP" --recover \
    | tee "$LOG_DIR/desk_prep_all.log" | tail -8

  echo "[2/7] GELLO teleop (publisher -> arm -> gripper)..."
  LINK_PREFLIGHT_OK=1 ROBOT_IP="$ROBOT_IP" bash /home/yao/gello_launch.sh 2>&1 \
    | tee "$LOG_DIR/teleop_all.log" | tail -12

  echo "[3/7] MuJoCo GPU mirrors..."
  export DISPLAY
  if bash /home/yao/gello_desk/start_mujoco_gpu.sh 2>&1 | tee "$LOG_DIR/mujoco_all.log"; then
    :
  else
    echo "WARN: MuJoCo may need desktop login on DISPLAY=$DISPLAY"
    tail -5 "$LOG_DIR/mujoco_franka.log" 2>/dev/null || true
  fi

  echo "[4/7] Dual RealSense ROS nodes (cam1 D435I + cam2 D435)..."
  bash "$DIR/start_dual_realsense.sh" start 2>&1 | tee "$LOG_DIR/dual_realsense_boot.log" | tail -20
  if ! wait_topic "/cam1/cam1/color/image_raw" 30; then
    echo "WARN: cam1 topic not ready"
  fi
  if ! wait_topic "/cam2/cam2/color/image_raw" 10; then
    echo "WARN: cam2 topic not ready"
  fi

  echo "[5/7] Image frame viewer + START/STOP buttons..."
  pkill -f "cam_view_dual.py" 2>/dev/null || true
  if [[ -f "$DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$DIR/.env"
    set +a
  fi
  nohup python3 "$DIR/cam_view_dual.py" \
    --cam1 /cam1/cam1/color/image_raw \
    --cam2 /cam2/cam2/color/image_raw \
    --api "http://127.0.0.1:${LEROBOT_RECORD_PORT:-8765}" \
    --repo "${DEFAULT_REPO:-fr3_gello_teleop}" \
    --task "${DEFAULT_TASK:-franka gello teleop}" \
    >"$LOG_DIR/cam_view_dual.log" 2>&1 &
  echo $! >"$LOG_DIR/cam_view_dual.pid"
  echo "viewer pid=$(cat "$LOG_DIR/cam_view_dual.pid")  DISPLAY=$DISPLAY  (click START/STOP on window)"

  echo "[6/7] LeRobot record daemon..."
  bash "$DIR/lerobot_record_daemon.sh" restart
  sleep 2

  echo "[7/7] Teleop anomaly watchdog (fault>${TELEOP_FAULT_HOLD_S:-10}s → full recover)..."
  pkill -f teleop_error_watchdog.py 2>/dev/null || true
  nohup python3 /home/yao/gello_desk/teleop_error_watchdog.py \
    --desk-host "$ROBOT_IP" \
    --fault-hold "${TELEOP_FAULT_HOLD_S:-10}" \
    --cooldown "${TELEOP_RECOVER_COOLDOWN_S:-90}" \
    --recover-script /home/yao/gello_desk/recover_arm_stack.sh \
    >"$LOG_DIR/teleop_error_watchdog.log" 2>&1 &
  echo $! >"$LOG_DIR/teleop_error_watchdog.pid"
  echo "watchdog pid=$(cat "$LOG_DIR/teleop_error_watchdog.pid") log=$LOG_DIR/teleop_error_watchdog.log"

  echo
  echo "=== All services started ==="
  cmd_status
  cat <<EOF

Episode control:
  bash $0 episode-start --repo fr3_gello_teleop --task "demo"
  bash $0 episode-stop
  bash $0 episode-status

Stop everything:
  bash $0 stop
EOF
}

cmd_status() {
  source_ros
  echo "=== kernel / load ==="
  uname -r
  uptime
  echo "=== FCI ==="
  curl -sk --connect-timeout 3 -u "franka:${DESK_PASSWORD}" "https://${ROBOT_IP}/api/fci" 2>/dev/null || echo "(unreachable)"
  echo
  echo "=== processes ==="
  pgrep -af "gello_publisher|ros2_control_node|mujoco_ros_mirror|realsense2_camera_node|cam_view_dual|record_server|teleop_error_watchdog|cam_replay" \
    | grep -v "pgrep -af" || echo "(none)"
  echo "=== controllers ==="
  timeout 3 ros2 control list_controllers 2>/dev/null || echo "(list_controllers timed out / ros2cli wedged)"
  echo "=== cameras ==="
  timeout 5 ros2 topic list 2>/dev/null | grep -E "/cam[12]/.*/color/image_raw$" || true
  echo "=== recorder ==="
  bash "$DIR/lerobot_episode.sh" status 2>/dev/null || echo "(recorder down)"
}

cmd_episode_start() {
  bash "$DIR/lerobot_episode.sh" start "$@"
}

cmd_episode_stop() {
  bash "$DIR/lerobot_episode.sh" stop
}

cmd_episode_status() {
  bash "$DIR/lerobot_episode.sh" status
}

case "${1:-}" in
  start) cmd_start ;;
  stop) stop_all ;;
  status) cmd_status ;;
  restart) stop_all; sleep 2; cmd_start ;;
  episode-start) shift; cmd_episode_start "$@" ;;
  episode-stop) cmd_episode_stop ;;
  episode-status) cmd_episode_status ;;
  *) usage; exit 1 ;;
esac
