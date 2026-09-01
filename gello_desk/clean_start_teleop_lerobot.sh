#!/usr/bin/env bash
# Clean residual teleop/recording processes, then start the full stack.
#
# Sequence:
#   1) List related residuals
#   2) Hard-stop teleop + LeRobot record + cams + mirrors + replays + watchdog
#   3) Verify cleared
#   4) start_teleop_lerobot_all.sh start  (preflight → desk_prep → teleop →
#      MuJoCo → dual RealSense → cam_view → record_server → anomaly watchdog)
#   5) Print status
#
# Usage:
#   bash /home/yao/gello_desk/clean_start_teleop_lerobot.sh
#   bash /home/yao/gello_desk/clean_start_teleop_lerobot.sh --status-only
#   bash /home/yao/gello_desk/clean_start_teleop_lerobot.sh --clean-only
#
# Env:
#   ROBOT_IP   default 10.229.66.91
#   DISPLAY    default :1
#   LOG_DIR    default /home/yao/gello_logs
set -eo pipefail

ROBOT_IP="${ROBOT_IP:-10.229.66.91}"
LOG_DIR="${LOG_DIR:-/home/yao/gello_logs}"
DISPLAY="${DISPLAY:-:1}"
DESK_DIR="/home/yao/gello_desk"
REC_DIR="$DESK_DIR/lerobot_record"
ALL_SH="$REC_DIR/start_teleop_lerobot_all.sh"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/clean_start_${STAMP}.log"

export ROBOT_IP LOG_DIR DISPLAY
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG") 2>&1

RELATED_PAT='gello_publisher|ros2_control_node|franka_gripper|franka_fr3_arm|teleop_error_watchdog|mujoco_ros_mirror|mujoco_replay|cam_replay|cam_view_dual|record_server|realsense2_camera|rs_launch|ros2 bag record|recover_arm_stack'

list_related() {
  pgrep -af "$RELATED_PAT" 2>/dev/null | grep -v pgrep || true
}

echo_section() {
  echo
  echo "======== $* ========"
}

clean_all() {
  echo_section "1) Residuals before clean"
  local before
  before="$(list_related)"
  if [[ -z "$before" ]]; then
    echo "(none)"
  else
    echo "$before"
  fi

  echo_section "2) Stop full stack (targeted)"
  if [[ -x "$ALL_SH" ]] || [[ -f "$ALL_SH" ]]; then
    bash "$ALL_SH" stop 2>/dev/null || true
  fi
  if [[ -f "$DESK_DIR/restart_teleop.sh" ]]; then
    bash "$DESK_DIR/restart_teleop.sh" stop 2>/dev/null || true
  fi
  bash /home/yao/gello_launch.sh stop 2>/dev/null || true
  bash "$REC_DIR/lerobot_record_daemon.sh" stop 2>/dev/null || true
  bash "$REC_DIR/start_dual_realsense.sh" stop 2>/dev/null || true

  # Sweep leftovers from interrupted restarts / orphan GUI
  local pats=(
    teleop_error_watchdog.py
    mujoco_ros_mirror.py
    mujoco_replay.py
    cam_replay.py
    cam_view_dual.py
    record_server.py
    recover_arm_stack.sh
    gello_publisher
    franka_gripper_client
    franka_fr3_arm_controllers
    franka_gripper_node
    'franka_gripper/'
    ros2_control_node
    'controller_manager/spawner'
    realsense2_camera_node
    rs_launch.py
    rqt_image_view
    'ros2 bag record'
  )
  local pat
  for pat in "${pats[@]}"; do
    pkill -f "$pat" 2>/dev/null || true
  done
  sleep 1
  for pat in "${pats[@]}"; do
    pkill -9 -f "$pat" 2>/dev/null || true
  done

  rm -f \
    "$LOG_DIR/lerobot_record.pid" \
    "$LOG_DIR/cam_view_dual.pid" \
    "$LOG_DIR/teleop_error_watchdog.pid" \
    "$LOG_DIR/realsense_cam1.pid" \
    "$LOG_DIR/realsense_cam2.pid" \
    "$LOG_DIR/recover_arm_stack.lock" \
    2>/dev/null || true

  sleep 1
  echo_section "3) Residuals after clean"
  local after
  after="$(list_related)"
  if [[ -z "$after" ]]; then
    echo "(none) — clean"
  else
    echo "$after"
    echo "WARN: some related processes remain; continuing anyway."
  fi

  if curl -sS --max-time 1 http://127.0.0.1:8765/record/status >/dev/null 2>&1; then
    echo "WARN: record API :8765 still responding"
  else
    echo "record API :8765 down (ok)"
  fi
}

start_all() {
  echo_section "4) Start full teleop + LeRobot stack"
  if [[ ! -f "$ALL_SH" ]]; then
    echo "ERROR: missing $ALL_SH"
    exit 1
  fi
  bash "$ALL_SH" start
}

print_status() {
  echo_section "5) Status"
  if [[ -f "$ALL_SH" ]]; then
    bash "$ALL_SH" status 2>&1 || true
  else
    list_related || true
  fi
  echo
  echo "Log: $LOG"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [--status-only|--clean-only|--help]

  (default)     clean residuals → start full teleop+record stack → status
  --clean-only  only clean residuals
  --status-only only print status
EOF
}

case "${1:-}" in
  -h|--help|help)
    usage
    exit 0
    ;;
  --status-only)
    print_status
    exit 0
    ;;
  --clean-only)
    clean_all
    echo "Log: $LOG"
    exit 0
    ;;
  ""|--start|start)
    clean_all
    start_all
    print_status
    ;;
  *)
    usage
    exit 1
    ;;
esac
