#!/bin/bash
# Start Franka Hand client + FR3 impedance controller for GELLO teleop.
set -eo pipefail

ROBOT_IP="${ROBOT_IP:-10.229.66.91}"
LOG_DIR=/home/yao/gello_logs
mkdir -p "$LOG_DIR"

source /opt/ros/humble/setup.bash
source /home/yao/franka_ros2_ws/install/setup.bash
export LD_LIBRARY_PATH="/home/yao/franka_ros2_ws/install/libfranka/lib:/opt/ros/humble/lib/x86_64-linux-gnu:/opt/ros/humble/lib:${LD_LIBRARY_PATH:-}"
export PYTHONNOUSERSITE=1

echo "[check] Waiting for FCI port 1337 on ${ROBOT_IP} ..."
for i in $(seq 1 30); do
  if timeout 1 bash -c "echo > /dev/tcp/${ROBOT_IP}/1337" 2>/dev/null; then
    echo "FCI port open (Desk: keep Activate FCI popup open)"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "FCI not ready. In Desk: Execution mode -> Activate FCI -> keep popup open"
    exit 1
  fi
  sleep 2
done

pkill -f franka_gripper_client 2>/dev/null || true
pkill -f franka_fr3_arm_controllers 2>/dev/null || true
pkill -f franka_gripper_node 2>/dev/null || true
pkill -f ros2_control_node 2>/dev/null || true
sleep 2

echo "[1/2] Starting FR3 stack (gripper node + impedance controller)..."
nohup ros2 launch franka_fr3_arm_controllers franka_fr3_arm_controllers.launch.py \
  robot_config_file:=example_fr3_config.yaml > "$LOG_DIR/arm_controllers.log" 2>&1 &
ARM_PID=$!
sleep 15

# Boost realtime priority for ros2_control
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

echo "[2/2] Starting Franka Hand gripper client (after arm stack)..."
nohup ros2 launch franka_gripper_manager franka_gripper_client.launch.py \
  config_file:=example_fr3_config_franka_hand.yaml > "$LOG_DIR/gripper.log" 2>&1 &
sleep 4

ros2 control list_controllers 2>/dev/null || true
if strings "$LOG_DIR/arm_controllers.log" | grep -q "communication_constraints_violation"; then
  echo
  echo "WARNING: communication_constraints_violation detected."
  echo "Use wired Ethernet from this PC to the FR3 Control unit (C2 Shop Floor port), not WiFi."
fi
