"""LeRobot v3 episode recorder: Franka + GELLO + dual ROS RealSense cameras."""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ros_bridge import RosBridge
from sync_remote import load_env, rsync_repo

sys.path = [p for p in sys.path if "workspace/lerobot" not in p]


def _import_dataset():
    from lerobot.datasets.lerobot_dataset import CODEBASE_VERSION, LeRobotDataset

    return LeRobotDataset, CODEBASE_VERSION, "lerobot.datasets"


LeRobotDataset, CODEBASE_VERSION, DATASET_IMPORT = _import_dataset()

ENV = load_env()
FPS = int(ENV.get("FPS", "15"))
LOCAL_ROOT = Path(ENV.get("LOCAL_DATASET_ROOT", "/home/yao/lerobot_datasets"))
DEFAULT_REPO = ENV.get("DEFAULT_REPO", "fr3_gello_teleop")
DEFAULT_TASK = ENV.get("DEFAULT_TASK", "franka gello teleop").strip('"')
HOST = ENV.get("LEROBOT_RECORD_HOST", "127.0.0.1")
PORT = int(ENV.get("LEROBOT_RECORD_PORT", "8765"))

CAM_SHAPE = (480, 640, 3)
FEATURES = {
    "observation.state": {
        "dtype": "float32",
        "shape": (8,),
        "names": [
            "fr3_joint1",
            "fr3_joint2",
            "fr3_joint3",
            "fr3_joint4",
            "fr3_joint5",
            "fr3_joint6",
            "fr3_joint7",
            "gripper",
        ],
    },
    "action": {
        "dtype": "float32",
        "shape": (8,),
        "names": [
            "fr3_joint1",
            "fr3_joint2",
            "fr3_joint3",
            "fr3_joint4",
            "fr3_joint5",
            "fr3_joint6",
            "fr3_joint7",
            "gripper",
        ],
    },
    "observation.images.cam1": {
        "dtype": "video",
        "shape": CAM_SHAPE,
        "names": ["height", "width", "channels"],
    },
    "observation.images.cam2": {
        "dtype": "video",
        "shape": CAM_SHAPE,
        "names": ["height", "width", "channels"],
    },
}


class StartBody(BaseModel):
    repo: str = Field(default=DEFAULT_REPO)
    task: str = Field(default=DEFAULT_TASK)


class RecorderState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.recording = False
        self.repo: str | None = None
        self.task: str = DEFAULT_TASK
        self.frames = 0
        self.episode_index: int | None = None
        self.last_error: str | None = None
        self.remote_sync: str | None = None
        self.saving = False
        self.codebase = CODEBASE_VERSION
        self.import_path = DATASET_IMPORT
        self._dataset = None
        self._thread: threading.Thread | None = None
        self._sync_thread: threading.Thread | None = None
        self._stop_loop = threading.Event()
        self._sidecar_states: list[np.ndarray] = []
        self._sidecar_actions: list[np.ndarray] = []
        self.bridge = RosBridge(
            franka_topic=ENV.get(
                "FRANKA_JOINT_TOPIC",
                "/franka_robot_state_broadcaster/measured_joint_states",
            ),
            gello_topic=ENV.get("GELLO_JOINT_TOPIC", "/gello/joint_states"),
            gripper_topic=ENV.get("GRIPPER_JOINT_TOPIC", "/franka_gripper/joint_states"),
            gello_gripper_topic=ENV.get(
                "GELLO_GRIPPER_TOPIC", "/gripper/gripper_client/target_gripper_width_percent"
            ),
            cam1_topic=ENV.get("CAM1_TOPIC", "/cam1/cam1/color/image_raw"),
            cam2_topic=ENV.get("CAM2_TOPIC", "/cam2/cam2/color/image_raw"),
        )

    def ensure_bridge(self) -> None:
        self.bridge.start()

    def status(self) -> dict[str, Any]:
        sample = self.bridge.get_latest()
        with self.lock:
            return {
                "recording": self.recording,
                "repo": self.repo,
                "task": self.task,
                "frames": self.frames,
                "episode_index": self.episode_index,
                "last_error": self.last_error,
                "remote_sync": self.remote_sync,
                "saving": self.saving,
                "codebase_version": self.codebase,
                "dataset_import": self.import_path,
                "fps": FPS,
                "local_root": str(LOCAL_ROOT),
                "streams": {
                    "ok": sample["ok"],
                    "ok_franka": sample["ok_franka"],
                    "ok_gello": sample["ok_gello"],
                    "ok_cam1": sample["ok_cam1"],
                    "ok_cam2": sample["ok_cam2"],
                    "age": sample["age"],
                },
            }


