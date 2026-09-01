# Franka GELLO 遥操 + LeRobot 一体化启动指南

一键拉起 Desk/FCI、GELLO 遥操、MuJoCo 镜像、双路 RealSense、录制预览 GUI、LeRobot 录制守护进程与错误看门狗。

组件细节见 `gello_desk/lerobot_record/README.md`（机上副本同目录 `README.md`）。  
遥操 / 相机 / 录制 HTTP 接口见 [`docs/franka_teleop_camera_api.md`](./franka_teleop_camera_api.md)。  
远程归档路径见 [`docs/lerobot_remote_archive_paths.md`](./lerobot_remote_archive_paths.md)。

---

## 环境

| 项 | 值 |
|----|-----|
| 遥操机 | `yao@10.229.20.125` |
| 机器人 | `10.229.66.91`（Desk：`franka` / `franka123`） |
| 主脚本 | `/home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh` |
| 快捷入口 | `/home/yao/start_teleop_lerobot_all.sh`（若存在 symlink） |
| `DISPLAY` | 默认 `:1`（MuJoCo / OpenCV 预览窗） |
| 日志目录 | `/home/yao/gello_logs` |

**网络约束**

- FR3 **无无线**；机器人操作必须经本机 **有线直连**。
- **禁止**通过远端服务器 `10.229.66.70` 或 Desk API 修改机器人侧 shopFloor / robot network。

可选环境变量（脚本内默认）：

```bash
ROBOT_IP=10.229.66.91
DISPLAY=:1
LOG_DIR=/home/yao/gello_logs
DESK_PASSWORD=franka123
```

录制相关可在 `gello_desk/lerobot_record/.env` 覆盖（参考 `env.example`）。

---

## 命令

在遥操机上执行：

```bash
# 启动整栈
bash /home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh start

# 停止 / 状态 / 重启
bash /home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh stop
bash /home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh status
bash /home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh restart

# 录制一集（也可在 GUI 点 START/STOP）
bash /home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh episode-start \
  --repo fr3_gello_teleop --task "demo"
bash /home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh episode-stop
bash /home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh episode-status
```

有 symlink 时可简写：

```bash
bash /home/yao/start_teleop_lerobot_all.sh start
```

---

## `start` 拉起顺序

| 步骤 | 内容 |
|------|------|
| 1 | Desk/FCI 预处理（`desk_prep.py --recover`：解锁 + 激活 FCI） |
| 2 | GELLO 遥操栈（`gello_launch.sh`：publisher → arm → gripper） |
| 3 | MuJoCo GPU 镜像（`start_mujoco_gpu.sh`） |
| 4 | 双路 RealSense ROS（cam1 D435I + cam2 D435） |
| 5 | `cam_view_dual` 预览窗（START / STOP / REPLAY / ABORT） |
| 6 | LeRobot record daemon（HTTP `127.0.0.1:8765`） |
| 7 | 遥操错误看门狗（`teleop_error_watchdog.py`，自动 Reflex / UserStop 恢复） |

`stop` 大致按相反顺序清理：录制 daemon → 预览 → 相机 → MuJoCo/回放 → 看门狗 → GELLO 遥操。

启动前检查：GELLO USB（`/dev/serial/by-id/usb-FTDI_*`）必须存在，否则 `start` 直接退出。

---

## GUI 控制（`cam_view_dual`）

窗口标题：`LeRobot Record | cam1 + cam2`

| 按钮 | 作用 |
|------|------|
| **START**（绿） | 开始本集录制 |
| **STOP**（红） | 结束本集并触发远程同步 |
| **REPLAY**（橙） | 新开 **视频回放 + 独立 MuJoCo**；**不关闭**采集窗 |
| **ABORT**（深紫） | 终止整机遥操栈并关闭预览窗 |

快捷键：

| 键 | 作用 |
|----|------|
| `s` | START |
| `e` | STOP（结束录制） |
| `p` | REPLAY |
| `x` | ABORT（停整栈） |
| `q` / `Esc` | 仅关预览窗（不停遥操/录制 daemon） |

状态行显示 REC/IDLE、帧数、本地保存路径。

**REPLAY 说明**

- 视频：PyAV 解码（兼容旧 AV1 / 新 H.264）
- 关节：来自 `meta/sidecars/episode_XXXXXX.npz`（新 episode 停止时写入）
- 需可用 `DISPLAY`，并使用带 **GUI OpenCV** 的 Python（默认优先 `/usr/bin/python3` + `av`；可用 `LEROBOT_REPLAY_PYTHON` 覆盖）

单独回放：

