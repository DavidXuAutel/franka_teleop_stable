#!/bin/bash
# Full Franka GELLO teleoperation launcher (GELLO + Franka Hand + FR3).
set -eo pipefail

LOG_DIR=/home/yao/gello_logs
mkdir -p "$LOG_DIR"

source /opt/ros/humble/setup.bash
source /home/yao/franka_ros2_ws/install/setup.bash
export PYTHONNOUSERSITE=1
export LD_LIBRARY_PATH="/home/yao/franka_ros2_ws/install/libfranka/lib:/opt/ros/humble/lib/x86_64-linux-gnu:/opt/ros/humble/lib:${LD_LIBRARY_PATH:-}"

stop_all() {
  pkill -f gello_publisher 2>/dev/null || true
  pkill -f franka_gripper_client 2>/dev/null || true
  pkill -f robotiq_gripper_client 2>/dev/null || true
  pkill -f franka_fr3_arm_controllers 2>/dev/null || true
  pkill -f franka_gripper_node 2>/dev/null || true
  pkill -f "ros2_control_node" 2>/dev/null || true
  python3 /home/yao/gello_desk/desk_prep.py --release 2>/dev/null || true
}

if [ "${1:-}" = "stop" ]; then
  stop_all
  echo "Stopped all GELLO teleop nodes."
  exit 0
fi

if ! ls /dev/serial/by-id/usb-FTDI_* >/dev/null 2>&1; then
  echo "GELLO serial device not found. Check USB connection."
  ls -la /dev/serial/by-id/ 2>/dev/null || true
  exit 1
fi

echo "[1/3] Starting GELLO state publisher..."
nohup ros2 launch franka_gello_state_publisher main.launch.py config_file:=franka_gello_single.yaml \
  > "$LOG_DIR/gello_publisher.log" 2>&1 &
sleep 4
if ! grep -q "Publishing GELLO joint states" "$LOG_DIR/gello_publisher.log" 2>/dev/null; then
  echo "GELLO publisher failed. See $LOG_DIR/gello_publisher.log"
  tail -20 "$LOG_DIR/gello_publisher.log"
  exit 1
fi

echo "[2/3] Checking FCI and starting Franka stack..."
bash /home/yao/gello_start_franka.sh

echo
echo "Teleop ready when joint_impedance_controller is loaded."
echo "Monitor: ros2 topic echo /gello/joint_states"
echo "Stop:    bash /home/yao/gello_launch.sh stop"
