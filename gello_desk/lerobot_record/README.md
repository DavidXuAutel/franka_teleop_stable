# LeRobot v3 episode recorder (Franka + GELLO + 2× RealSense ROS)

完整运维说明（环境、启动顺序、GUI、排障）：见仓库  
[`docs/teleop_lerobot_launcher.md`](../../docs/teleop_lerobot_launcher.md)  
（遥操机副本：`/home/yao/gello_desk/lerobot_record/USAGE.md`）。

## One-shot launcher (recommended)

Starts **Desk/FCI + GELLO teleop + MuJoCo + dual cameras + live image viewer + record daemon**:

```bash
bash /home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh start
# or
bash /home/yao/start_teleop_lerobot_all.sh start
```

```bash
# record one episode
bash /home/yao/start_teleop_lerobot_all.sh episode-start --repo fr3_gello_teleop --task "demo"
bash /home/yao/start_teleop_lerobot_all.sh episode-stop

bash /home/yao/start_teleop_lerobot_all.sh status
bash /home/yao/start_teleop_lerobot_all.sh stop
```

Live view window title: `LeRobot Record | cam1 + cam2`
- 绿色 **START** / 红色 **STOP**（结束本段录制）/ 橙色 **REPLAY** / 深紫 **ABORT**（终止整机遥操栈并关窗）
- **REPLAY** 会新开 **视频回放窗 + 独立 MuJoCo 仿真回放**，**不关闭**采集窗口
- 视频用 PyAV 解码（兼容已有 AV1 / 新录制 H.264）
- 关节轨迹来自 `meta/sidecars/episode_XXXXXX.npz`（新 episode 停止时写入）
- 快捷键：`s` 开始，`e` 结束录制，`p` 回放，`x` 终止整机，`q` 仅关闭采集预览
- 状态行显示 REC/IDLE、帧数，以及本地保存路径

### 数据存放地址

| 位置 | 路径 |
|------|------|
| 本地（主） | `/home/yao/lerobot_datasets/<repo>/` |
| 视频 | `.../videos/observation.images.cam1|cam2/.../*.mp4` |
| 状态/动作 | `.../data/chunk-***/file-***.parquet` |
| 回放关节 sidecar | `.../meta/sidecars/episode_XXXXXX.npz` |
| 元数据 | `.../meta/info.json` |
| 远程同步 | `a25689@10.239.121.11:31126:~/lerobot_datasets/<repo>/` |

默认 repo：`fr3_gello_teleop`

单独回放：

```bash
python3 /home/yao/gello_desk/lerobot_record/cam_replay.py \
  --root /home/yao/lerobot_datasets/fr3_gello_teleop --loop

# 仅 MuJoCo（需已有 sidecar）
python3 /home/yao/gello_desk/lerobot_record/mujoco_replay.py \
  --root /home/yao/lerobot_datasets/fr3_gello_teleop --loop
```

回放窗口快捷键：`space` 暂停，`a`/`d` 快退/快进，`r` 重播；**关闭**：`q` / `Esc` / 窗口 X / 橙色 **CLOSE** 按钮（同时结束 MuJoCo 回放）。
**已有旧数据**：视频可回放；关节需重新录一段 episode 后才有 sidecar。

## 遥操报错自动恢复

随 `start_teleop_lerobot_all.sh` 启动：

```bash
python3 /home/yao/gello_desk/teleop_error_watchdog.py --desk-host 10.229.66.91
```

检测到 `REFLEX` / `USER_STOPPED` / `current_errors` 后自动：
1. 调用 `/action_server/error_recovery`
2. 仍为 User Stop 时走 Desk `safety/recovery`
3. 必要时重新 `activate joint_impedance_controller`

日志：`/home/yao/gello_logs/teleop_error_watchdog.log`

## Cameras (ROS only)

| Key | Device | Serial | Topic |
|-----|--------|--------|-------|
| `observation.images.cam1` | D435I | `247122072824` | `/cam1/cam1/color/image_raw` |
| `observation.images.cam2` | D435 | `141722071359` | `/cam2/cam2/color/image_raw` |

## HTTP

- `POST /record/start` `{"repo":"...","task":"..."}`
- `POST /record/stop`
- `GET /record/status`

Default: `http://127.0.0.1:8765`

## Paths

- Local: `/home/yao/lerobot_datasets/<repo>/`
- Remote: `a25689@10.239.121.11:31126:~/lerobot_datasets/<repo>/`
