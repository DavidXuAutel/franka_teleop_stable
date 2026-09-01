# LeRobot v3 Episode Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a long-lived recorder on the teleop server that writes LeRobot Dataset v3 episodes (Franka + GELLO + RealSense @ 15 FPS) with HTTP/CLI start-stop, and rsyncs each finished episode to `a25689@10.239.121.11:31126:~/lerobot_datasets/<repo>/`.

**Architecture:** ROS2 subscribers feed a 15 Hz loop into official `LeRobotDataset.create` / `add_frame` / `save_episode`. FastAPI serves `/record/start|stop|status`. CLI curls the API. Deploy under `/home/yao/gello_desk/lerobot_record/` (sync from this repo's `gello_desk/lerobot_record/`).

**Tech Stack:** Python 3 + conda env `lerobot`, ROS2 Humble (`rclpy`), FastAPI/uvicorn, OpenCV, `sshpass`/`rsync`, LeRobot `LeRobotDataset`.

**Spec:** `docs/superpowers/specs/2026-07-14-lerobot-v3-episode-recording-design.md`

---

## File map

| File | Responsibility |
|------|----------------|
| `gello_desk/lerobot_record/ros_bridge.py` | Threaded ROS2 subscriptions; latest joint/image latch |
| `gello_desk/lerobot_record/sync_remote.py` | Load env; rsync local repo to remote |
| `gello_desk/lerobot_record/record_server.py` | Dataset lifecycle + 15 Hz loop + FastAPI |
| `gello_desk/lerobot_record/lerobot_episode.sh` | CLI → HTTP |
| `gello_desk/lerobot_record/lerobot_record_daemon.sh` | Daemon start/stop/status + logs |
| `gello_desk/lerobot_record/env.example` | Credential/path template |
| `gello_desk/lerobot_record/README.md` | Usage |

Deploy target on teleop: `/home/yao/gello_desk/lerobot_record/`  
Runtime dataset: `/home/yao/lerobot_datasets/<repo>/`  
Conda: `source ~/anaconda3/etc/profile.d/conda.sh && conda activate lerobot`

---

### Task 1: Scaffold package + env template

- [ ] Create `gello_desk/lerobot_record/` with `env.example`, `README.md` skeleton
- [ ] `env.example` keys: `LEROBOT_RECORD_HOST`, `LEROBOT_RECORD_PORT`, `LOCAL_DATASET_ROOT`, `REMOTE_SSH`, `REMOTE_PORT`, `REMOTE_PATH`, `SSHPASS` (optional), `SSH_KEY` (optional), `FPS=15`
- [ ] Document: copy to `/home/yao/gello_desk/lerobot_record/.env` (gitignored)

### Task 2: `ros_bridge.py` — latch ROS topics

- [ ] Subscribe:
  - `/franka/joint_states` (or fallback `/joint_states` if namespaced)
  - `/gello/joint_states`
  - gripper: prefer `franka_gripper/...` joint/position if present; else 0.0 placeholder logged once
  - `/camera/camera/color/image_raw` via `cv_bridge` or manual numpy from `sensor_msgs/Image`
- [ ] Provide `get_latest()` returning dict with `franka_q(7)`, `gello_q(7)`, `gripper_obs`, `gripper_act`, `image_rgb`, `stamps`, `ok` flags
- [ ] Sort Franka/GELLO joints by `fr3_joint1..7` name order

### Task 3: `sync_remote.py`

- [ ] `load_env(path)` 
- [ ] `rsync_repo(local_dir, repo_name) -> (ok, message)`
- [ ] Ensure remote `~/lerobot_datasets` exists via `ssh mkdir -p`
- [ ] Use `sshpass` only if `SSHPASS` set and no key

### Task 4: `record_server.py` — LeRobot + HTTP

- [ ] Features dict matching design (state/action float32 (8,), image video)
- [ ] On first `start`: `LeRobotDataset.create(...)` or resume existing root if repo already exists (use LeRobot API for open/append if available; else create once and reuse process-local handle)
- [ ] Recording loop thread at 15 FPS while `recording`
- [ ] `add_frame({observation.state, action, observation.images.front, task})`
- [ ] `stop` → `save_episode()` then `sync_remote.rsync_repo`
- [ ] FastAPI routes; reject double-start; idle without recording does not write frames
- [ ] Bind `127.0.0.1:$PORT`

### Task 5: Shell wrappers

- [ ] `lerobot_record_daemon.sh start|stop|status` — activate conda+ROS, nohup uvicorn/module, pid/log under `/home/yao/gello_logs/`
- [ ] `lerobot_episode.sh start|stop|status` — curl API

### Task 6: Deploy to teleop server + configure `.env`

- [ ] `scp`/`rsync` package to `/home/yao/gello_desk/lerobot_record/`
- [ ] Write `.env` with remote credentials (server-only, not git)
- [ ] `pip`/conda ensure `fastapi uvicorn opencv` if missing in `lerobot` env
- [ ] `ssh mkdir -p ~/lerobot_datasets` on remote archive host

### Task 7: Integration smoke test

- [ ] Confirm teleop stack running (or start minimal camera+topics if needed)
- [ ] Daemon start → `status` idle
- [ ] `episode start` → frame count rises
- [ ] `episode stop` → local `meta/info.json` has `codebase_version` v3.x (or current LeRobot create version) + video file; remote directory updated
- [ ] Second episode start/stop succeeds
- [ ] Record results in README “Verified” section

### Task 8: Sync local git tree (no commit unless user asks)

- [ ] Ensure local `franka_teleop_stable` mirrors deployed scripts
- [ ] Do **not** commit secrets; do **not** `git commit` unless user requests

---

## Test plan (manual)

1. Teleop up: FCI Active, `/franka/joint_states` + `/gello/joint_states` + camera publishing.  
2. `bash lerobot_record_daemon.sh start`  
3. `bash lerobot_episode.sh start --repo fr3_gello_teleop --task "pick test"`  
4. Move GELLO ~10s; `status` shows frames > 0  
5. `bash lerobot_episode.sh stop`  
6. Check `/home/yao/lerobot_datasets/fr3_gello_teleop/{meta,data,videos}`  
7. `ssh` remote: `ls ~/lerobot_datasets/fr3_gello_teleop`  

## Risks

- LeRobot installed version may be v2.1 API vs v3 — verify `LeRobotDataset.create` / `finalize` against conda env; adapt import paths.  
- Generic kernel FCI drops — recording should tolerate short gaps (skip stale frames).  
- Password auth: prefer deploying an SSH key to the archive host later.
