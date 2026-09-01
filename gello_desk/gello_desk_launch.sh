#!/bin/bash
# GELLO teleoperation via Desk API prep + ROS2 impedance control (WiFi/X5, no cable).
set -eo pipefail

ROBOT_IP="${FRANKA_HOST:-10.229.66.91}"
DESK_USER="${DESK_USER:-franka}"
DESK_PASSWORD="${DESK_PASSWORD:-franka123}"
LOG_DIR=/home/yao/gello_logs
mkdir -p "$LOG_DIR"

source /opt/ros/humble/setup.bash
source /home/yao/franka_ros2_ws/install/setup.bash
export PYTHONNOUSERSITE=1
export LD_LIBRARY_PATH="/home/yao/franka_ros2_ws/install/libfranka/lib:/opt/ros/humble/lib/x86_64-linux-gnu:/opt/ros/humble/lib:${LD_LIBRARY_PATH:-}"
source /home/yao/gello_desk/env.sh 2>/dev/null || true

release_control() {
  if python3 /home/yao/gello_desk/desk_prep.py --host "${DESK_HOST_WIFI:-$ROBOT_IP}" --release; then
    echo "Desk control released."
  fi
}

stop_all() {
  pkill -f mujoco_ros_mirror.py 2>/dev/null || true
  pkill -f teleop_error_watchdog.py 2>/dev/null || true
  pkill -f gello_publisher 2>/dev/null || true
  pkill -f franka_gripper_client 2>/dev/null || true
  pkill -f franka_fr3_arm_controllers 2>/dev/null || true
  pkill -f franka_gripper_node 2>/dev/null || true
  pkill -f ros2_control_node 2>/dev/null || true
  release_control
}

if [ "${1:-}" = "stop" ]; then
  stop_all
  echo "Stopped GELLO Desk teleop stack."
  exit 0
fi

if [ "${1:-}" = "mujoco" ]; then
  pkill -f mujoco_ros_mirror.py 2>/dev/null || true
  sleep 1
  export DISPLAY="${DISPLAY:-:1}"
  MUJOCO_CMD="unset PYTHONNOUSERSITE; export DISPLAY=${DISPLAY}; source /opt/ros/humble/setup.bash && source /home/yao/franka_ros2_ws/install/setup.bash"
  bash -lc "${MUJOCO_CMD} && python3 /home/yao/gello_desk/mujoco_ros_mirror.py --source franka" \
    > "$LOG_DIR/mujoco_franka.log" 2>&1 &
  bash -lc "${MUJOCO_CMD} && python3 /home/yao/gello_desk/mujoco_ros_mirror.py --source gello" \
    > "$LOG_DIR/mujoco_gello.log" 2>&1 &
  sleep 3
  if grep -q "could not initialize GLFW" "$LOG_DIR/mujoco_franka.log" 2>/dev/null; then
    echo "MuJoCo viewer failed. See $LOG_DIR/mujoco_franka.log"
    tail -5 "$LOG_DIR/mujoco_franka.log"
    exit 1
  fi
  echo "MuJoCo sync started: Franka window + GELLO window (DISPLAY=${DISPLAY})"
  echo "Logs: $LOG_DIR/mujoco_franka.log , $LOG_DIR/mujoco_gello.log"
  exit 0
fi

on_launch_exit() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ]; then
    release_control
  fi
}
trap on_launch_exit EXIT

stop_all
sleep 2

echo "[0/4] Link preflight (mandatory before teleop)..."
if [ "${LINK_PREFLIGHT_OK:-0}" != "1" ]; then
  if ! bash /home/yao/gello_desk/link_preflight.sh; then
    echo "ABORT: link preflight failed — not starting teleop."
    echo "中止：链路预检失败，未启动遥操。"
    exit 1
  fi
else
  echo "Link preflight skipped (LINK_PREFLIGHT_OK=1)."
fi

echo "[1/4] Desk API prepare (token / unlock / FCI / recovery)..."
python3 /home/yao/gello_desk/desk_prep.py \
  --host "${DESK_HOST_WIFI:-$ROBOT_IP}" \
  --user "$DESK_USER" \
  --password "$DESK_PASSWORD" \
  --recover \
  | tee "$LOG_DIR/desk_prep.log"

echo "[2/4] Starting GELLO publisher..."
# GELLO needs pyserial/dynamixel; do not use PYTHONNOUSERSITE here.
(
  unset PYTHONNOUSERSITE
  source /opt/ros/humble/setup.bash
  source /home/yao/franka_ros2_ws/install/setup.bash
  nohup ros2 launch franka_gello_state_publisher main.launch.py \
    config_file:=franka_gello_single.yaml > "$LOG_DIR/gello_publisher.log" 2>&1 &
)
sleep 4
if ! grep -q "Publishing GELLO joint states" "$LOG_DIR/gello_publisher.log" 2>/dev/null; then
  echo "GELLO publisher failed. See $LOG_DIR/gello_publisher.log"
  tail -20 "$LOG_DIR/gello_publisher.log"
  exit 1
fi

echo "[3/4] Starting FR3 impedance controller..."
nohup ros2 launch franka_fr3_arm_controllers franka_fr3_arm_controllers.launch.py \
  robot_config_file:=example_fr3_config.yaml > "$LOG_DIR/arm_controllers.log" 2>&1 &
sleep 15

ROS2_PID=$(pgrep -f "ros2_control_node.*franka_fr3_arm_controllers" | head -1 || true)
if [ -n "$ROS2_PID" ]; then
  echo "cupcake777" | sudo -S renice -n -20 -p "$ROS2_PID" 2>/dev/null || true
fi

for i in $(seq 1 20); do
  if ros2 control list_controllers 2>/dev/null | grep -q "joint_impedance_controller.*active"; then
    echo "joint_impedance_controller active"
    break
  fi
  sleep 2
done

ros2 control list_controllers 2>/dev/null || true

echo "[4/4] Starting MuJoCo sync (Franka + GELLO)..."
bash "$0" mujoco

echo "[+] Starting teleop anomaly watchdog (fault-hold=${TELEOP_FAULT_HOLD_S:-10}s)..."
pkill -f teleop_error_watchdog.py 2>/dev/null || true
nohup python3 /home/yao/gello_desk/teleop_error_watchdog.py \
  --desk-host "${DESK_HOST_WIFI:-$ROBOT_IP}" \
  --fault-hold "${TELEOP_FAULT_HOLD_S:-10}" \
  --cooldown "${TELEOP_RECOVER_COOLDOWN_S:-90}" \
  --recover-script /home/yao/gello_desk/recover_arm_stack.sh \
  >"$LOG_DIR/teleop_error_watchdog.log" 2>&1 &
echo "watchdog pid=$!"

echo "Stop and release Desk control: bash $0 stop"
echo "Logs in $LOG_DIR/"

if strings "$LOG_DIR/arm_controllers.log" | grep -q "communication_constraints_violation"; then
  echo "WARNING: WiFi latency caused communication_constraints_violation."
fi
