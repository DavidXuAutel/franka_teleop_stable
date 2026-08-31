#!/usr/bin/env python3
"""Watch Franka robot_mode / current_errors during teleop and auto-recover.

On REFLEX / USER_STOPPED / any current_errors flag:
  1) Call /action_server/error_recovery (libfranka automaticErrorRecovery)
  2) If still USER_STOPPED, run Desk safety recovery via desk_prep.py
  3) Re-activate joint_impedance_controller if it dropped inactive

Does not change robot Desk network settings.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import threading
import time
from typing import Any

import rclpy
from franka_msgs.action import ErrorRecovery
from franka_msgs.msg import FrankaRobotState
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy


MODE_NAMES = {
    FrankaRobotState.ROBOT_MODE_OTHER: "OTHER",
    FrankaRobotState.ROBOT_MODE_IDLE: "IDLE",
    FrankaRobotState.ROBOT_MODE_MOVE: "MOVE",
    FrankaRobotState.ROBOT_MODE_GUIDING: "GUIDING",
    FrankaRobotState.ROBOT_MODE_REFLEX: "REFLEX",
    FrankaRobotState.ROBOT_MODE_USER_STOPPED: "USER_STOPPED",
    FrankaRobotState.ROBOT_MODE_AUTOMATIC_ERROR_RECOVERY: "AUTO_RECOVERY",
}

FAULT_MODES = {
    FrankaRobotState.ROBOT_MODE_REFLEX,
    FrankaRobotState.ROBOT_MODE_USER_STOPPED,
}


def _errors_active(errors: Any) -> list[str]:
    names: list[str] = []
    for name in dir(errors):
        if name.startswith("_"):
            continue
        try:
            val = getattr(errors, name)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(val, bool) and val:
            names.append(name)
    return names


class TeleopErrorWatchdog(Node):
    def __init__(
        self,
        *,
        cooldown_s: float,
        desk_host: str,
        desk_user: str,
        desk_password: str,
        enable_desk: bool,
        controller: str,
    ) -> None:
        super().__init__("teleop_error_watchdog")
        self.cooldown_s = cooldown_s
        self.desk_host = desk_host
        self.desk_user = desk_user
        self.desk_password = desk_password
        self.enable_desk = enable_desk
        self.controller = controller
        self._last_recover_t = 0.0
        self._recovering = False
        self._lock = threading.Lock()
        self._last_mode = -1
        self._last_error_names: list[str] = []
        self._cb_group = ReentrantCallbackGroup()

        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(
            FrankaRobotState,
            "/franka_robot_state_broadcaster/robot_state",
            self._on_state,
            qos,
            callback_group=self._cb_group,
        )
        self._er_client = ActionClient(
            self,
            ErrorRecovery,
            "/action_server/error_recovery",
            callback_group=self._cb_group,
        )
        self.get_logger().info(
            f"watching robot_state | cooldown={cooldown_s}s desk={enable_desk} "
            f"controller={controller}"
        )

    def _on_state(self, msg: FrankaRobotState) -> None:
        mode = int(msg.robot_mode)
        err_names = _errors_active(msg.current_errors)
        if mode != self._last_mode:
            self.get_logger().info(f"robot_mode -> {MODE_NAMES.get(mode, mode)}")
            self._last_mode = mode
        if err_names != self._last_error_names:
            if err_names:
                self.get_logger().warn(f"current_errors: {', '.join(err_names)}")
            self._last_error_names = err_names

        need = mode in FAULT_MODES or bool(err_names)
        if not need:
            return

        now = time.monotonic()
        with self._lock:
            if self._recovering or now - self._last_recover_t < self.cooldown_s:
                return
            self._recovering = True
            self._last_recover_t = now

        reason = MODE_NAMES.get(mode, str(mode))
        if err_names:
            reason = f"{reason} errors={err_names[:5]}"
        self.get_logger().warn(f"auto-recover triggered: {reason}")
        threading.Thread(target=self._recover_worker, args=(mode,), daemon=True).start()

    def _recover_worker(self, mode_at_trigger: int) -> None:
        try:
            ok = self._run_fci_error_recovery()
            self.get_logger().info(f"FCI error_recovery ok={ok}")
            time.sleep(0.8)
            if (
                self.enable_desk
                and mode_at_trigger == FrankaRobotState.ROBOT_MODE_USER_STOPPED
                and self.desk_password
            ):
                self._run_desk_recover()
                time.sleep(1.0)
            elif (
                self.enable_desk
                and self._last_mode == FrankaRobotState.ROBOT_MODE_USER_STOPPED
                and self.desk_password
            ):
                self._run_desk_recover()
                time.sleep(1.0)
            self._ensure_controller_active()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"recover failed: {exc}")
        finally:
            with self._lock:
                self._recovering = False

    def _run_fci_error_recovery(self) -> bool:
        if not self._er_client.wait_for_server(timeout_sec=3.0):
            self.get_logger().error("error_recovery action server unavailable")
            return False
        goal = ErrorRecovery.Goal()
        send_future = self._er_client.send_goal_async(goal)
        deadline = time.monotonic() + 5.0
        while not send_future.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not send_future.done():
            self.get_logger().error("error_recovery send_goal timeout")
            return False
        goal_handle = send_future.result()
        if goal_handle is None or not goal_handle.accepted:
            self.get_logger().error("error_recovery goal rejected")
            return False
        result_future = goal_handle.get_result_async()
        deadline = time.monotonic() + 15.0
        while not result_future.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not result_future.done():
            self.get_logger().error("error_recovery result timeout")
            return False
        return result_future.result() is not None

    def _run_desk_recover(self) -> None:
        desk_prep = os.environ.get("DESK_PREP", "/home/yao/gello_desk/desk_prep.py")
        cmd = [
            "python3",
            desk_prep,
            "--host",
            self.desk_host,
            "--user",
            self.desk_user,
            "--password",
            self.desk_password,
            "--recover",
        ]
        self.get_logger().info("running Desk safety recovery...")
        try:
            out = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            tail = ((out.stdout or "") + (out.stderr or ""))[-400:]
            if out.returncode != 0:
                self.get_logger().warn(f"desk_prep rc={out.returncode}: {tail}")
            else:
                self.get_logger().info(f"desk_prep recover done: {tail}")
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"desk_prep failed: {exc}")

    def _ensure_controller_active(self) -> None:
        try:
            listed = subprocess.run(
                ["ros2", "control", "list_controllers"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f"list_controllers failed: {exc}")
            return
        text = listed.stdout or ""
        line = next((ln for ln in text.splitlines() if self.controller in ln), "")
        if line and "active" in line and "inactive" not in line:
            self.get_logger().info(f"{self.controller} already active")
            return
        self.get_logger().warn(f"reactivating {self.controller}...")
        sw = subprocess.run(
            ["ros2", "control", "switch_controllers", "--activate", self.controller],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        msg = (sw.stderr or sw.stdout or "").strip()
        if sw.returncode != 0:
            self.get_logger().warn(f"switch_controllers rc={sw.returncode} {msg}")
        else:
            self.get_logger().info(f"{self.controller} activated {msg}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Franka teleop error auto-recovery watchdog")
    parser.add_argument("--cooldown", type=float, default=8.0)
    parser.add_argument(
        "--desk-host",
        default=os.environ.get("FRANKA_HOST")
        or os.environ.get("DESK_HOST_WIFI")
        or "10.229.66.91",
    )
    parser.add_argument("--desk-user", default=os.environ.get("DESK_USER", "franka"))
    parser.add_argument("--desk-password", default=os.environ.get("DESK_PASSWORD", "franka123"))
    parser.add_argument("--no-desk", action="store_true")
    parser.add_argument(
        "--controller",
        default=os.environ.get("TELEOP_CONTROLLER", "joint_impedance_controller"),
    )
    args = parser.parse_args()

    rclpy.init()
    node = TeleopErrorWatchdog(
        cooldown_s=args.cooldown,
        desk_host=args.desk_host,
        desk_user=args.desk_user,
        desk_password=args.desk_password,
        enable_desk=not args.no_desk,
        controller=args.controller,
    )
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
