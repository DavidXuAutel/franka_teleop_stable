#!/usr/bin/env python3
"""Live dual RealSense viewer with on-window LeRobot episode START/STOP buttons."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

BAR_H = 84
BTN_W = 120
BTN_H = 48
BTN_GAP = 12


def decode_rgb(msg: Image) -> np.ndarray:
    h, w = int(msg.height), int(msg.width)
    enc = (msg.encoding or "").lower()
    raw = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    if enc.startswith("rgb8"):
        return raw.reshape(h, w, 3)
    if enc.startswith("bgr8"):
        return raw.reshape(h, w, 3)[:, :, ::-1]
    if enc.startswith("rgba8"):
        return raw.reshape(h, w, 4)[:, :, :3]
    if enc.startswith("bgra8"):
        return raw.reshape(h, w, 4)[:, :, [2, 1, 0]]
    return raw[: h * w * 3].reshape(h, w, 3)


class RecordClient:
    def __init__(self, base: str, repo: str, task: str) -> None:
        self.base = base.rstrip("/")
        self.repo = repo
        self.task = task
        self._lock = threading.Lock()
        self.status: dict = {"recording": False, "frames": 0}
        self.last_msg = ""
        self._busy = False

    def refresh(self) -> None:
        try:
            with urllib.request.urlopen(f"{self.base}/record/status", timeout=1.5) as resp:
                data = json.loads(resp.read().decode())
            with self._lock:
                self.status = data
                # Clear sticky poll failures once status is reachable again.
                if self.last_msg.startswith("status err:"):
                    self.last_msg = ""
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.last_msg = f"status err: {exc}"

    def start(self) -> None:
        self._post("/record/start", {"repo": self.repo, "task": self.task})

    def stop(self) -> None:
        self._post("/record/stop", {})

    def _post(self, path: str, body: dict) -> None:
        with self._lock:
            if self._busy:
                return
            self._busy = True
            self.last_msg = "..."

        def _run() -> None:
            try:
                payload = json.dumps(body).encode()
                req = urllib.request.Request(
                    f"{self.base}{path}",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode())
                with self._lock:
                    self.status = data
                    if data.get("recording"):
                        self.last_msg = f"REC frames={data.get('frames', 0)}"
                    elif data.get("saving"):
                        self.last_msg = f"saved frames={data.get('frames', 0)}; remote syncing..."
                    else:
                        sync = data.get("remote_sync")
                        self.last_msg = f"stopped frames={data.get('frames', 0)} sync={sync}"
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")
                try:
                    detail = json.loads(detail).get("detail", detail)
                except Exception:  # noqa: BLE001
                    pass
                with self._lock:
                    self.last_msg = f"HTTP {exc.code}: {str(detail)[:140]}"
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self.last_msg = f"error: {exc}"
            finally:
                with self._lock:
                    self._busy = False
                self.refresh()

        threading.Thread(target=_run, daemon=True).start()


class DualCamViewer(Node):
    def __init__(self, cam1: str, cam2: str) -> None:
        super().__init__("lerobot_dual_cam_view")
        self._lock = threading.Lock()
        self._img1: np.ndarray | None = None
        self._img2: np.ndarray | None = None
        self._ts1 = 0.0
        self._ts2 = 0.0
        qos = qos_profile_sensor_data
        self.create_subscription(Image, cam1, self._on1, qos)
        self.create_subscription(Image, cam2, self._on2, qos)
        self.get_logger().info(f"viewing {cam1} + {cam2}")

    def _on1(self, msg: Image) -> None:
        rgb = decode_rgb(msg)
        with self._lock:
            self._img1 = rgb
            self._ts1 = time.time()

    def _on2(self, msg: Image) -> None:
        rgb = decode_rgb(msg)
        with self._lock:
            self._img2 = rgb
            self._ts2 = time.time()

    def snapshot(self):
        with self._lock:
            return (
                None if self._img1 is None else self._img1.copy(),
                None if self._img2 is None else self._img2.copy(),
                self._ts1,
                self._ts2,
            )


def _btn_rects(canvas_w: int) -> dict[str, tuple[int, int, int, int]]:
    y1 = 10
    y2 = y1 + BTN_H
    x = 16
    boxes = {}
    for name in ("start", "stop", "replay", "abort"):
        boxes[name] = (x, y1, x + BTN_W, y2)
        x += BTN_W + BTN_GAP
    return boxes


def _abort_stack() -> str:
    """Stop full teleop + record stack, then signal caller to exit viewer."""
    script = Path(__file__).resolve().parent / "start_teleop_lerobot_all.sh"
    if not script.is_file():
        script = Path("/home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh")
    log_dir = Path(os.environ.get("TELEOP_LOG_DIR", "/home/yao/gello_logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "cam_view_abort.log"
    try:
        with open(log_path, "ab", buffering=0) as log_f:
            if script.is_file():
                subprocess.run(
                    ["bash", str(script), "stop"],
                    stdout=log_f,
                    stderr=log_f,
                    timeout=60,
                    check=False,
                )
            # Extra sweep (viewer will be killed or exit itself)
            subprocess.run(
                [
                    "bash",
                    "-lc",
                    "pkill -f 'gello_publisher|ros2_control_node|mujoco_ros_mirror|"
                    "realsense2_camera|record_server|teleop_error_watchdog|"
                    "cam_replay|mujoco_replay|franka_fr3|franka_gripper' 2>/dev/null || true",
                ],
                stdout=log_f,
                stderr=log_f,
                timeout=15,
                check=False,
            )
        return f"ABORT: stack stopping (log {log_path})"
    except Exception as exc:  # noqa: BLE001
        return f"ABORT failed: {exc}"


def _draw_button(bar: np.ndarray, box: tuple[int, int, int, int], label: str, color, enabled: bool) -> None:
    x1, y1, x2, y2 = box
    fill = color if enabled else (80, 80, 80)
    cv2.rectangle(bar, (x1, y1), (x2, y2), fill, thickness=-1)
    cv2.rectangle(bar, (x1, y1), (x2, y2), (255, 255, 255), thickness=2)
    tw = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)[0][0]
    tx = x1 + max(4, (BTN_W - tw) // 2)
    ty = y1 + 32
    cv2.putText(bar, label, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)


def _dataset_root(repo: str) -> Path:
    base = os.environ.get("LOCAL_DATASET_ROOT", "/home/yao/lerobot_datasets")
    return Path(base) / repo


_REPLAY_PY_CACHE: str | None = None


def _replay_python() -> str:
    """Prefer /usr/bin/python3 (GUI OpenCV + av). Avoid lerobot headless OpenCV."""
    global _REPLAY_PY_CACHE
    if _REPLAY_PY_CACHE:
        return _REPLAY_PY_CACHE

    preferred = os.environ.get("LEROBOT_REPLAY_PYTHON", "").strip()
    candidates = [c for c in (preferred, "/usr/bin/python3", sys.executable) if c]
    # Never prefer conda lerobot for GUI unless explicitly forced via env.
    if preferred:
        candidates = [preferred]

    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        try:
            proc = subprocess.run(
                [
                    candidate,
                    "-c",
                    "import av, cv2, os\n"
                    "os.environ.setdefault('DISPLAY', ':1')\n"
                    "cv2.namedWindow('_gui_probe')\n"
                    "cv2.destroyWindow('_gui_probe')\n",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":1")},
            )
            if proc.returncode == 0:
                _REPLAY_PY_CACHE = candidate
                return candidate
        except Exception:  # noqa: BLE001
            continue

    # Fallback: system python even if probe failed (DISPLAY may be missing in probe)
    if Path("/usr/bin/python3").is_file():
        _REPLAY_PY_CACHE = "/usr/bin/python3"
        return _REPLAY_PY_CACHE
    _REPLAY_PY_CACHE = sys.executable
    return _REPLAY_PY_CACHE


def _launch_replay(repo: str) -> str:
    root = _dataset_root(repo)
    script = Path(__file__).resolve().parent / "cam_replay.py"
    if not script.is_file():
        return f"missing {script}"
    if not (root / "videos").is_dir():
        return f"no videos yet under {root}"
    env = os.environ.copy()
    env["DISPLAY"] = os.environ.get("DISPLAY") or ":1"
    env.setdefault("MUJOCO_GL", "glfw")
    # Ensure --user site packages (av) are visible for /usr/bin/python3
    env.setdefault("PYTHONUSERBASE", str(Path.home() / ".local"))
    py = _replay_python()
    log_dir = Path(os.environ.get("TELEOP_LOG_DIR", "/home/yao/gello_logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "cam_replay.log"
    log_f = open(log_path, "ab", buffering=0)  # noqa: SIM115
    proc = subprocess.Popen(
        [py, str(script), "--root", str(root), "--loop"],
        env=env,
        stdout=log_f,
        stderr=log_f,
        start_new_session=True,
    )
    return f"REPLAY pid={proc.pid} py={py} (decode may take a few s) log={log_path}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cam1", default="/cam1/cam1/color/image_raw")
    parser.add_argument("--cam2", default="/cam2/cam2/color/image_raw")
    parser.add_argument("--scale", type=float, default=0.75)
    parser.add_argument("--api", default="http://127.0.0.1:8765")
    parser.add_argument("--repo", default="fr3_gello_teleop")
    parser.add_argument("--task", default="franka gello teleop")
    parser.add_argument(
        "--data-root",
        default=os.environ.get("LOCAL_DATASET_ROOT", "/home/yao/lerobot_datasets"),
        help="Local LeRobot datasets parent directory",
    )
    args = parser.parse_args()
    os.environ["LOCAL_DATASET_ROOT"] = args.data_root

    client = RecordClient(args.api, args.repo, args.task)
    client.refresh()

    rclpy.init()
    node = DualCamViewer(args.cam1, args.cam2)
    win = "LeRobot Record | cam1 + cam2"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    click = {"action": None}
    aborting = False

    def on_mouse(event, x, y, _flags, _param) -> None:
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        h = getattr(on_mouse, "canvas_h", 0)
        if h <= 0 or y < h - BAR_H:
            return
        local_y = y - (h - BAR_H)
        rects = _btn_rects(getattr(on_mouse, "canvas_w", 800))
        for name, (x1, y1, x2, y2) in rects.items():
            if x1 <= x <= x2 and y1 <= local_y <= y2:
                click["action"] = name
                break

    cv2.setMouseCallback(win, on_mouse)
    last_poll = 0.0
    data_path = str(_dataset_root(args.repo))

    try:
        while rclpy.ok() and not aborting:
            rclpy.spin_once(node, timeout_sec=0.02)
            now = time.time()
            if now - last_poll > 0.5:
                client.refresh()
                last_poll = now

            action = click["action"]
            if action == "start":
                click["action"] = None
                client.start()
            elif action == "stop":
                click["action"] = None
                client.stop()
            elif action == "replay":
                click["action"] = None
                with client._lock:
                    client.last_msg = _launch_replay(args.repo)
            elif action == "abort":
                click["action"] = None
                with client._lock:
                    recording_now = bool(client.status.get("recording"))
                    client.last_msg = "ABORT: shutting down..."
                if recording_now:
                    try:
                        client.stop()
                        time.sleep(0.3)
                    except Exception:  # noqa: BLE001
                        pass
                # Run stack stop off the UI thread briefly then exit.
                msg = _abort_stack()
                with client._lock:
                    client.last_msg = msg
                aborting = True
                break

            img1, img2, ts1, ts2 = node.snapshot()
            panels = []
            for name, img, ts in (("cam1", img1, ts1), ("cam2", img2, ts2)):
                if img is None:
                    panel = np.zeros((480, 640, 3), dtype=np.uint8)
                    text = f"{name}: waiting"
                else:
                    panel = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                    age = now - ts if ts else -1
                    text = f"{name}: {panel.shape[1]}x{panel.shape[0]} age={age:.2f}s"
                cv2.putText(panel, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                if args.scale != 1.0:
                    panel = cv2.resize(panel, None, fx=args.scale, fy=args.scale)
                panels.append(panel)

            h = max(p.shape[0] for p in panels)
            fixed = []
            for p in panels:
                if p.shape[0] != h:
                    p = cv2.resize(p, (int(p.shape[1] * h / p.shape[0]), h))
                fixed.append(p)
            canvas = np.hstack(fixed)
            bar = np.zeros((BAR_H, canvas.shape[1], 3), dtype=np.uint8)
            bar[:] = (40, 40, 40)

            with client._lock:
                recording = bool(client.status.get("recording"))
                saving = bool(client.status.get("saving"))
                frames = int(client.status.get("frames") or 0)
                msg = client.last_msg
                err = client.status.get("last_error")
                remote_sync = client.status.get("remote_sync")
                streams = client.status.get("streams") or {}
                stream_ok = streams.get("ok")
                busy = client._busy or saving

            rects = _btn_rects(canvas.shape[1])
            _draw_button(bar, rects["start"], "START", (0, 160, 0), enabled=not recording and not busy)
            _draw_button(bar, rects["stop"], "STOP", (0, 0, 200), enabled=recording and not busy)
            _draw_button(bar, rects["replay"], "REPLAY", (180, 120, 0), enabled=not recording and not busy)
            _draw_button(bar, rects["abort"], "ABORT", (0, 0, 120), enabled=True)

            if saving:
                phase = "SAVING"
            elif recording:
                phase = "REC"
            else:
                phase = "IDLE"
            status = f"{phase} frames={frames} streams={'OK' if stream_ok else '?'}"
            if client._busy and not saving:
                status = "BUSY " + status
            color = (0, 0, 255) if recording else ((0, 200, 255) if saving else (200, 200, 200))
            x_info = 16 + 4 * (BTN_W + BTN_GAP)
            cv2.putText(bar, status, (x_info, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            cv2.putText(
                bar,
                f"save: {data_path}",
                (x_info, 52),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (170, 170, 170),
                1,
            )
            # Remote sync failures are warnings, not stop/save errors.
            sync_fail = isinstance(remote_sync, str) and remote_sync.startswith("failed:")
            if msg:
                tip = msg
            elif err:
                tip = f"ERR: {err}"
            elif sync_fail:
                tip = f"local ok; remote {remote_sync}"
            elif remote_sync == "syncing...":
                tip = "local saved; remote syncing..."
            else:
                tip = "ABORT=stop all teleop"
            tip_color = (
                (0, 0, 255)
                if (
                    err
                    or tip.startswith("HTTP")
                    or tip.startswith("error")
                    or tip.startswith("status err")
                    or tip.startswith("ABORT")
                )
                else ((0, 165, 255) if sync_fail or saving else (180, 180, 180))
            )
            cv2.putText(bar, tip[:90], (x_info, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.4, tip_color, 1)

            out = np.vstack([canvas, bar])
            on_mouse.canvas_h = out.shape[0]
            on_mouse.canvas_w = out.shape[1]
            cv2.imshow(win, out)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("s"):
                client.start()
            if key == ord("e"):
                client.stop()
            if key == ord("p"):
                with client._lock:
                    client.last_msg = _launch_replay(args.repo)
            if key == ord("x"):
                click["action"] = "abort"
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