STATE = RecorderState()
app = FastAPI(title="LeRobot dual-camera episode recorder", version="1.1")


def _resume_local_for_recording(repo: str, root: Path):
    """Open an existing local v3 dataset for further recording without HuggingFace Hub."""
    from lerobot.datasets.lerobot_dataset import (
        LeRobotDatasetMetadata,
        get_safe_default_codec,
        resolve_vcodec,
    )
    from lerobot.datasets.utils import load_info, load_stats, load_tasks

    meta = LeRobotDatasetMetadata.__new__(LeRobotDatasetMetadata)
    meta.repo_id = repo
    meta.root = root
    meta.revision = CODEBASE_VERSION
    meta.writer = None
    meta.latest_episode = None
    meta.metadata_buffer = []
    meta.metadata_buffer_size = 1
    try:
        meta.load_metadata()
    except Exception:
        # Episode parquet may be missing if previous process exited without flush;
        # still resume from info.json counters.
        meta.info = load_info(root)
        try:
            meta.tasks = load_tasks(root)
        except Exception:
            meta.tasks = None
        meta.subtasks = None
        meta.episodes = None
        try:
            meta.stats = load_stats(root)
        except Exception:
            meta.stats = None

    obj = LeRobotDataset.__new__(LeRobotDataset)
    obj.meta = meta
    obj.repo_id = repo
    obj.root = root
    obj.revision = None
    obj.tolerance_s = 1e-4
    obj.image_writer = None
    obj.batch_encoding_size = 1
    obj.episodes_since_last_encoding = 0
    # LeRobot 0.4.x accepts "h264" (not ffmpeg's "libx264" name).
    obj.vcodec = resolve_vcodec("h264")
    obj._encoder_threads = None
    obj.episode_buffer = obj.create_episode_buffer()
    obj.episodes = None
    obj.hf_dataset = obj.create_hf_dataset()
    obj.image_transforms = None
    obj.delta_timestamps = None
    obj.delta_indices = None
    obj._absolute_to_relative_idx = None
    obj.video_backend = get_safe_default_codec()
    obj.writer = None
    obj.latest_episode = None
    obj._current_file_start_frame = None
    obj._lazy_loading = False
    obj._recorded_frames = int(meta.total_frames)
    obj._writer_closed_for_reading = False
    obj._streaming_encoder = None
    obj.start_image_writer(num_processes=0, num_threads=4)
    return obj


def _open_or_create_dataset(repo: str):
    root = LOCAL_ROOT / repo
    root.parent.mkdir(parents=True, exist_ok=True)
    if (
        STATE._dataset is not None
        and STATE.repo == repo
        and getattr(STATE._dataset, "root", None) is not None
    ):
        return STATE._dataset
    if (root / "meta" / "info.json").exists():
        return _resume_local_for_recording(repo, root)
    return LeRobotDataset.create(
        repo_id=repo,
        fps=FPS,
        features=FEATURES,
        root=root,
        robot_type="fr3",
        use_videos=True,
        image_writer_threads=4,
        batch_encoding_size=1,
        metadata_buffer_size=1,
        vcodec="h264",
    )


