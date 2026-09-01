#!/usr/bin/env python3
"""ROS2 bridge: Franka/GELLO joints + two RealSense color topics (ROS only)."""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

import numpy as np

JOINT_NAMES = [f"fr3_joint{i}" for i in range(1, 8)]


def _ordered_positions(names: list[str], positions: list[float]) -> np.ndarray | None:
    mapping = {n: float(p) for n, p in zip(names, positions)}
    if not all(j in mapping for j in JOINT_NAMES):
        alt: dict[str, float] = {}
        for n, p in mapping.items():
            for j in JOINT_NAMES:
                if n.endswith(j) or j.endswith(n) or n == j:
                    alt[j] = float(p)
        mapping = {**mapping, **alt}
    if not all(j in mapping for j in JOINT_NAMES):
        return None
    return np.array([mapping[j] for j in JOINT_NAMES], dtype=np.float32)


def _normalize_gello_names(names: list[str]) -> list[str]:
    out = []
    for n in names:
        if n.startswith("fr3_"):
            out.append(n)
        elif n.startswith("joint"):
            out.append("fr3_" + n)
        else:
            out.append(n)
    return out


def _image_msg_to_rgb(msg) -> np.ndarray:
    h, w = int(msg.height), int(msg.width)
    encoding = (msg.encoding or "").lower()
    raw = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    if encoding.startswith("rgb8"):
        return np.ascontiguousarray(raw.reshape(h, w, 3))
    if encoding.startswith("bgr8"):
        return np.ascontiguousarray(raw.reshape(h, w, 3)[:, :, ::-1])
    if encoding.startswith("rgba8"):
        return np.ascontiguousarray(raw.reshape(h, w, 4)[:, :, :3])
    if encoding.startswith("bgra8"):
        return np.ascontiguousarray(raw.reshape(h, w, 4)[:, :, [2, 1, 0]])
    if encoding in ("mono8", "8uc1"):
        g = raw.reshape(h, w)
        return np.stack([g, g, g], axis=-1)
    if raw.size >= h * w * 3:
        return np.ascontiguousarray(raw[: h * w * 3].reshape(h, w, 3))
    raise ValueError(f"Unsupported image encoding: {encoding!r}")


@dataclass
class LatestSample:
    franka_q: np.ndarray | None = None
    gello_q: np.ndarray | None = None
    gripper_obs: float = 0.0
    gripper_act: float = 0.0
    cam1: np.ndarray | None = None
    cam2: np.ndarray | None = None
    franka_stamp: float = 0.0
    gello_stamp: float = 0.0
    cam1_stamp: float = 0.0
    cam2_stamp: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


