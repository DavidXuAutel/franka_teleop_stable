#!/bin/bash
# Unified launcher: Desk/FCI + GELLO teleop + MuJoCo (GPU) + RealSense capture.
# Usage:
#   bash start_teleop_all.sh start    # start everything
#   bash start_teleop_all.sh stop     # stop everything
#   bash start_teleop_all.sh status   # show status
#   bash start_teleop_all.sh restart  # stop then start
set -eo pipefail

ROBOT_IP="${ROBOT_IP:-10.229.66.91}"
LOG_DIR="${LOG_DIR:-/home/yao/gello_logs}"
CAMERA_BAG_DIR="$LOG_DIR/camera_bags"
DISPLAY="${DISPLAY:-:1}"

source /opt/ros/humble/setup.bash
source /home/yao/franka_ros2_ws/install/setup.bash
export LD_LIBRARY_PATH="/home/yao/franka_ros2_ws/install/libfranka/lib:/home/yao/genie_sim/source/teleop/app/vendors/lib:/opt/ros/humble/lib/x86_64-linux-gnu:/opt/ros/humble/lib:${LD_LIBRARY_PATH:-}"
source /home/yao/gello_desk/env.sh 2>/dev/null || true

mkdir -p "$LOG_DIR" "$CAMERA_BAG_DIR"

usage() {
  cat <<EOF
Usage: $(basename "$0") {start|stop|status|restart}

  start    Prepare FCI, GELLO teleop, MuJoCo GPU mirrors, RealSense + rosbag
  stop     Stop teleop, MuJoCo, camera driver, and bag recording
  status   Show process / topic / load summary
  restart  stop && start

Environment:
  ROBOT_IP   Robot IP (default: 10.229.66.91)
  DISPLAY    X display for MuJoCo (default: :1)
  LOG_DIR    Log and bag output (default: /home/yao/gello_logs)
EOF
}

stop_all() {
  echo "[stop] GELLO teleop stack..."
  bash /home/yao/gello_launch.sh stop 2>/dev/null || true
  echo "[stop] error watchdog..."
  pkill -f teleop_error_watchdog.py 2>/dev/null || true
  echo "[stop] MuJoCo mirrors / replay..."
  pkill -f mujoco_ros_mirror.py 2>/dev/null || true
  pkill -f mujoco_replay.py 2>/dev/null || true
  pkill -f cam_replay.py 2>/dev/null || true
  echo "[stop] RealSense driver..."
  pkill -f realsense2_camera_node 2>/dev/null || true
  pkill -f "ros2 launch realsense2_camera" 2>/dev/null || true
  echo "[stop] Camera bag recording..."
  pkill -f "ros2 bag record" 2>/dev/null || true
  pkill -f rqt_image_view 2>/dev/null || true
  sleep 2
  echo "[stop] Done."
}

cmd_status() {
  echo "=== kernel / gpu ==="
  uname -r
  nvidia-smi 2>&1 | head -8 || echo "(nvidia-smi unavailable on this kernel)"
  echo "=== FCI ==="
  curl -sk -u "${DESK_USER:-franka}:${DESK_PASSWORD:-franka123}" "https://${ROBOT_IP}/api/fci" 2>/dev/null || true
  echo
  echo "=== load ==="
  uptime
  echo "=== processes ==="
  pgrep -af "gello_publisher|ros2_control_node|mujoco_ros_mirror|realsense2_camera|ros2 bag record|teleop_error_watchdog" || echo "(none)"
  echo "=== controllers ==="
  ros2 control list_controllers 2>/dev/null || true
  echo "=== camera topics ==="
  ros2 topic list 2>/dev/null | grep -E "^/camera/" | head -10 || true
}

cmd_start() {
  if ! ls /dev/serial/by-id/usb-FTDI_* >/dev/null 2>&1; then
    echo "ERROR: GELLO USB not found."
    ls -la /dev/serial/by-id/ 2>/dev/null || true
    exit 1
  fi

  echo "[1/5] Desk API: FCI + unlock..."
  python3 /home/yao/gello_desk/desk_prep.py --host "$ROBOT_IP" --recover \
    | tee "$LOG_DIR/desk_prep_all.log" | tail -6

  echo "[2/5] GELLO teleop (publisher -> arm -> gripper)..."
  ROBOT_IP="$ROBOT_IP" bash /home/yao/gello_launch.sh 2>&1 | tee "$LOG_DIR/teleop_all.log" | tail -12

  echo "[3/5] MuJoCo GPU mirrors (15 Hz)..."
  export DISPLAY
  if bash /home/yao/gello_desk/start_mujoco_gpu.sh 2>&1 | tee "$LOG_DIR/mujoco_all.log"; then
    :
  else
    echo "WARN: MuJoCo may need desktop login on DISPLAY=$DISPLAY"
    tail -5 "$LOG_DIR/mujoco_franka.log" 2>/dev/null || true
  fi

  echo "[4/5] RealSense D435 camera driver..."
  pkill -f realsense2_camera_node 2>/dev/null || true
  sleep 1
  nohup ros2 launch realsense2_camera rs_launch.py \
    camera_namespace:=camera \
    camera_name:=camera \
    enable_color:=true \
    enable_depth:=false \
    enable_infra1:=false \
    enable_infra2:=false \
    > "$LOG_DIR/realsense.log" 2>&1 &
  echo "Waiting for camera topics..."
  for i in $(seq 1 20); do
    if ros2 topic list 2>/dev/null | grep -q "/camera/camera/color/image_raw"; then
      echo "Camera topic ready."
      break
    fi
    sleep 1
  done

  echo "[5/6] Camera rosbag capture..."
  STAMP=$(date +%Y%m%d_%H%M%S)
  BAG_PATH="$CAMERA_BAG_DIR/bag_${STAMP}"
  nohup ros2 bag record -o "$BAG_PATH" \
    /camera/camera/color/image_raw \
    /camera/camera/color/camera_info \
    > "$LOG_DIR/camera_record.log" 2>&1 &
  echo "Recording to: $BAG_PATH"

  echo "[6/6] Teleop error watchdog..."
  pkill -f teleop_error_watchdog.py 2>/dev/null || true
  nohup python3 /home/yao/gello_desk/teleop_error_watchdog.py \
    --desk-host "$ROBOT_IP" \
    >"$LOG_DIR/teleop_error_watchdog.log" 2>&1 &
  echo "watchdog pid=$! log=$LOG_DIR/teleop_error_watchdog.log"

  sleep 2
  echo
  echo "=== All services started ==="
  cmd_status
  echo
  echo "Monitor:"
  echo "  ros2 topic hz /gello/joint_states"
  echo "  ros2 topic hz /camera/camera/color/image_raw"
  echo "  tail -f $LOG_DIR/camera_record.log"
  echo "Stop:"
  echo "  bash $(readlink -f "$0" 2>/dev/null || echo "$0") stop"
}

case "${1:-}" in
  start) cmd_start ;;
  stop) stop_all ;;
  status) cmd_status ;;
  restart) stop_all; cmd_start ;;
  *) usage; exit 1 ;;
esac