def _write_sidecar(repo: str, episode_index: int, states: list[np.ndarray], actions: list[np.ndarray]) -> Path:
    out_dir = LOCAL_ROOT / repo / "meta" / "sidecars"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"episode_{episode_index:06d}.npz"
    np.savez_compressed(
        path,
        observation_state=np.asarray(states, dtype=np.float32),
        action=np.asarray(actions, dtype=np.float32),
        fps=np.asarray([FPS], dtype=np.float32),
    )
    return path


def _maybe_update_cam_shapes(sample: dict[str, Any]) -> None:
    for key, img_key in (("observation.images.cam1", "cam1"), ("observation.images.cam2", "cam2")):
        img = sample.get(img_key)
        if img is None:
            continue
        h, w = int(img.shape[0]), int(img.shape[1])
        FEATURES[key]["shape"] = (h, w, 3)


def _record_loop() -> None:
    period = 1.0 / max(FPS, 1)
    skips = 0
    while not STATE._stop_loop.is_set():
        t0 = time.perf_counter()
        if not STATE.recording or STATE._dataset is None:
            time.sleep(0.05)
            continue
        sample = STATE.bridge.get_latest()
        if not sample["ok"]:
            skips += 1
            if skips % 30 == 1:
                STATE.last_error = f"stale streams ages={sample.get('age')}"
            time.sleep(0.01)
            continue
        skips = 0
        state_vec = np.concatenate([sample["franka_q"], [sample["gripper_obs"]]]).astype(np.float32)
        action_vec = np.concatenate([sample["gello_q"], [sample["gripper_act"]]]).astype(np.float32)
        frame = {
            "observation.state": state_vec,
            "action": action_vec,
            "observation.images.cam1": sample["cam1"],
            "observation.images.cam2": sample["cam2"],
            "task": STATE.task,
        }
        try:
            STATE._dataset.add_frame(frame)
            STATE._sidecar_states.append(state_vec.copy())
            STATE._sidecar_actions.append(action_vec.copy())
            with STATE.lock:
                STATE.frames += 1
                STATE.last_error = None
        except Exception as exc:  # noqa: BLE001
            STATE.last_error = f"add_frame: {exc}"
            traceback.print_exc()
        elapsed = time.perf_counter() - t0
        time.sleep(max(0.0, period - elapsed))


@app.on_event("startup")
def _startup() -> None:
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    STATE.ensure_bridge()
    STATE._stop_loop.clear()
    STATE._thread = threading.Thread(target=_record_loop, name="record-loop", daemon=True)
    STATE._thread.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    STATE._stop_loop.set()
    STATE.recording = False
    STATE.bridge.stop()


@app.get("/record/status")
def record_status() -> dict[str, Any]:
    return STATE.status()


@app.post("/record/start")
def record_start(body: StartBody) -> dict[str, Any]:
    repo = (body.repo or DEFAULT_REPO).strip()
    task = (body.task or DEFAULT_TASK).strip()
    with STATE.lock:
        if STATE.recording:
            raise HTTPException(status_code=409, detail="already recording")

    STATE.ensure_bridge()
    sample = None
    for _ in range(40):  # up to ~4s for streams after restart
        sample = STATE.bridge.get_latest()
        if sample["ok"]:
            break
        time.sleep(0.1)
    assert sample is not None
    _maybe_update_cam_shapes(sample)
    if not sample["ok"]:
        detail = (
            f"streams not ready ages={sample.get('age')} "
            f"flags={{'ok_franka': {sample['ok_franka']}, 'ok_gello': {sample['ok_gello']}, "
            f"'ok_cam1': {sample['ok_cam1']}, 'ok_cam2': {sample['ok_cam2']}}}"
        )
        STATE.last_error = detail
        raise HTTPException(status_code=500, detail=detail)

    with STATE.lock:
        if STATE.recording:
            raise HTTPException(status_code=409, detail="already recording")
        if STATE.saving:
            raise HTTPException(status_code=409, detail="still saving previous episode")
        try:
            STATE._dataset = _open_or_create_dataset(repo)
            STATE.repo = repo
            STATE.task = task
            STATE.frames = 0
            STATE._sidecar_states = []
            STATE._sidecar_actions = []
            STATE.remote_sync = None
            STATE.last_error = None
            epi = getattr(getattr(STATE._dataset, "meta", None), "total_episodes", None)
            STATE.episode_index = int(epi) if epi is not None else None
            STATE.recording = True
        except Exception as exc:  # noqa: BLE001
            STATE.last_error = str(exc)
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return STATE.status()


