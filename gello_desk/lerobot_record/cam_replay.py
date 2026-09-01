#!/usr/bin/env python3
"""Replay dual-camera episode videos (PyAV) and spawn MuJoCo joint replay.

Close with: q / Esc / window X / CLOSE button.
DISCARD: click twice to confirm — exits and deletes the replayed episode's full local assets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import av
import cv2
import numpy as np

BAR_H = 108
BTN_W = 120
BTN_H = 40
BTN_GAP = 12


def _abspath(path: Path | None) -> str:
    if path is None:
        return "(none)"
    return str(path.expanduser().resolve())


def _fit_text(text: str, max_chars: int) -> str:
    """Keep full path when possible; otherwise keep head+tail so path stays identifiable."""
    if max_chars < 24 or len(text) <= max_chars:
        return text
    keep = max_chars - 3
    head = keep // 2
    tail = keep - head
    return f"{text[:head]}...{text[-tail:]}"


def _latest_video(root: Path, cam_key: str) -> Path | None:
    base = root / "videos" / f"observation.images.{cam_key}"
    if not base.is_dir():
        return None
    files = sorted(base.rglob("*.mp4"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _latest_sidecar(root: Path) -> Path | None:
    base = root / "meta" / "sidecars"
    if not base.is_dir():
        return None
    files = sorted(base.glob("episode_*.npz"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _load_info(root: Path) -> dict:
    p = root / "meta" / "info.json"
    if not p.is_file():
        return {}
    return json.loads(p.read_text())


def _file_index_from_video(path: Path) -> tuple[str, int] | None:
    """Return (chunk_dir_name, file_index) from .../chunk-000/file-018.mp4."""
    m = re.search(r"(chunk-\d+)/file-(\d+)\.mp4$", str(path).replace("\\", "/"))
    if not m:
        return None
    return m.group(1), int(m.group(2))


def _episode_index_from_sidecar(path: Path | None) -> int | None:
    if path is None:
        return None
    m = re.search(r"episode_(\d+)\.npz$", path.name)
    return int(m.group(1)) if m else None


def _unlink(path: Path, deleted: list[str]) -> None:
    if path.is_file():
        path.unlink()
        deleted.append(str(path))
        print(f"deleted {path}", flush=True)


def discard_episode_assets(
    root: Path,
    *,
    v1: Path,
    v2: Path,
    sidecar: Path | None,
    frame_count: int,
) -> list[str]:
    """Delete the full local asset set for the episode currently being replayed.

    Removes:
      - cam1 / cam2 mp4 being replayed
      - matching data/*.parquet and meta/episodes/*.parquet (same file_index)
      - sidecar episode_XXXXXX.npz
      - empty leftover dirs are left in place
    Updates meta/info.json counters (total_episodes / total_frames / splits).
    """
    deleted: list[str] = []
    root = root.resolve()

    fi_info = _file_index_from_video(v1) or _file_index_from_video(v2)
    epi = _episode_index_from_sidecar(sidecar)

    # 1) videos currently open in this replay
    for p in (v1, v2):
        _unlink(p.resolve(), deleted)

    # 2) sidecar joints
    if sidecar is not None:
        _unlink(sidecar.resolve(), deleted)

    # 3) parquet siblings by file_index (v3 layout: one episode per file in this setup)
    if fi_info is not None:
        chunk_name, file_index = fi_info
        for rel in (
            root / "data" / chunk_name / f"file-{file_index:03d}.parquet",
            root / "meta" / "episodes" / chunk_name / f"file-{file_index:03d}.parquet",
        ):
            _unlink(rel, deleted)

        # optional per-frame image dumps if present
        for cam in ("cam1", "cam2"):
            img_base = root / "images" / f"observation.images.{cam}" / chunk_name
            if img_base.is_dir():
                for p in img_base.glob(f"*file-{file_index:03d}*"):
                    if p.is_file():
                        _unlink(p, deleted)
                    elif p.is_dir():
                        for child in sorted(p.rglob("*"), reverse=True):
                            if child.is_file():
                                _unlink(child, deleted)
                        try:
                            p.rmdir()
                            deleted.append(str(p))
                        except OSError:
                            pass

    # 4) update info.json
    info_path = root / "meta" / "info.json"
    if info_path.is_file():
        info = json.loads(info_path.read_text())
        te = int(info.get("total_episodes") or 0)
        tf = int(info.get("total_frames") or 0)
        new_te = max(0, te - 1)
        new_tf = max(0, tf - max(0, int(frame_count)))
        info["total_episodes"] = new_te
        info["total_frames"] = new_tf
        info["splits"] = {"train": f"0:{new_te}"}
        info_path.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n")
        deleted.append(f"{info_path} (updated episodes={new_te} frames={new_tf})")
        print(
            f"updated info.json total_episodes {te}->{new_te} total_frames {tf}->{new_tf} "
            f"(discarded episode_index={epi} file_index={fi_info[1] if fi_info else '?'})",
            flush=True,
        )

    if not deleted:
        print("discard: nothing deleted", flush=True)
    return deleted


def _decode_all(path: Path) -> list[np.ndarray]:
    frames: list[np.ndarray] = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for packet in container.demux(stream):
            for frame in packet.decode():
                frames.append(frame.to_ndarray(format="bgr24"))
    return frames


def _window_closed(win: str) -> bool:
    """Return True only when the user has closed the window.

    OpenCV/GTK often returns -1 for WND_PROP_VISIBLE before/around the first
    imshow; treating ``prop < 1`` as closed makes the replay exit immediately
    (looks like REPLAY does nothing).
    """
    try:
        prop = cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE)
        return prop == 0
    except Exception:  # noqa: BLE001
        return False


def _kill_proc(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=2)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _launch_mujoco(root: Path, sidecar: Path | None) -> subprocess.Popen | None:
    script = Path(__file__).resolve().parent / "mujoco_replay.py"
    if not script.is_file():
        print(f"missing {script}", flush=True)
        return None
    env = os.environ.copy()
    env.setdefault("DISPLAY", os.environ.get("DISPLAY", ":1"))
    env.setdefault("MUJOCO_GL", "glfw")
    cmd = [sys.executable, str(script), "--root", str(root), "--loop"]
    if sidecar is not None:
        cmd.extend(["--sidecar", str(sidecar)])
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"spawned MuJoCo replay pid={proc.pid}", flush=True)
    return proc


def _btn_box(index: int) -> tuple[int, int, int, int]:
    x1 = 12 + index * (BTN_W + BTN_GAP)
    y1 = 8
    return x1, y1, x1 + BTN_W, y1 + BTN_H


def _draw_btn(bar: np.ndarray, box: tuple[int, int, int, int], label: str, color) -> None:
    x1, y1, x2, y2 = box
    cv2.rectangle(bar, (x1, y1), (x2, y2), color, -1)
    cv2.rectangle(bar, (x1, y1), (x2, y2), (255, 255, 255), 2)
    tw = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0][0]
    tx = x1 + max(4, (BTN_W - tw) // 2)
    cv2.putText(bar, label, (tx, y1 + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)


def main() -> int:
    parser = argparse.ArgumentParser(description="LeRobot dual-cam + MuJoCo replay")
    parser.add_argument("--root", default="/home/yao/lerobot_datasets/fr3_gello_teleop")
    parser.add_argument("--scale", type=float, default=0.75)
    parser.add_argument("--loop", action="store_true", help="Loop video playback")
    parser.add_argument("--no-mujoco", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    info = _load_info(root)
    fps = float(info.get("fps") or 15)
    v1 = _latest_video(root, "cam1")
    v2 = _latest_video(root, "cam2")
    if v1 is None or v2 is None:
        print(f"No cam videos under {root}/videos/")
        return 1
    v1 = v1.resolve()
    v2 = v2.resolve()
    sidecar = _latest_sidecar(root)
    if sidecar is not None:
        sidecar = sidecar.resolve()

    path_root = _abspath(root)
    path_cam1 = _abspath(v1)
    path_cam2 = _abspath(v2)
    path_sidecar = _abspath(sidecar)
    episode_tag = sidecar.name if sidecar is not None else (v1.name if v1 else "unknown")
    episode_index = _episode_index_from_sidecar(sidecar)

    print(f"Replay root: {path_root}")
    print(f"  decoding cam1: {path_cam1}")
    print(f"  decoding cam2: {path_cam2}")
    print(f"  sidecar: {path_sidecar}")
    try:
        frames1 = _decode_all(v1)
        frames2 = _decode_all(v2)
    except Exception as exc:  # noqa: BLE001
        print(f"PyAV decode failed: {exc}")
        return 1
    if not frames1 and not frames2:
        print("No frames decoded")
        return 1

    total = max(len(frames1), len(frames2))
    play_fps = fps if fps > 1 else 15.0
    delay_ms = max(1, int(1000 / play_fps))
    print(f"  frames={total}  fps={play_fps:.1f}  episode={episode_tag}")
    print("  close: q / Esc / X / CLOSE | discard: DISCARD (click twice)", flush=True)

    mujoco_proc: subprocess.Popen | None = None
    if not args.no_mujoco:
        mujoco_proc = _launch_mujoco(root, sidecar)

    win = f"LeRobot Replay | {episode_tag} | {path_root}"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    paused = False
    idx = 0
    click = {"close": False, "discard": False}
    discard_armed_until = 0.0
    status_tip = ""

    close_box = _btn_box(0)
    discard_box = _btn_box(1)

    def on_mouse(event, x, y, _flags, _param) -> None:
        nonlocal discard_armed_until, status_tip
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        h = getattr(on_mouse, "canvas_h", 0)
        if h <= 0 or y < h - BAR_H:
            return
        local_y = y - (h - BAR_H)
        lx1, ly1, lx2, ly2 = close_box
        if lx1 <= x <= lx2 and ly1 <= local_y <= ly2:
            click["close"] = True
            return
        dx1, dy1, dx2, dy2 = discard_box
        if dx1 <= x <= dx2 and dy1 <= local_y <= dy2:
            now = time.monotonic()
            if now <= discard_armed_until:
                click["discard"] = True
                status_tip = "DISCARD confirmed — deleting..."
            else:
                discard_armed_until = now + 5.0
                status_tip = "CONFIRM: click DISCARD again within 5s to DELETE this episode"

    cv2.setMouseCallback(win, on_mouse)

    def blank_like(other: np.ndarray | None) -> np.ndarray:
        if other is not None:
            return np.zeros_like(other)
        return np.zeros((480, 640, 3), dtype=np.uint8)

    do_discard = False
    try:
        shown_once = False
        while True:
            if click["close"]:
                break
            if click["discard"]:
                do_discard = True
                break
            if shown_once and _window_closed(win):
                break

            if not paused:
                f1 = frames1[idx] if idx < len(frames1) else blank_like(frames1[0] if frames1 else None)
                f2 = frames2[idx] if idx < len(frames2) else blank_like(frames2[0] if frames2 else None)
            else:
                f1 = frames1[min(idx, len(frames1) - 1)] if frames1 else blank_like(None)
                f2 = frames2[min(idx, len(frames2) - 1)] if frames2 else blank_like(None)

            if args.scale != 1.0:
                f1 = cv2.resize(f1, None, fx=args.scale, fy=args.scale)
                f2 = cv2.resize(f2, None, fx=args.scale, fy=args.scale)
            h = max(f1.shape[0], f2.shape[0])
            if f1.shape[0] != h:
                f1 = cv2.resize(f1, (int(f1.shape[1] * h / f1.shape[0]), h))
            if f2.shape[0] != h:
                f2 = cv2.resize(f2, (int(f2.shape[1] * h / f2.shape[0]), h))

            epi_label = f"ep{episode_index}" if episode_index is not None else episode_tag
            cv2.putText(
                f1,
                f"cam1  {idx + 1}/{total}  {epi_label}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
            )
            cv2.putText(
                f2,
                f"cam2  {idx + 1}/{total}  {epi_label}",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
            )
            canvas = np.hstack([f1, f2])
            bar = np.zeros((BAR_H, canvas.shape[1], 3), dtype=np.uint8)
            bar[:] = (35, 35, 35)
            _draw_btn(bar, close_box, "CLOSE", (0, 0, 180))
            armed = time.monotonic() <= discard_armed_until
            _draw_btn(
                bar,
                discard_box,
                "CONFIRM" if armed else "DISCARD",
                (0, 0, 220) if armed else (0, 90, 180),
            )
            tip = status_tip or (
                f"{'PAUSE' if paused else 'PLAY'}  {episode_tag}  "
                f"q/Esc/CLOSE=exit  DISCARD×2=delete episode  space/a/d/r"
            )
            tip_color = (0, 0, 255) if (armed or status_tip.startswith("CONFIRM") or status_tip.startswith("DISCARD")) else (220, 220, 220)
            tip_x = discard_box[2] + 16
            cv2.putText(bar, tip[:95], (tip_x, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.45, tip_color, 1)
            max_chars = max(40, canvas.shape[1] // 7)
            path_lines = [
                f"root: {path_root}",
                f"cam1: {path_cam1}",
                f"cam2: {path_cam2}",
                f"sidecar: {path_sidecar}",
            ]
            y = 54
            for line in path_lines:
                cv2.putText(
                    bar,
                    _fit_text(line, max_chars),
                    (12, y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.38,
                    (180, 220, 180),
                    1,
                )
                y += 13
            out = np.vstack([canvas, bar])
            on_mouse.canvas_h = out.shape[0]
            on_mouse.canvas_w = out.shape[1]
            cv2.imshow(win, out)
            shown_once = True

            key = cv2.waitKeyEx(delay_ms if not paused else 30)
            key8 = key & 0xFF if key >= 0 else 255
            if key in (ord("q"), ord("Q"), 27) or key8 in (ord("q"), ord("Q"), 27):
                break
            if key8 == ord(" "):
                paused = not paused
            if key8 == ord("r"):
                idx = 0
                paused = False
                continue
            if key8 == ord("a"):
                idx = max(0, idx - int(play_fps))
                paused = True
                continue
            if key8 == ord("d"):
                idx = min(total - 1, idx + int(play_fps))
                paused = True
                continue

            if not paused:
                idx += 1
                if idx >= total:
                    if args.loop:
                        idx = 0
                    else:
                        idx = total - 1
                        paused = True
    finally:
        _kill_proc(mujoco_proc)
        # Drop decoded frames so we don't hold huge RAM during delete.
        frames1.clear()
        frames2.clear()
        try:
            cv2.destroyWindow(win)
        except Exception:  # noqa: BLE001
            pass
        cv2.destroyAllWindows()
        for _ in range(5):
            cv2.waitKey(1)

        if do_discard:
            print(
                f"DISCARD episode assets root={root} tag={episode_tag} frames={total}",
                flush=True,
            )
            try:
                deleted = discard_episode_assets(
                    root,
                    v1=v1,
                    v2=v2,
                    sidecar=sidecar,
                    frame_count=total,
                )
                print(f"DISCARD done n={len(deleted)}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"DISCARD failed: {exc}", flush=True)
                return 2
        else:
            print("replay closed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
