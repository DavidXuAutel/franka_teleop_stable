#!/usr/bin/env bash
# Safe teleop restart with mandatory link preflight gate.
#
# Sequence:
#   1) stop teleop carefully (targeted pkill only — never kill SSH)
#   2) link preflight (MUST pass)
#   3) desk_prep --recover
#   4) gello_launch
#   5) brief post-start health (success_rate / communication_constraints)
#
# Usage:
#   bash /home/yao/gello_desk/restart_teleop.sh           # full restart
#   bash /home/yao/gello_desk/restart_teleop.sh preflight # link test only
#   bash /home/yao/gello_desk/restart_teleop.sh stop
#   bash /home/yao/gello_desk/restart_teleop.sh status
set -eo pipefail

ROBOT_IP="${ROBOT_IP:-10.229.66.91}"
LOG_DIR="${LOG_DIR:-/home/yao/gello_logs}"
DESK_DIR="/home/yao/gello_desk"
PREFLIGHT="$DESK_DIR/link_preflight.sh"
LAUNCH="${GELLO_LAUNCH:-/home/yao/gello_launch.sh}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/restart_teleop_${STAMP}.log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG") 2>&1

usage() {
  cat <<EOF
Usage: $(basename "$0") [{restart|preflight|stop|status}]

  restart    stop → link preflight → desk_prep → gello_launch → health (default)
  preflight  run link_preflight.sh only
  stop       stop teleop stack carefully
  status     quick FCI / route / process summary

Environment:
  ROBOT_IP      default 10.229.66.91
  LOG_DIR       default /home/yao/gello_logs
  GELLO_LAUNCH  default /home/yao/gello_launch.sh
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
  source "$DESK_DIR/env.sh" 2>/dev/null || true
}

# Targeted stops only — never pkill ssh / bash session broadly.
stop_teleop() {
  echo "[1/5] Stopping teleop carefully..."
  pkill -f teleop_error_watchdog.py 2>/dev/null || true
  if [ -x "$LAUNCH" ] || [ -f "$LAUNCH" ]; then
    bash "$LAUNCH" stop 2>/dev/null || true
  fi
  # Leftovers from prior launches (same patterns as gello_start_franka / gello_launch)
  pkill -f gello_publisher 2>/dev/null || true
  pkill -f franka_gripper_client 2>/dev/null || true
  pkill -f franka_fr3_arm_controllers 2>/dev/null || true
  pkill -f franka_gripper_node 2>/dev/null || true
  pkill -f "ros2_control_node" 2>/dev/null || true
  pkill -f "/opt/ros/humble/lib/controller_manager/spawner" 2>/dev/null || true
  sleep 2
  echo "[stop] Done."
}

run_preflight() {
  echo "[2/5] Link preflight (mandatory)..."
  if [ ! -x "$PREFLIGHT" ] && [ ! -f "$PREFLIGHT" ]; then
    echo "ERROR: missing $PREFLIGHT"
    exit 1
  fi
  bash "$PREFLIGHT"
}

run_desk_prep() {
  echo "[3/5] desk_prep --recover..."
  source_ros
  python3 "$DESK_DIR/desk_prep.py" --host "$ROBOT_IP" --recover \
    | tee "$LOG_DIR/desk_prep_restart.log" | tail -12

  # After desk_prep, FCI port should open
  echo "[3b] Re-check TCP 1337 after desk_prep..."
  local ok=0
  local i
  for i in $(seq 1 30); do
    if timeout 1 bash -c "echo >/dev/tcp/${ROBOT_IP}/1337" 2>/dev/null; then
      echo "FCI port 1337 open."
      ok=1
      break
    fi
    sleep 1
  done
  if [ "$ok" -ne 1 ]; then
    echo "ERROR: FCI port 1337 still closed after desk_prep. 勿启动遥操。"
    exit 1
  fi
}

run_launch() {
  echo "[4/5] Starting GELLO teleop (preflight already passed)..."
  # Skip nested preflight inside gello_launch to avoid double ping cost.
  # Gripper client (default on) pauses impedance around Move; Homing stays off.
  LINK_PREFLIGHT_OK=1 \
    START_GRIPPER_CLIENT="${START_GRIPPER_CLIENT:-1}" \
    SKIP_GRIPPER_HOMING="${SKIP_GRIPPER_HOMING:-1}" \
    ROBOT_IP="$ROBOT_IP" bash "$LAUNCH" \
    | tee "$LOG_DIR/teleop_restart_launch.log" | tail -20
}