def _remote_sync_async(repo: str) -> None:
    """Upload in background so STOP returns after local save (not after slow tar|ssh)."""
    try:
        with STATE.lock:
            STATE.remote_sync = "syncing..."
        ok, msg = rsync_repo(LOCAL_ROOT / repo, repo, ENV)
        with STATE.lock:
            STATE.remote_sync = "ok" if ok else f"failed: {msg}"
            # Remote sync failure must not look like a recording/save failure.
            if STATE.last_error and str(STATE.last_error).startswith("failed:"):
                STATE.last_error = None
        print(f"remote sync {STATE.remote_sync}", flush=True)
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            STATE.remote_sync = f"failed: {exc}"
        print(f"remote sync exception: {exc}", flush=True)
        traceback.print_exc()
    finally:
        with STATE.lock:
            STATE.saving = False


@app.post("/record/stop")
def record_stop() -> dict[str, Any]:
    with STATE.lock:
        if STATE.saving:
            raise HTTPException(status_code=409, detail="still saving previous episode")
        if not STATE.recording:
            raise HTTPException(status_code=409, detail="not recording")
        STATE.recording = False
        STATE.saving = True
        dataset = STATE._dataset
        repo = STATE.repo
        frames = STATE.frames

    if dataset is None or not repo:
        with STATE.lock:
            STATE.saving = False
        raise HTTPException(status_code=500, detail="dataset missing")
    if frames <= 0:
        try:
            if hasattr(dataset, "clear_episode_buffer"):
                dataset.clear_episode_buffer()
        except Exception:  # noqa: BLE001
            pass
        with STATE.lock:
            STATE.last_error = "no frames captured; episode discarded"
            STATE.saving = False
            STATE._dataset = None
        return STATE.status()

    states = list(STATE._sidecar_states)
    actions = list(STATE._sidecar_actions)
    STATE._sidecar_states = []
    STATE._sidecar_actions = []

    try:
        dataset.save_episode()
        # Flush episode parquet metadata immediately so resume works after daemon restart.
        if hasattr(dataset, "meta") and hasattr(dataset.meta, "_flush_metadata_buffer"):
            dataset.meta._flush_metadata_buffer()
        # Close parquet writers so files get valid footers and remain readable for replay.
        if hasattr(dataset, "_close_writer"):
            dataset._close_writer()
        elif hasattr(dataset, "close"):
            dataset.close()
        epi = getattr(getattr(dataset, "meta", None), "total_episodes", None)
        with STATE.lock:
            STATE.episode_index = int(epi) - 1 if epi is not None and int(epi) > 0 else 0
            STATE.last_error = None
        try:
            sc = _write_sidecar(repo, int(STATE.episode_index), states, actions)
            print(f"wrote sidecar {sc} frames={len(states)}", flush=True)
        except Exception as sc_exc:  # noqa: BLE001
            print(f"sidecar write failed: {sc_exc}", flush=True)
        # Drop in-memory dataset handle so next start reopens writers cleanly.
        STATE._dataset = None
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            STATE.last_error = f"save_episode: {exc}"
            STATE.saving = False
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Local save done — return immediately; remote upload is best-effort background work.
    sync_thr = threading.Thread(target=_remote_sync_async, args=(repo,), daemon=True)
    with STATE.lock:
        STATE._sync_thread = sync_thr
        STATE.remote_sync = "syncing..."
    sync_thr.start()
    return STATE.status()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
