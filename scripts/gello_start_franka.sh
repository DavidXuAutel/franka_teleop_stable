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

# ros2cli (list_controllers / topic list / daemon) can hang forever when DDS
# discovery or the ros2 daemon is wedged. Always wrap with timeout.
ros2_list_controllers() {
  timeout 3 ros2 control list_controllers 2>/dev/null || true
}

impedance_ready_in_log() {
  strings "$LOG_DIR/arm_controllers.log" 2>/dev/null \
    | grep "Configured and activated" \
    | grep -Fq "joint_impedance_controller"
}

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
# orphan spawners from prior launches also poison DDS / controller_manager clients
pkill -f "/opt/ros/humble/lib/controller_manager/spawner" 2>/dev/null || true
sleep 2

echo "[1/2] Starting FR3 stack (impedance controller; load_gripper from config)..."
: > "$LOG_DIR/arm_controllers.log"
nohup ros2 launch franka_fr3_arm_controllers franka_fr3_arm_controllers.launch.py \
  robot_config_file:=example_fr3_config.yaml > "$LOG_DIR/arm_controllers.log" 2>&1 &
ARM_PID=$!

# Prefer log readiness over fixed 15s + blocking list_controllers.
READY=0
for i in $(seq 1 30); do
  if impedance_ready_in_log; then
    echo "joint_impedance_controller active (from arm log, ${i}s)"
    READY=1
    break
  fi
  sleep 1
done

# Boost realtime priority for ros2_control
ROS2_PID=$(pgrep -f "ros2_control_node" | head -1 || true)
if [ -n "$ROS2_PID" ]; then
  echo "cupcake777" | sudo -S renice -n -20 -p "$ROS2_PID" 2>/dev/null || true
fi

if [ "$READY" -eq 0 ]; then
  for i in $(seq 1 5); do
    if ros2_list_controllers | grep -q "joint_impedance_controller.*active"; then
      echo "joint_impedance_controller active (list_controllers)"
      READY=1
      break
    fi
    if impedance_ready_in_log; then
      echo "joint_impedance_controller active (from arm log, late)"
      READY=1
      break
    fi
    sleep 1
  done
fi

if [ "$READY" -eq 0 ]; then
  echo "WARN: could not confirm joint_impedance_controller within timeout; continuing"
  tail -20 "$LOG_DIR/arm_controllers.log" || true
fi

# Gripper uses :1338 (separate from arm FCI :1337). Homing/Move while
# joint_impedance is ACTIVE still drops the arm TCP — the patched client
# pauses impedance around each Move (pause_arm_for_move). Always skip Homing.
# Disable client: START_GRIPPER_CLIENT=0
START_GRIPPER_CLIENT="${START_GRIPPER_CLIENT:-1}"
SKIP_GRIPPER_HOMING="${SKIP_GRIPPER_HOMING:-1}"
if [ "$START_GRIPPER_CLIENT" = "1" ] || [ "$START_GRIPPER_CLIENT" = "true" ]; then
  if [ "$SKIP_GRIPPER_HOMING" = "0" ] || [ "$SKIP_GRIPPER_HOMING" = "false" ]; then
    GRIPPER_SKIP_HOMING=false
    echo "[2/2] Starting Franka Hand gripper client (WITH homing — may drop arm FCI)..."
  else
    GRIPPER_SKIP_HOMING=true
    echo "[2/2] Starting Franka Hand gripper client (skip_homing=true, pause_arm_for_move)..."
  fi
  nohup ros2 launch franka_gripper_manager franka_gripper_client.launch.py \
    config_file:=example_fr3_config_franka_hand.yaml \
    skip_homing:="$GRIPPER_SKIP_HOMING" > "$LOG_DIR/gripper.log" 2>&1 &
  # Client ignores GELLO cmds for startup_ignore_sec; give it time to come up.
  sleep 4
  # Ensure impedance is (still/again) active after client init.
  if ! ros2_list_controllers | grep -q "joint_impedance_controller.*active"; then
    echo "[2b] Re-activating joint_impedance_controller after gripper client start..."
    timeout 8 ros2 control switch_controllers --activate joint_impedance_controller 2>/dev/null || true
    sleep 1
  fi
else
  echo "[2/2] Skipping franka_gripper_client (START_GRIPPER_CLIENT=0)."
  echo "skip_client" > "$LOG_DIR/gripper.log"
fi

ros2_list_controllers || true
if strings "$LOG_DIR/arm_controllers.log" | grep -q "communication_constraints_violation"; then
  echo
  echo "WARNING: communication_constraints_violation detected."
  echo "Use wired Ethernet from this PC to the FR3 Control unit (C2 Shop Floor port), not WiFi."
fi
