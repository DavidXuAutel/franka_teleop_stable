#!/bin/bash
# Unified Franka FR3 control from remote server 10.229.20.125
set -eo pipefail

ROBOT_IP="${ROBOT_IP:-10.229.66.91}"
LOG_DIR=/home/yao/gello_logs
mkdir -p "$LOG_DIR"

source /opt/ros/humble/setup.bash
source /home/yao/franka_ros2_ws/install/setup.bash
export LD_LIBRARY_PATH="/home/yao/franka_ros2_ws/install/libfranka/lib:/home/yao/genie_sim/source/teleop/app/vendors/lib:/opt/ros/humble/lib/x86_64-linux-gnu:/opt/ros/humble/lib:${LD_LIBRARY_PATH:-}"
source /home/yao/gello_desk/env.sh 2>/dev/null || true

usage() {
  cat <<EOF
Usage: franka_control.sh <command>

Commands:
  status     Check robot connectivity, FCI, and ROS2 controllers
  preflight  Link/connectivity preflight (route + RTT + :1337)
  prepare    Activate FCI via Desk API (desk_prep.py)
  start      Start FR3 arm stack only (no GELLO)
  teleop     Start full GELLO teleop (publisher + arm stack)
  restart    Safe restart: stop → preflight → desk_prep → teleop
  stop       Stop all Franka/GELLO nodes and release Desk token
  test       Run libfranka communication_test + echo_robot_state

Environment:
  ROBOT_IP   Robot IP (default: 10.229.66.91)
EOF
}

cmd_status() {
  echo "=== Robot ping ($ROBOT_IP) ==="
  ping -c1 -W2 "$ROBOT_IP" || true
  echo "=== FCI ==="
  curl -sk -u "${DESK_USER:-franka}:${DESK_PASSWORD:-franka123}" "https://$ROBOT_IP/api/fci" || true
  echo
  nc -zv -w2 "$ROBOT_IP" 1337 2>&1 || true
  echo "=== ROS2 controllers ==="
  ros2 control list_controllers 2>&1 || true
  echo "=== Processes ==="
  pgrep -af "gello|franka_fr3|ros2_control_node" 2>/dev/null | head -10 || echo "(none)"
}

cmd_preflight() {
  bash /home/yao/gello_desk/link_preflight.sh
}

cmd_prepare() {
  python3 /home/yao/gello_desk/desk_prep.py --host "$ROBOT_IP" --recover
}

cmd_start() {
  ROBOT_IP="$ROBOT_IP" bash /home/yao/gello_start_franka.sh
}

cmd_teleop() {
  ROBOT_IP="$ROBOT_IP" bash /home/yao/gello_launch.sh
}

cmd_restart() {
  bash /home/yao/gello_desk/restart_teleop.sh restart
}

cmd_stop() {
  bash /home/yao/gello_launch.sh stop 2>/dev/null || bash /home/yao/gello_desk/gello_desk_launch.sh stop 2>/dev/null || true
  pkill -f franka_fr3_arm_controllers 2>/dev/null || true
  pkill -f ros2_control_node 2>/dev/null || true
  python3 /home/yao/gello_desk/desk_prep.py --host "$ROBOT_IP" --release 2>/dev/null || true
  echo "Stopped."
}

cmd_test() {
  echo "=== communication_test ==="
  printf "\n" | timeout 10 /home/yao/franka_ros2_ws/install/libfranka/bin/communication_test "$ROBOT_IP" 2>&1 | head -5 || true
  echo "=== echo_robot_state (q) ==="
  timeout 5 /home/yao/franka_ros2_ws/install/libfranka/bin/echo_robot_state "$ROBOT_IP" 2>&1 | python3 -c "import sys,json; d=json.loads(sys.stdin.read().splitlines()[-1]); print(\"q=\", d.get(\"q\"))" 2>/dev/null || true
}

case "${1:-}" in
  status) cmd_status ;;
  preflight|link) cmd_preflight ;;
  prepare) cmd_prepare ;;
  start) cmd_start ;;
  teleop) cmd_teleop ;;
  restart) cmd_restart ;;
  stop) cmd_stop ;;
  test) cmd_test ;;
  *) usage; exit 1 ;;
esac
