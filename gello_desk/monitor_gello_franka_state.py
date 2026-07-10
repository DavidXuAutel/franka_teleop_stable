#!/usr/bin/env python3
"""Print live GELLO vs Franka joint and gripper states."""

from __future__ import annotations

import argparse
import math
import threading
import time
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import JointState
from std_msgs.msg import Float32


FRANKA_JOINTS = [f"fr3_joint{i}" for i in range(1, 8)]
FINGER_JOINTS = ["fr3_finger_joint1", "fr3_finger_joint2"]
BEST_EFFORT_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
)
RELIABLE_QOS = QoSProfile(
    reliability=ReliabilityPolicy.RELIABLE,
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
)


def pick_positions(msg: JointState, names: list[str]) -> Optional[list[float]]:
    values = dict(zip(msg.name, msg.position))
    try:
        return [float(values[name]) for name in names]
    except KeyError:
        return None


class StateMonitor(Node):
    def __init__(self, rate_hz: float) -> None:
        super().__init__("gello_franka_state_monitor")
        self.rate_hz = rate_hz
        self.lock = threading.Lock()
        self.gello_q: Optional[list[float]] = None
        self.franka_q: Optional[list[float]] = None
        self.gello_gripper_percent: Optional[float] = None
        self.franka_fingers: Optional[list[float]] = None
        self.last_gello = 0.0
        self.last_franka = 0.0
        self.last_gello_gripper = 0.0
        self.last_franka_gripper = 0.0

        self.create_subscription(JointState, "/gello/joint_states", self.on_gello, RELIABLE_QOS)
        self.create_subscription(
            JointState,
            "/franka_robot_state_broadcaster/measured_joint_states",
            self.on_franka,
            BEST_EFFORT_QOS,
        )
        self.create_subscription(
            Float32,
            "/gripper/gripper_client/target_gripper_width_percent",
            self.on_gello_gripper,
            RELIABLE_QOS,
        )
        self.create_subscription(
            JointState,
            "/franka_gripper/joint_states",
            self.on_franka_gripper,
            BEST_EFFORT_QOS,
        )

    def on_gello(self, msg: JointState) -> None:
        q = pick_positions(msg, FRANKA_JOINTS)
        if q is not None:
            with self.lock:
                self.gello_q = q
                self.last_gello = time.time()

    def on_franka(self, msg: JointState) -> None:
        q = pick_positions(msg, FRANKA_JOINTS)
        if q is not None:
            with self.lock:
                self.franka_q = q
                self.last_franka = time.time()

    def on_gello_gripper(self, msg: Float32) -> None:
        with self.lock:
            self.gello_gripper_percent = float(msg.data)
            self.last_gello_gripper = time.time()

    def on_franka_gripper(self, msg: JointState) -> None:
        fingers = pick_positions(msg, FINGER_JOINTS)
        if fingers is not None:
            with self.lock:
                self.franka_fingers = fingers
                self.last_franka_gripper = time.time()

    def run(self) -> None:
        period = 1.0 / self.rate_hz if self.rate_hz > 0 else 0.5
        while rclpy.ok():
            now = time.time()
            with self.lock:
                gello_q = self.gello_q
                franka_q = self.franka_q
                gello_age = now - self.last_gello if self.last_gello else math.inf
                franka_age = now - self.last_franka if self.last_franka else math.inf
                gello_gripper = self.gello_gripper_percent
                franka_fingers = self.franka_fingers
                gello_gripper_age = now - self.last_gello_gripper if self.last_gello_gripper else math.inf
                franka_gripper_age = now - self.last_franka_gripper if self.last_franka_gripper else math.inf

            print("\n=== GELLO / FRANKA STATE ===", flush=True)
            print(f"gello_age={gello_age:.3f}s franka_age={franka_age:.3f}s", flush=True)
            if gello_q is None:
                print("gello_q: missing", flush=True)
            else:
                print("gello_q:  " + " ".join(f"{v:+.4f}" for v in gello_q), flush=True)
            if franka_q is None:
                print("franka_q: missing", flush=True)
            else:
                print("franka_q: " + " ".join(f"{v:+.4f}" for v in franka_q), flush=True)
            if gello_q is not None and franka_q is not None:
                err = [g - f for g, f in zip(gello_q, franka_q)]
                print("error g-f:" + " ".join(f"{v:+.4f}" for v in err), flush=True)
                print(f"max_abs_error={max(abs(v) for v in err):.4f} rad", flush=True)

            print(
                "gello_gripper_percent="
                + (f"{gello_gripper:.3f}" if gello_gripper is not None else "missing")
                + f" age={gello_gripper_age:.3f}s",
                flush=True,
            )
            print(
                "franka_fingers="
                + (" ".join(f"{v:+.4f}" for v in franka_fingers) if franka_fingers is not None else "missing")
                + f" age={franka_gripper_age:.3f}s",
                flush=True,
            )
            time.sleep(period)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rate", type=float, default=2.0)
    args = parser.parse_args()

    rclpy.init()
    node = StateMonitor(args.rate)
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()
    try:
        node.run()
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