```bash
python3 /home/yao/gello_desk/lerobot_record/cam_replay.py \
  --root /home/yao/lerobot_datasets/fr3_gello_teleop --loop

python3 /home/yao/gello_desk/lerobot_record/mujoco_replay.py \
  --root /home/yao/lerobot_datasets/fr3_gello_teleop --loop
```

回放窗：`space` 暂停，`a`/`d` 快退/快进，`r` 重播；`q` / `Esc` / **CLOSE** 结束（同时停 MuJoCo 回放）。

---

## 数据路径

| 位置 | 路径 |
|------|------|
| 本地根 | `/home/yao/lerobot_datasets/<repo>/` |
| 视频 | `.../videos/observation.images.cam1\|cam2/.../*.mp4` |
| 状态/动作 | `.../data/chunk-***/file-***.parquet` |
| 关节 sidecar | `.../meta/sidecars/episode_XXXXXX.npz` |
| 元数据 | `.../meta/info.json` |
| 远程同步 | `a25689@10.239.121.11:31126:~/lerobot_datasets/<repo>/` |

默认 repo：`fr3_gello_teleop`。`episode-stop` 后自动 rsync 到远程。

详细远程结构见 [lerobot_remote_archive_paths.md](./lerobot_remote_archive_paths.md)。

---

## 摄像头（ROS）

| Key | 设备 | Serial | Topic |
|-----|------|--------|-------|
| `observation.images.cam1` | D435I | `247122072824` | `/cam1/cam1/color/image_raw` |
| `observation.images.cam2` | D435 | `141722071359` | `/cam2/cam2/color/image_raw` |

录制 HTTP（daemon）：

- `POST /record/start` — `{"repo":"...","task":"..."}`
- `POST /record/stop`
- `GET /record/status`
- 默认：`http://127.0.0.1:8765`

---

## 前置条件 / 排障（简要）

**Desk 控制权**

- Desk 不能长期占用 control token（日志里常见 `owner=franka`）。
- 在 Desk 上 **释放** 或 **接受** `gello-teleop` 请求，再跑 `start`。

**FCI**

- 机械臂运动需要 **FCI Active**。`desk_prep` 会尝试激活；可用 `status` 查看 FCI JSON。

**卡在 GELLO / ROS2**

- `ros2 control list_controllers` / `ros2 topic list` 可能挂死（ros2cli/DDS 卡死）。
- 脚本侧已对 `list_controllers` / `topic list` 加 **timeout**。
- 仍卡住时：杀掉卡住的 `list_controllers` 进程，或 `ros2 daemon stop && ros2 daemon start`，再 `restart`。

**MuJoCo / 预览窗看不到**

- 确认桌面已登录到 `DISPLAY=:1`（或导出实际 DISPLAY）。
- 日志：`/home/yao/gello_logs/mujoco_*.log`、`cam_view_dual.log`。

**REPLAY 无窗 / OpenCV 报错**

- 需要 `DISPLAY` + 带 GUI 的 OpenCV；系统已固定优先 `/usr/bin/python3`（含 `av`）。
- 可用：`export LEROBOT_REPLAY_PYTHON=/usr/bin/python3`

**GELLO USB 找不到**

```bash
ls -la /dev/serial/by-id/
# 期望：usb-FTDI_*
```

**看门狗**

- 进程：`teleop_error_watchdog.py`
- 日志：`/home/yao/gello_logs/teleop_error_watchdog.log`
- 自动处理 `REFLEX` / `USER_STOPPED`：error_recovery → Desk safety/recovery → 必要时重激活 `joint_impedance_controller`

**常用状态检查**

```bash
bash /home/yao/gello_desk/lerobot_record/start_teleop_lerobot_all.sh status
```

会打印：负载、FCI、关键进程、controllers（带超时）、相机 topic、recorder HTTP 状态。

---

## 相关文件

| 路径 | 说明 |
|------|------|
| `gello_desk/lerobot_record/start_teleop_lerobot_all.sh` | 一体化启动入口 |
| `gello_desk/lerobot_record/lerobot_episode.sh` | episode CLI |
| `gello_desk/lerobot_record/lerobot_record_daemon.sh` | 录制 HTTP daemon |
| `gello_desk/lerobot_record/cam_view_dual.py` | 双相机预览 + 录制按钮 |
| `gello_desk/lerobot_record/start_dual_realsense.sh` | 双 RealSense |
| `gello_desk/teleop_error_watchdog.py` | 遥操错误自动恢复 |
| `gello_desk/desk_prep.py` | Desk/FCI 准备 |
| `/home/yao/gello_launch.sh` | GELLO 遥操栈（机上） |