start_watchdog() {
  echo "[4b] Starting teleop anomaly watchdog (fault-hold=${TELEOP_FAULT_HOLD_S:-10}s)..."
  source_ros
  pkill -f teleop_error_watchdog.py 2>/dev/null || true
  nohup python3 "$DESK_DIR/teleop_error_watchdog.py" \
    --desk-host "$ROBOT_IP" \
    --fault-hold "${TELEOP_FAULT_HOLD_S:-10}" \
    --cooldown "${TELEOP_RECOVER_COOLDOWN_S:-90}" \
    --recover-script "$DESK_DIR/recover_arm_stack.sh" \
    >"$LOG_DIR/teleop_error_watchdog.log" 2>&1 &
  echo $! >"$LOG_DIR/teleop_error_watchdog.pid"
  echo "watchdog pid=$(cat "$LOG_DIR/teleop_error_watchdog.pid") log=$LOG_DIR/teleop_error_watchdog.log"
}

post_health() {
  echo "[5/5] Post-start health (brief)..."
  source_ros
  local health_fail=0
  sleep 3

  if strings "$LOG_DIR/arm_controllers.log" 2>/dev/null \
      | grep -q "communication_constraints_violation"; then
    echo "[FAIL] communication_constraints_violation in arm log (immediate)."
    health_fail=1
  else
    echo "[PASS] No immediate communication_constraints_violation in arm log."
  fi

  # After teleop owns FCI, do NOT raw-TCP-probe :1337 and do NOT run echo_robot_state —
  # both can interrupt the active libfranka session ("server closed connection").
  local fci_json
  fci_json="$(curl -sk -u "${DESK_USER:-franka}:${DESK_PASSWORD:-franka123}" \
    "https://${ROBOT_IP}/api/fci" 2>/dev/null || true)"
  if echo "$fci_json" | grep -q Active; then
    echo "[PASS] Desk FCI API Active (no :1337 probe while control is live)."
  else
    echo "[FAIL] Desk FCI API not Active after start: ${fci_json}"
    health_fail=1
  fi

  # success_rate from live robot_state topic (safe; does not open a second FCI client)
  local sr_out
  set +e
  sr_out="$(timeout 4 ros2 topic echo /franka_robot_state_broadcaster/robot_state --once 2>/dev/null | python3 -c '
import sys, re
t = sys.stdin.read()
vals = [float(x) for x in re.findall(r"control_command_success_rate:\s*([0-9.eE+-]+)", t)]
if not vals:
    print("NO_DATA")
else:
    print(f"n={len(vals)} last={vals[-1]:.4f} min={min(vals):.4f}")
' 2>/dev/null)"
  set -e
  echo "success_rate sample: ${sr_out:-NO_DATA}"
  if echo "$sr_out" | grep -q "NO_DATA"; then
    echo "[WARN] Could not sample success_rate from robot_state topic."
  elif echo "$sr_out" | grep -qE 'min=0\.0[0-4]|last=0\.0[0-4]'; then
    echo "[FAIL] success_rate stuck very low — link/teleop unhealthy."
    health_fail=1
  else
    echo "[PASS] success_rate not stuck at floor."
  fi

  if timeout 3 ros2 control list_controllers 2>/dev/null \
      | grep -q "joint_impedance_controller.*active"; then
    echo "[PASS] joint_impedance_controller active"
  else
    echo "[WARN] joint_impedance_controller not confirmed active (DDS may be slow)."
  fi

  if [ "$health_fail" -ne 0 ]; then
    echo
    echo "RESULT: START completed but health check FAILED — investigate before use."
    echo "Log: $LOG"
    exit 1
  fi
  echo
  echo "RESULT: OK — link preflight passed and teleop looks healthy."
  echo "Log: $LOG"
}

cmd_status() {
  source_ros
  echo "=== route ==="
  ip route get "$ROBOT_IP" | head -1
  echo "=== FCI API ==="
  curl -sk -u "${DESK_USER:-franka}:${DESK_PASSWORD:-franka123}" \
    "https://${ROBOT_IP}/api/fci" 2>/dev/null || true
  echo
  echo "=== port 1337 ==="
  timeout 1 bash -c "echo >/dev/tcp/${ROBOT_IP}/1337" 2>/dev/null && echo open || echo closed
  echo "=== processes ==="
  pgrep -af "gello_publisher|ros2_control_node|franka_fr3|teleop_error_watchdog" || echo "(none)"
}

case "${1:-restart}" in
  preflight|link)
    bash "$PREFLIGHT"
    ;;
  stop)
    stop_teleop
    ;;
  status)
    cmd_status
    ;;
  restart|start|"")
    stop_teleop
    run_preflight
    run_desk_prep
    run_launch
    start_watchdog
    post_health
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac
