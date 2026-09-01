#!/usr/bin/env bash
# Recover Franka teleop after sustained anomaly (used by teleop_error_watchdog).
#
# Flow (matches manual recovery):
#   1) restart_teleop.sh  (stop → link preflight → desk_prep → launch → health)
#   2) ensure MuJoCo / RealSense / record_server / cam_view_dual are up
#
# IMPORTANT: after teleop owns FCI, do NOT TCP-probe :1337.
set -eo pipefail

ROBOT_IP="${ROBOT_IP:-10.229.66.91}"
LOG_DIR="${LOG_DIR:-/home/yao/gello_logs}"
DESK_DIR="/home/yao/gello_desk"
REC_DIR="$DESK_DIR/lerobot_record"
DISPLAY="${DISPLAY:-:1}"
export ROBOT_IP LOG_DIR DISPLAY

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/recover_arm_stack_${STAMP}.log"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG") 2>&1

echo "=== recover_arm_stack start $(date) ==="

bash "$DESK_DIR/restart_teleop.sh" restart

source_ros() {
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  # shellcheck disable=SC1091
  source /home/yao/franka_ros2_ws/install/setup.bash
  set -u
  export LD_LIBRARY_PATH="/home/yao/franka_ros2_ws/install/libfranka/lib:/home/yao/genie_sim/source/teleop/app/vendors/lib:/opt/ros/humble/lib/x86_64-linux-gnu:/opt/ros/humble/lib:${LD_LIBRARY_PATH:-}"
  # shellcheck disable=SC1091
  source "$DESK_DIR/env.sh" 2>/dev/null || true
}

source_ros

ensure_proc() {
  local pat="$1"
  pgrep -af "$pat" | grep -v pgrep >/dev/null 2>&1
}

if ! ensure_proc 'mujoco_ros_mirror.py'; then
  echo "[+] MuJoCo mirrors..."
  bash "$DESK_DIR/start_mujoco_gpu.sh" 2>&1 | tail -6 || true
fi

if ! ensure_proc 'realsense2_camera_node'; then
  echo "[+] Dual RealSense..."
  bash "$REC_DIR/start_dual_realsense.sh" start 2>&1 | tail -10 || true
fi

echo "[+] Record daemon..."
bash "$REC_DIR/lerobot_record_daemon.sh" restart 2>&1 | tail -6 || true

if ! ensure_proc 'cam_view_dual.py'; then
  echo "[+] cam_view_dual..."
  nohup python3 "$REC_DIR/cam_view_dual.py" \
    --cam1 /cam1/cam1/color/image_raw \
    --cam2 /cam2/cam2/color/image_raw \
    --api http://127.0.0.1:8765 \
    --repo fr3_gello_teleop \
    --task "franka gello teleop" \
    >>"$LOG_DIR/cam_view_dual.log" 2>&1 &
  echo "viewer pid=$!"
fi

sleep 2
echo "=== recover_arm_stack done $(date) log=$LOG ==="
pgrep -af 'gello_publisher|ros2_control_node|franka_gripper|teleop_error_watchdog|record_server|cam_view_dual|realsense2_camera_node|mujoco_ros_mirror' \
  | grep -v pgrep || true
