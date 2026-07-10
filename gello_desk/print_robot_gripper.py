#!/usr/bin/env python3
"""Print real Franka Hand gripper state via libfranka (FCI)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROBOT_IP = os.environ.get("FRANKA_HOST", "10.229.66.91")
READER = os.environ.get(
    "FRANKA_GRIPPER_READER",
    "/home/yao/gello_desk/read_franka_gripper",
)
ENV = {
    **os.environ,
    "LD_LIBRARY_PATH": ":".join(
        [
            "/home/yao/franka_ros2_ws/install/libfranka/lib",
            "/opt/ros/humble/lib/x86_64-linux-gnu",
            "/opt/ros/humble/lib",
            os.environ.get("LD_LIBRARY_PATH", ""),
        ]
    ),
}


def read_once() -> dict | None:
    try:
        raw = subprocess.check_output([READER, ROBOT_IP], env=ENV, text=True, stderr=subprocess.STDOUT)
        return json.loads(raw)
    except subprocess.CalledProcessError as exc:
        return {"error": exc.output.strip()}
    except json.JSONDecodeError as exc:
        return {"error": f"invalid json: {exc}"}


def main() -> int:
    once = "--once" in sys.argv
    print(f"Franka gripper reader @ {ROBOT_IP} (Ctrl+C to stop)", flush=True)
    try:
        while True:
            state = read_once()
            if state and "error" not in state:
                width = float(state.get("width", 0.0))
                max_width = float(state.get("max_width", 0.0))
                pct = (width / max_width * 100.0) if max_width > 0 else 0.0
                grasped = state.get("is_grasped", False)
                print(
                    f"\rWidth: {width*1000:.2f} mm | max {max_width*1000:.2f} mm | "
                    f"{pct:5.1f}% | grasped={grasped}   ",
                    end="",
                    flush=True,
                )
            else:
                err = state.get("error", "unknown") if state else "unknown"
                print(f"\rFranka gripper unavailable: {err}   ", end="", flush=True)
            if once:
                print(flush=True)
                return 0 if state and "error" not in state else 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
