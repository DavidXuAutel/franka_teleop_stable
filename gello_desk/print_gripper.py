#!/usr/bin/env python3
"""Print GELLO gripper width percent in real time."""

from __future__ import annotations

import sys
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

TOPIC = "/gripper/gripper_client/target_gripper_width_percent"


class GripperMonitor(Node):
    def __init__(self) -> None:
        super().__init__("gripper_monitor")
        self.latest: float | None = None
        self.create_subscription(Float32, TOPIC, self._callback, 10)

    def _callback(self, msg: Float32) -> None:
        self.latest = float(msg.data)


def main() -> int:
    rclpy.init()
    node = GripperMonitor()
    print(f"Monitoring {TOPIC} (Ctrl+C to stop)", flush=True)
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
            if node.latest is not None:
                pct = node.latest * 100.0
                print(
                    f"\rGripper: {node.latest:.4f} ({pct:6.2f}%)  ",
                    end="",
                    flush=True,
                )
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
