# LeRobot v3 Episode Recording Design

**Date:** 2026-07-14  
**Status:** Approved  
**Host:** teleop server `yao@10.229.20.125`  
**Remote archive:** `a25689@10.239.121.11:31126` → `~/lerobot_datasets/<repo>/`

## Goal

Record Franka teleop (robot state + GELLO action + RealSense color) into **Hugging Face LeRobot Dataset v3** format, with **per-episode start/stop** via HTTP API and CLI. After each successful `stop`, sync the local dataset to the remote archive host.

## Non-goals

- Does not modify robot shopFloor / network settings.
- Does not replace `start_teleop_all.sh` (recording remains decoupled; teleop must already be running).
- Does not push to Hugging Face Hub by default.
- Does not store SSH passwords in git.

## Decisions (locked)

| Topic | Choice |
|-------|--------|
| Interface | HTTP primary + CLI wrapper |
| Features | Separate Franka `observation.state` and GELLO `action` (+ gripper) |
| Camera | **2× RealSense via ROS nodes** (not librealsense direct), color @ **15 FPS**, `dtype=video` (MP4) |
| Local root | `/home/yao/lerobot_datasets/<repo>/` |
| Remote root | `~/lerobot_datasets/<repo>/` on `10.239.121.11:31126` |
| Sync | Local write first; **rsync after each episode stop** |
| Approach | Long-lived recorder daemon using official `LeRobotDataset.create` / `add_frame` / `save_episode` |

## Architecture

```
[ 2× realsense2_camera ROS nodes + Franka + GELLO ]
                 │
                 ▼
     lerobot_record_server.py  (daemon, conda env: lerobot)
                 │
     LeRobotDataset v3 (local /home/yao/lerobot_datasets/<repo>)
                 │  on POST /record/stop
                 ▼
     rsync → a25689@10.239.121.11:31126:~/lerobot_datasets/<repo>/
```

CLI `lerobot_episode.sh {start|stop|status}` calls the HTTP API.

## Dataset schema

FPS: **15**  
Robot type: `fr3`  
`use_videos=True`

| Feature key | dtype | shape | Source |
|-------------|-------|-------|--------|
| `observation.state` | float32 | (8,) | Franka joints[7] + gripper width |
| `action` | float32 | (8,) | GELLO joints[7] + gripper |
| `observation.images.cam1` | video | [480, 640, 3] | D435I `247122072824` → `/cam1/cam1/color/image_raw` |
| `observation.images.cam2` | video | [480, 640, 3] | D435 `141722071359` → `/cam2/cam2/color/image_raw` |
| (implicit) `timestamp`, `frame_index`, `episode_index`, `task` | — | — | LeRobot + start payload |

Joint order: `fr3_joint1` … `fr3_joint7` (sorted by name). Gripper: FR3 finger sum (meters) for obs; GELLO target width percent→0..1 for action.

Camera launch helper: `start_dual_realsense.sh` starts two `realsense2_camera` ROS nodes with distinct namespaces/serials. Recorder subscribes with BEST_EFFORT QoS only (no pyrealsense fallback).

## HTTP API

Default bind: `127.0.0.1:8765` (configurable via env `LEROBOT_RECORD_PORT`).

| Method | Path | Body | Behavior |
|--------|------|------|----------|
| `POST` | `/record/start` | `{ "repo": "fr3_gello_default", "task": "..." }` | Start buffering one episode (reject if already recording) |
| `POST` | `/record/stop` | `{}` | `save_episode`, flush video writers as needed, rsync to remote |
| `GET` | `/record/status` | — | `{recording, repo, episode_index, frames, last_error, remote_sync}` |

## CLI

```bash
bash /home/yao/gello_desk/lerobot_record/lerobot_episode.sh start [--repo NAME] [--task TEXT]
bash /home/yao/gello_desk/lerobot_record/lerobot_episode.sh stop
bash /home/yao/gello_desk/lerobot_record/lerobot_episode.sh status
```

Server lifecycle (separate from teleop):

```bash
bash /home/yao/gello_desk/lerobot_record/lerobot_record_daemon.sh start|stop|status
```

## Sync & credentials

- Env file (not in git): `/home/yao/gello_desk/lerobot_record/.env` or `~/.config/lerobot_record.env`
  - `REMOTE_SSH=a25689@10.239.121.11`
  - `REMOTE_PORT=31126`
  - `REMOTE_PATH=~/lerobot_datasets`
  - Prefer `SSH_KEY` over password; if password needed, `SSHPASS` via `sshpass` only on server-side config.
- After `stop`: `rsync -az -e "ssh -p 31126 ..." local/ remote:~/lerobot_datasets/<repo>/`
- Failure of rsync sets `remote_sync: failed` but keeps local dataset intact.

## Sampling loop

- Target 15 Hz wall clock.
- On each tick: latch latest Franka / GELLO / gripper / image; skip frame if any required stream stale beyond tolerance (e.g. 200 ms) and log warning.
- Images converted BGR→RGB if needed; ensure HWC uint8.

## File layout (code)

```
gello_desk/lerobot_record/
  README.md
  env.example
  record_server.py          # FastAPI/Flask HTTP + recording loop
  ros_bridge.py             # ROS2 topic subscribers (or rclpy node thread)
  sync_remote.py            # rsync helper
  lerobot_episode.sh        # CLI
  lerobot_record_daemon.sh  # start/stop daemon
```

## Success criteria

1. `start` → status shows `recording: true` and frame count increases.
2. `stop` → new episode appears under local LeRobot v3 layout (`meta/`, `data/`, `videos/`).
3. After stop, remote `~/lerobot_datasets/<repo>/` contains the same episode (rsync OK).
4. Camera feature readable as video via LeRobot tools; state/action lengths 8.
5. Second `start`/`stop` appends another episode without corrupting the dataset.
6. Password / secrets never committed.

## Open defaults (if unset)

- Default repo: `fr3_gello_teleop`
- Default task: `franka gello teleop`
- HTTP: `127.0.0.1:8765`
