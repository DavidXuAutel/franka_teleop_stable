#!/usr/bin/env bash
# Launch two RealSense cameras as ROS2 nodes (cam1=D435I, cam2=D435).
set -euo pipefail

LOG_DIR="${LOG_DIR:-/home/yao/gello_logs}"
mkdir -p "$LOG_DIR"
PID_FILE="$LOG_DIR/dual_realsense.pids"

# D435I / D435 serials (override via env)
CAM1_SERIAL="${CAM1_SERIAL:-247122072824}"
CAM2_SERIAL="${CAM2_SERIAL:-141722071359}"

usage() {
  echo "Usage: $0 {start|stop|status}"
}

stop_cams() {
  if [[ -f "$PID_FILE" ]]; then
    while read -r pid; do
      [[ -n "$pid" ]] || continue
      kill "$pid" 2>/dev/null || true
    done <"$PID_FILE"
    rm -f "$PID_FILE"
  fi
  # Best-effort cleanup of leftover nodes (do not match this script)
  pkill -f "/opt/ros/humble/lib/realsense2_camera/realsense2_camera_node" 2>/dev/null || true
  pkill -f "ros2 launch realsense2_camera rs_launch" 2>/dev/null || true
  sleep 1
  echo "dual realsense stopped"
}

start_cams() {
  stop_cams
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  # shellcheck disable=SC1091
  source /home/yao/franka_ros2_ws/install/setup.bash
  set -u

  nohup ros2 launch realsense2_camera rs_launch.py \
    camera_namespace:=cam1 camera_name:=cam1 \
    serial_no:="_${CAM1_SERIAL}" \
    enable_color:=true enable_depth:=false enable_infra1:=false enable_infra2:=false \
    >>"$LOG_DIR/realsense_cam1.log" 2>&1 &
  echo $! >>"$PID_FILE"

  sleep 2

  nohup ros2 launch realsense2_camera rs_launch.py \
    camera_namespace:=cam2 camera_name:=cam2 \
    serial_no:="_${CAM2_SERIAL}" \
    enable_color:=true enable_depth:=false enable_infra1:=false enable_infra2:=false \
    >>"$LOG_DIR/realsense_cam2.log" 2>&1 &
  echo $! >>"$PID_FILE"

  sleep 5
  echo "started cam1 serial=$CAM1_SERIAL  cam2 serial=$CAM2_SERIAL"
  # Boot path: process check only. Do NOT run `ros2 topic hz` here — it can
  # hang / corrupt rclpy context even under `timeout`, and blocks full-stack start.
  echo "=== processes ==="
  pgrep -af realsense2_camera_node || echo "(none)"
}

status_cams() {
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  set -u
  echo "=== processes ==="
  pgrep -af realsense2_camera_node || echo "(none)"
  echo "=== topics ==="
  timeout 5 ros2 topic list 2>/dev/null | grep -E "/cam[12]/.*/color/image_raw" || true
  echo "=== hz (manual status only; omit from boot) ==="
  timeout 3 ros2 topic hz /cam1/cam1/color/image_raw 2>&1 | tail -3 || true
  timeout 3 ros2 topic hz /cam2/cam2/color/image_raw 2>&1 | tail -3 || true
}

case "${1:-}" in
  start) start_cams ;;
  stop) stop_cams ;;
  status) status_cams ;;
  *) usage; exit 1 ;;
esac
