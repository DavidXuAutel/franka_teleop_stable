#!/usr/bin/env python3
"""Replay recorded Franka joint trajectories in a standalone MuJoCo viewer."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("MUJOCO_GL", "glfw")
os.environ.setdefault("DISPLAY", os.environ.get("DISPLAY", ":1"))

import mujoco  # noqa: E402
import mujoco.viewer  # noqa: E402
import numpy as np  # noqa: E402

FRANKA_JOINT_NAMES = [f"fr3_joint{i}" for i in range(1, 8)]
GRIPPER_FINGER_JOINTS = ["fr3_finger_joint1", "fr3_finger_joint2"]
FINGER_OPEN_POS = 0.04


def _latest_sidecar(root: Path) -> Path | None:
    base = root / "meta" / "sidecars"
    if not base.is_dir():
        return None
    files = sorted(base.glob("episode_*.npz"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _load_states(path: Path) -> tuple[np.ndarray, float]:
    data = np.load(path)
    states = np.asarray(data["observation_state"], dtype=np.float32)
    fps = float(np.asarray(data["fps"]).reshape(-1)[0]) if "fps" in data.files else 15.0
    return states, fps


def _load_info_fps(root: Path) -> float:
    p = root / "meta" / "info.json"
    if not p.is_file():
        return 15.0
    try:
        return float(json.loads(p.read_text()).get("fps") or 15)
    except Exception:  # noqa: BLE001
        return 15.0


def main() -> int:
    parser = argparse.ArgumentParser(description="MuJoCo offline replay from LeRobot sidecar")
    parser.add_argument("--root", default="/home/yao/lerobot_datasets/fr3_gello_teleop")
    parser.add_argument("--sidecar", default="", help="Explicit episode_XXXXXX.npz path")
    parser.add_argument(
        "--model",
        default=os.environ.get("MUJOCO_MODEL", "/home/yao/franka_mujoco_sync/fr3.mujoco.urdf"),
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--source", choices=("state", "action"), default="state")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    sidecar = Path(args.sidecar).expanduser() if args.sidecar else _latest_sidecar(root)
    if sidecar is None or not sidecar.is_file():
        print(f"No sidecar joints under {root}/meta/sidecars/ — record a new episode first.")
        # Still open MuJoCo so the user sees a viewer; hold home pose.
        states = np.zeros((1, 8), dtype=np.float32)
        fps = _load_info_fps(root)
    else:
        states, fps = _load_states(sidecar)
        if args.source == "action":
            data = np.load(sidecar)
            if "action" in data.files:
                states = np.asarray(data["action"], dtype=np.float32)
        print(f"MuJoCo replay sidecar={sidecar} frames={len(states)} fps={fps}")

    model = mujoco.MjModel.from_xml_path(str(args.model))
    data = mujoco.MjData(model)
    joint_to_qpos: dict[str, int] = {}
    for name in FRANKA_JOINT_NAMES:
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if jid < 0:
            raise RuntimeError(f"Joint not found: {name}")
        joint_to_qpos[name] = model.jnt_qposadr[jid]

    finger_to_qpos: dict[str, int] = {}
    for name in GRIPPER_FINGER_JOINTS:
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if jid >= 0:
            finger_to_qpos[name] = model.jnt_qposadr[jid]

    period = 1.0 / max(fps, 1.0)
    idx = 0
    n = len(states)

    with mujoco.viewer.launch_passive(model, data) as viewer:
        while viewer.is_running():
            row = states[min(idx, n - 1)]
            q = row[:7]
            for i, name in enumerate(FRANKA_JOINT_NAMES):
                data.qpos[joint_to_qpos[name]] = float(q[i])
            if finger_to_qpos and row.shape[0] >= 8:
                # observation.state gripper is finger width (m); action may be percent.
                g = float(row[7])
                if args.source == "action" and 0.0 <= g <= 1.0:
                    finger = g * FINGER_OPEN_POS
                else:
                    finger = max(0.0, min(FINGER_OPEN_POS, g if g <= FINGER_OPEN_POS else g * FINGER_OPEN_POS))
                for qpos_index in finger_to_qpos.values():
                    data.qpos[qpos_index] = finger
            mujoco.mj_forward(model, data)
            viewer.sync()
            time.sleep(period)
            idx += 1
            if idx >= n:
                if args.loop:
                    idx = 0
                else:
                    # Hold final pose.
                    idx = n - 1
                    time.sleep(0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
