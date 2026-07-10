#!/usr/bin/env python3
"""Mirror GELLO or Franka joint states from ROS2 into a MuJoCo viewer."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
import os
import threading
import time

os.environ.setdefault("MUJOCO_GL", "glfw")
os.environ.setdefault("DISPLAY", ":1")

import mujoco  # noqa: E402
import mujoco.viewer  # noqa: E402
import rclpy  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy  # noqa: E402
from sensor_msgs.msg import JointState  # noqa: E402
from std_msgs.msg import Float32  # noqa: E402

FRANKA_JOINT_NAMES = [f"fr3_joint{i}" for i in range(1, 8)]
GRIPPER_FINGER_JOINTS = ["fr3_finger_joint1", "fr3_finger_joint2"]
FINGER_OPEN_POS = 0.04
TOPICS = {
    "franka": "/franka_robot_state_broadcaster/measured_joint_states",
    "gello": "/gello/joint_states",
}
GRIPPER_TOPICS = {
    "franka": "/franka_gripper/joint_states",
    "gello": "/gripper/gripper_client/target_gripper_width_percent",
}
QOS = {
    "franka": QoSProfile(
        reliability=ReliabilityPolicy.BEST_EFFORT,
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
    ),
    "gello": QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
    ),
}


def parse_joint_positions(msg: JointState) -> list[float] | None:
    name_to_pos = dict(zip(msg.name, msg.position))
    try:
        return [float(name_to_pos[name]) for name in FRANKA_JOINT_NAMES]
    except KeyError:
        return None


def parse_finger_position(msg: JointState) -> float | None:
    name_to_pos = dict(zip(msg.name, msg.position))
    for name in GRIPPER_FINGER_JOINTS:
        if name in name_to_pos:
            return float(name_to_pos[name])
    return None


class MirrorSubscriber(Node):
    def __init__(self, source: str, topic: str, gripper_topic: str) -> None:
        super().__init__(f"mujoco_{source}_mirror")
        self.source = source
        self.latest: list[float] | None = None
        self.latest_finger: float | None = None
        self.create_subscription(
            JointState,
            topic,
            self._joint_callback,
            QOS[source],
        )
        if source == "gello":
            self.create_subscription(
                Float32,
                gripper_topic,
                self._gripper_percent_callback,
                QOS[source],
            )
        else:
            self.create_subscription(
                JointState,
                gripper_topic,
                self._gripper_joint_callback,
                QOS[source],
            )

    def _joint_callback(self, msg: JointState) -> None:
        self.latest = parse_joint_positions(msg)

    def _gripper_percent_callback(self, msg: Float32) -> None:
        width_percent = max(0.0, min(1.0, float(msg.data)))
        self.latest_finger = width_percent * FINGER_OPEN_POS

    def _gripper_joint_callback(self, msg: JointState) -> None:
        finger_pos = parse_finger_position(msg)
        if finger_pos is not None:
            self.latest_finger = finger_pos


def main() -> int:
    parser = argparse.ArgumentParser(description="MuJoCo ROS2 joint mirror")
    parser.add_argument(
        "--source",
        choices=("franka", "gello"),
        default="franka",
        help="Which ROS2 joint source to visualize",
    )
    args = parser.parse_args()

    topic = os.environ.get("MUJOCO_TOPIC", TOPICS[args.source])
    gripper_topic = os.environ.get("MUJOCO_GRIPPER_TOPIC", GRIPPER_TOPICS[args.source])
    model_path = os.environ.get(
        "MUJOCO_MODEL",
        "/home/yao/franka_mujoco_sync/fr3.mujoco.urdf",
    )
    rate = float(os.environ.get("MUJOCO_SYNC_HZ", "30"))

    model = mujoco.MjModel.from_xml_path(model_path)
    data = mujoco.MjData(model)
    joint_to_qpos: dict[str, int] = {}
    for name in FRANKA_JOINT_NAMES:
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if joint_id < 0:
            raise RuntimeError(f"Joint not found in MuJoCo model: {name}")
        joint_to_qpos[name] = model.jnt_qposadr[joint_id]

    finger_to_qpos: dict[str, int] = {}
    for name in GRIPPER_FINGER_JOINTS:
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if joint_id >= 0:
            finger_to_qpos[name] = model.jnt_qposadr[joint_id]

    rclpy.init()
    node = MirrorSubscriber(args.source, topic, gripper_topic)
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    period = 1.0 / rate if rate > 0 else 0.0
    print(
        f"MuJoCo {args.source} sync at {rate} Hz | topic={topic} | "
        f"gripper={gripper_topic} | fingers={list(finger_to_qpos)} | "
        f"DISPLAY={os.environ.get('DISPLAY', '')}",
        flush=True,
    )

    def read_franka_q_fallback() -> list[float] | None:
        if args.source != "franka" or os.environ.get("MUJOCO_DISABLE_FRANKA_FALLBACK") == "1":
            return None
        reader = os.environ.get("MUJOCO_FRANKA_READER", "/home/yao/franka_mujoco_sync/read_franka_q")
        robot_ip = os.environ.get("FRANKA_HOST", "10.229.66.91")
        env = os.environ.copy()
        library_paths = [
            "/home/yao/franka_ros2_ws/install/libfranka/lib",
            "/opt/ros/humble/lib",
            "/opt/ros/humble/lib/x86_64-linux-gnu",
        ]
        if env.get("LD_LIBRARY_PATH"):
            library_paths.append(env["LD_LIBRARY_PATH"])
        env["LD_LIBRARY_PATH"] = ":".join(library_paths)
        try:
            result = subprocess.run(
                [reader, robot_ip],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=1.0,
            )
            return [float(value) for value in json.loads(result.stdout)["q"]]
        except Exception as exc:
            print(f"Franka fallback read failed: {exc!r}", flush=True)
            return None

    with mujoco.viewer.launch_passive(model, data) as viewer:
        while viewer.is_running():
            joint_values = node.latest
            if joint_values is None and args.source == "franka":
                joint_values = read_franka_q_fallback()
            if joint_values is not None:
                for index, name in enumerate(FRANKA_JOINT_NAMES):
                    data.qpos[joint_to_qpos[name]] = joint_values[index]
            if node.latest_finger is not None and finger_to_qpos:
                finger_pos = max(0.0, min(FINGER_OPEN_POS, node.latest_finger))
                for qpos_index in finger_to_qpos.values():
                    data.qpos[qpos_index] = finger_pos
            mujoco.mj_forward(model, data)
            viewer.sync()
            if period:
                time.sleep(period)

    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