class RosBridge:
    def __init__(
        self,
        franka_topic: str,
        gello_topic: str,
        gripper_topic: str,
        gello_gripper_topic: str,
        cam1_topic: str,
        cam2_topic: str,
        stale_s: float = 0.35,
        image_stale_s: float = 0.75,
    ) -> None:
        self.franka_topic = franka_topic
        self.gello_topic = gello_topic
        self.gripper_topic = gripper_topic
        self.gello_gripper_topic = gello_gripper_topic
        self.cam1_topic = cam1_topic
        self.cam2_topic = cam2_topic
        self.stale_s = stale_s
        self.image_stale_s = image_stale_s
        self.sample = LatestSample()
        self._node = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._image_errors = 0
        self._first_logged: set[str] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin_forever, name="ros-bridge", daemon=True)
        self._thread.start()
        deadline = time.time() + 5.0
        while self._node is None and time.time() < deadline:
            time.sleep(0.05)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._thread = None

    def _log_first(self, key: str, detail: str = "") -> None:
        if key in self._first_logged:
            return
        self._first_logged.add(key)
        print(f"[ros_bridge] first {key} {detail}", flush=True)

    def _spin_forever(self) -> None:
        """Restart inner spin if context is externally shut down."""
        while not self._stop.is_set():
            try:
                self._spin_once_session()
            except Exception as exc:  # noqa: BLE001
                print(f"[ros_bridge] session ended: {exc!r}", flush=True)
                traceback.print_exc()
            self._node = None
            if self._stop.is_set():
                break
            time.sleep(0.5)

    def _spin_once_session(self) -> None:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
        from sensor_msgs.msg import Image, JointState
        from std_msgs.msg import Float32

        # Use BEST_EFFORT for joints: franka_robot_state_broadcaster is BE;
        # also matches more camera/sensor styles across restarts.
        joint_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )
        # GELLO publishes RELIABLE — offer both by using a matching compatible profile:
        # In ROS2, BEST_EFFORT subscriber does NOT match RELIABLE publisher.
        # So use RELIABLE for GELLO, BEST_EFFORT for Franka.
        gello_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        if not rclpy.ok():
            rclpy.init(args=None)
        node = Node("lerobot_record_bridge")
        self._node = node
        qos = qos_profile_sensor_data

        def on_franka(msg: JointState) -> None:
            q = _ordered_positions(list(msg.name), list(msg.position))
            if q is None:
                return
            with self.sample.lock:
                self.sample.franka_q = q
                self.sample.franka_stamp = time.time()
            self._log_first("franka", f"topic={self.franka_topic}")

        def on_gello(msg: JointState) -> None:
            q = _ordered_positions(list(msg.name), list(msg.position))
            if q is None:
                q = _ordered_positions(_normalize_gello_names(list(msg.name)), list(msg.position))
            if q is None:
                return
            with self.sample.lock:
                self.sample.gello_q = q
                self.sample.gello_stamp = time.time()
            self._log_first("gello", f"topic={self.gello_topic}")

        def on_gripper(msg: JointState) -> None:
            width = float(sum(msg.position)) if msg.position else 0.0
            with self.sample.lock:
                self.sample.gripper_obs = width

        def on_gello_grip(msg: Float32) -> None:
            val = float(msg.data)
            if val > 1.5:
                val = val / 100.0
            with self.sample.lock:
                self.sample.gripper_act = val

        def make_cam_cb(which: str):
            def _cb(msg: Image) -> None:
                try:
                    rgb = _image_msg_to_rgb(msg)
                except Exception as exc:  # noqa: BLE001
                    self._image_errors += 1
                    if self._image_errors <= 5:
                        node.get_logger().warning(f"{which} decode failed: {exc}")
                    return
                with self.sample.lock:
                    if which == "cam1":
                        self.sample.cam1 = rgb
                        self.sample.cam1_stamp = time.time()
                    else:
                        self.sample.cam2 = rgb
                        self.sample.cam2_stamp = time.time()
                self._log_first(which)

            return _cb

        node.create_subscription(JointState, self.franka_topic, on_franka, joint_qos)
        node.create_subscription(JointState, self.gello_topic, on_gello, gello_qos)
        node.create_subscription(JointState, self.gripper_topic, on_gripper, joint_qos)
        node.create_subscription(Float32, self.gello_gripper_topic, on_gello_grip, gello_qos)
        node.create_subscription(Image, self.cam1_topic, make_cam_cb("cam1"), qos)
        node.create_subscription(Image, self.cam2_topic, make_cam_cb("cam2"), qos)
        print(
            f"[ros_bridge] subscribed franka={self.franka_topic} gello={self.gello_topic} "
            f"cam1={self.cam1_topic} cam2={self.cam2_topic}",
            flush=True,
        )

        while not self._stop.is_set():
            rclpy.spin_once(node, timeout_sec=0.05)

        node.destroy_node()
        # Do not call rclpy.shutdown() here — other threads/tools may share the context.
        self._node = None

    def get_latest(self) -> dict[str, Any]:
        now = time.time()
        with self.sample.lock:
            franka = None if self.sample.franka_q is None else self.sample.franka_q.copy()
            gello = None if self.sample.gello_q is None else self.sample.gello_q.copy()
            cam1 = None if self.sample.cam1 is None else self.sample.cam1.copy()
            cam2 = None if self.sample.cam2 is None else self.sample.cam2.copy()
            g_obs = float(self.sample.gripper_obs)
            g_act = float(self.sample.gripper_act)
            fs = self.sample.franka_stamp
            gs = self.sample.gello_stamp
            c1s = self.sample.cam1_stamp
            c2s = self.sample.cam2_stamp

        ok_franka = franka is not None and (now - fs) <= self.stale_s
        ok_gello = gello is not None and (now - gs) <= self.stale_s
        ok_cam1 = cam1 is not None and (now - c1s) <= self.image_stale_s
        ok_cam2 = cam2 is not None and (now - c2s) <= self.image_stale_s
        return {
            "franka_q": franka,
            "gello_q": gello,
            "gripper_obs": g_obs,
            "gripper_act": g_act,
            "cam1": cam1,
            "cam2": cam2,
            "ok": bool(ok_franka and ok_gello and ok_cam1 and ok_cam2),
            "ok_franka": ok_franka,
            "ok_gello": ok_gello,
            "ok_cam1": ok_cam1,
            "ok_cam2": ok_cam2,
            "age": {
                "franka": None if not fs else now - fs,
                "gello": None if not gs else now - gs,
                "cam1": None if not c1s else now - c1s,
                "cam2": None if not c2s else now - c2s,
            },
        }
