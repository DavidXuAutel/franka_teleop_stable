# Franka 遥操机（10.229.20.125）接口文档

遥操 PC `yao@10.229.20.125`（有线 `eno1` / `10.229.20.125`）上对外可用的 **Franka 遥操接口** 与 **双路相机接口** 汇总。  
机器人 Desk / FCI 地址：`10.229.66.91`（**禁止**经 Desk API 或 `10.229.66.70` 改机器人侧 shopFloor / robot network）。

| 项 | 值 |
|----|-----|
| 遥操机 | `10.229.20.125` |
| 机器人 / Desk | `10.229.66.91` |
| Desk 账号 | `franka` / `franka123`（环境变量 `DESK_PASSWORD`） |
| 录制 HTTP | `http://127.0.0.1:8765`（默认仅本机回环） |
| 数据集根 | `/home/yao/lerobot_datasets` |
| 日志 | `/home/yao/gello_logs` |
| 代码目录 | `/home/yao/gello_desk/`、`/home/yao/gello_desk/lerobot_record/` |

---

## 1. 拓扑与端口

```text
GELLO USB ──► gello_publisher ──► /gello/joint_states
                                      │
                                      ▼
                              joint_impedance_controller
                                      │ FCI :1337
                                      ▼
                              Franka FR3 (10.229.66.91)
                                      │
                                      ├── /franka_robot_state_broadcaster/*
                                      └── /franka_gripper/*  (:1338 Hand)

RealSense ×2 ──► ROS nodes ──► /cam1/... /cam2/...
                                      │
                                      ▼
                         record_server (:8765) + cam_view_dual
```

| 端口 / URL | 位置 | 用途 |
|------------|------|------|
| **TCP 8765** | 遥操机 `127.0.0.1` | LeRobot episode 录制 HTTP API |
| **TCP 1337** | 机器人 | 机械臂 FCI（libfranka）。**控制占用后禁止再裸探测**，以免打断会话 |
| **TCP 1338** | 机器人 | Franka Hand 夹爪 |
| **HTTPS 443** | 机器人 Desk | Desk REST（解锁 / FCI / safety recovery） |
| **SSH 31126** | 归档机 `a25689@10.239.121.11` | episode 停止后异步 `tar\|ssh` 归档（可选） |

OpenAPI（录制服务启动后）：`http://127.0.0.1:8765/docs`、`/redoc`、`/openapi.json`。

---

## 2. 录制 HTTP API（`:8765`）

实现：`gello_desk/lerobot_record/record_server.py`  
绑定：`LEROBOT_RECORD_HOST`（默认 `127.0.0.1`）+ `LEROBOT_RECORD_PORT`（默认 `8765`）。

CLI 客户端：

```bash
bash /home/yao/gello_desk/lerobot_record/lerobot_episode.sh status
bash /home/yao/gello_desk/lerobot_record/lerobot_episode.sh start --repo fr3_gello_teleop --task "demo"
bash /home/yao/gello_desk/lerobot_record/lerobot_episode.sh stop
```

GUI：`cam_view_dual.py --api http://127.0.0.1:8765`（START / STOP 按钮）。

### 2.1 `GET /record/status`

无请求体。返回当前录制状态与四路流健康度。

**响应字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `recording` | bool | 是否正在采帧 |
| `repo` | string\|null | 数据集 repo id |
| `task` | string | 任务描述 |
| `frames` | int | 当前 episode 已写帧数 |
| `episode_index` | int\|null | 开始时为 `meta.total_episodes`；停止保存后为刚写入的索引 |
| `last_error` | string\|null | 本地录制/保存错误（远端同步失败**不**写入此字段） |
| `remote_sync` | string\|null | `null` \| `"syncing..."` \| `"ok"` \| `"failed: ..."` |
| `saving` | bool | 本地已停录、仍在保存或后台同步 |
| `codebase_version` | string | LeRobot 版本（如 `v3.0`） |
| `dataset_import` | string | `"lerobot.datasets"` |
| `fps` | int | 默认 `15` |
| `local_root` | string | 如 `/home/yao/lerobot_datasets` |
| `streams` | object | 见下表 |

**`streams`**

| 字段 | 说明 |
|------|------|
| `ok` | franka + gello + cam1 + cam2 均新鲜 |
| `ok_franka` / `ok_gello` / `ok_cam1` / `ok_cam2` | 单路是否新鲜 |
| `age` | `{franka,gello,cam1,cam2}` 距上次更新秒数；未收到为 `null` |

新鲜度阈值（`ros_bridge.py`）：关节 `0.35s`，图像 `0.75s`。

**示例**

```bash
curl -sS http://127.0.0.1:8765/record/status | python3 -m json.tool
```

### 2.2 `POST /record/start`

开始一个 episode。请求 JSON：

```json
{
  "repo": "fr3_gello_teleop",
  "task": "franka gello teleop"
}
```

| 字段 | 默认 |
|------|------|
| `repo` | `DEFAULT_REPO` → `fr3_gello_teleop` |
| `task` | `DEFAULT_TASK` → `franka gello teleop` |

成功：返回与 status 相同结构，`recording=true`，`frames=0`。

| HTTP | `detail` |
|------|----------|
| 409 | `already recording` |
| 409 | `still saving previous episode` |
| 500 | `streams not ready ages=... flags={...}` |
| 500 | 数据集打开/创建失败信息 |

开始前约等待最多 4s 四路流就绪。

```bash
curl -sS -X POST http://127.0.0.1:8765/record/start \
  -H 'Content-Type: application/json' \
  -d '{"repo":"fr3_gello_teleop","task":"demo"}'
```

### 2.3 `POST /record/stop`

无请求体。流程：

1. `recording=false`，本地 `save_episode()` + sidecar  
2. **立即**返回 status（常带 `remote_sync="syncing..."`，`saving=true`）  
3. 后台 `tar|ssh` 归档；失败只反映在 `remote_sync`，不当作录制失败  

| HTTP | `detail` |
|------|----------|
| 409 | `not recording` / `still saving previous episode` |
| 500 | `dataset missing` / 保存异常 |

`frames<=0`：丢弃缓冲，`last_error="no frames captured; episode discarded"`，不归档。

```bash
curl -sS -X POST http://127.0.0.1:8765/record/stop
```

### 2.4 写入数据 schema（录制内容）

| Key | dtype / shape | 含义 |
|-----|---------------|------|
| `observation.state` | float32 `[8]` | 机器人关节 + 夹爪宽度（m） |
| `action` | float32 `[8]` | GELLO 目标关节 + 夹爪指令（百分制归一） |
| `observation.images.cam1` | video `H×W×3` | 默认约 `480×640` |
| `observation.images.cam2` | video `H×W×3` | 同上 |

关节名：`fr3_joint1` … `fr3_joint7`，`gripper`。

本地路径：

| 内容 | 路径 |
|------|------|
| 数据集 | `/home/yao/lerobot_datasets/<repo>/` |
| 关节 sidecar | `.../meta/sidecars/episode_XXXXXX.npz`（键：`observation_state` / `action` / `fps`） |
| 视频 | `.../videos/observation.images.cam{1,2}/chunk-***/file-***.mp4` |

---

## 3. 遥操 ROS 接口（Franka / GELLO / 夹爪）

### 3.1 录制桥订阅的话题（`ros_bridge`）

| 角色 | 环境变量 | **实机 / 代码默认** | 消息类型 |
|------|----------|---------------------|----------|
| 臂观测 | `FRANKA_JOINT_TOPIC` | `/franka_robot_state_broadcaster/measured_joint_states` | `sensor_msgs/JointState` |
| GELLO 指令源 | `GELLO_JOINT_TOPIC` | `/gello/joint_states` | `sensor_msgs/JointState` |
| 夹爪观测 | `GRIPPER_JOINT_TOPIC` | `/franka_gripper/joint_states` | `sensor_msgs/JointState`（两指位置求和 → 宽度） |
| 夹爪指令 | `GELLO_GRIPPER_TOPIC` | `/gripper/gripper_client/target_gripper_width_percent` | `std_msgs/Float32`（`>1.5` 时按百分数 ÷100） |

> **注意：** `env.example` 里曾写 `FRANKA_JOINT_TOPIC=/franka/joint_states`，与当前栈不一致。以 `record_server.py` 默认及实机 broadcaster 话题为准。

### 3.2 状态 / 健康（看护进程）

| 话题 | 消息 | 用途 |
|------|------|------|
| `/franka_robot_state_broadcaster/robot_state` | `franka_msgs/FrankaRobotState` | `robot_mode`、`current_errors`、`control_command_success_rate`；异常看护 `teleop_error_watchdog.py` |

常用相关话题（遥操栈也会发布）：

| 话题 | 说明 |
|------|------|
| `/franka/joint_states` | `joint_state_broadcaster` 重映射名空间下关节 |
| `/franka_robot_state_broadcaster/current_pose` 等 | Franka 便捷状态话题族 |
| `/dynamic_joint_states` | ros2_control 动态关节 |

### 3.3 控制器

| 名称 | 类型 | 期望状态 |
|------|------|----------|
| `joint_impedance_controller` | `franka_fr3_arm_controllers/JointImpedanceController` | teleop 时 `active` |
| `franka_robot_state_broadcaster` | Franka state broadcaster | `active` |
| `joint_state_broadcaster` | JointStateBroadcaster | `active` |

查询：

```bash
ros2 control list_controllers
```

### 3.4 夹爪 action / 客户端

| 接口 | 默认名 |
|------|--------|
| Move action | `/franka_gripper/move` |
| Homing action | `/franka_gripper/homing`（实机启动常用 `skip_homing:=true`） |
| 宽度指令话题 | `/gripper/gripper_client/target_gripper_width_percent` |

实现：`franka_gripper_client`（阻抗控制期间 Move 会短暂停臂，避免 FCI 断连）。

### 3.5 MuJoCo 镜像订阅

| `--source` | 关节 | 夹爪 |
|------------|------|------|
| `franka` | `/franka_robot_state_broadcaster/measured_joint_states` | `/franka_gripper/joint_states` |
| `gello` | `/gello/joint_states` | `/gripper/gripper_client/target_gripper_width_percent` |

---

## 4. 相机 ROS 接口（双路 RealSense）

启动脚本：`/home/yao/gello_desk/lerobot_record/start_dual_realsense.sh`  
（125 实机默认 serial 如下；可用环境变量覆盖。）

| 键 | 设备角色 | Serial（125 默认） | namespace / name | 彩色图话题 |
|----|----------|--------------------|-----------------|------------|
| cam1 | RealSense #1 | `141722071359` | `cam1` / `cam1` | `/cam1/cam1/color/image_raw` |
| cam2 | RealSense #2 | `247122072824` | `cam2` / `cam2` | `/cam2/cam2/color/image_raw` |

环境变量：`CAM1_SERIAL`、`CAM2_SERIAL`、`CAM1_TOPIC`、`CAM2_TOPIC`。

Launch 参数：`enable_color:=true`，**关闭** depth / infra。

消息类型：`sensor_msgs/Image`（常见 `rgb8` / `bgr8`）。录制桥统一转 RGB。

运维：

```bash
bash /home/yao/gello_desk/lerobot_record/start_dual_realsense.sh start|stop|status
ros2 topic list | grep 'cam[12].*color/image_raw'
# 勿在 FCI 占用期滥用阻塞型探测；status 内 hz 仅人工排查用
```

预览 GUI：

```bash
python3 /home/yao/gello_desk/lerobot_record/cam_view_dual.py \
  --cam1 /cam1/cam1/color/image_raw \
  --cam2 /cam2/cam2/color/image_raw \
  --api http://127.0.0.1:8765
```

---

## 5. Desk REST API（经 125 调机器人）

实现：`gello_desk/desk_prep.py`  
Base：`https://10.229.66.91`（TLS，代码侧可不校验证书）  
认证：HTTP Basic；控制类请求带 `X-Control-Token`。

**本栈实际调用：**

| Method | Path | 控制令牌 | 作用 |
|--------|------|----------|------|
| GET | `/api/system` | 否 | 系统 / 运行模式 |
| GET | `/api/fci` | 否 | FCI 是否 `Active` |
| GET | `/api/system/control-token` | 否 | 当前 owner |
| POST | `/api/system/control-token:take` | 否 | body `{"owner":"gello-teleop","timeout":8.0}` |
| POST | `/api/system/control-token:release` | 是 | 释放控制 |
| POST | `/api/arm/joints:unlock` | 是 | 解锁关节 |
| POST | `/api/fci:activate` | 是 | 激活 FCI |
| GET | `/api/safety/recovery` | 是 | 查询 recovery |
| POST | `/api/safety/recovery:start` | 是 | `--recover` |
| POST | `/api/safety/recovery:confirm` | 是 | `--recover` |

```bash
python3 /home/yao/gello_desk/desk_prep.py --host 10.229.66.91 --recover
python3 /home/yao/gello_desk/desk_prep.py --host 10.229.66.91 --release
```

**明确不在此文档作为常规遥操接口：** 修改 Desk shopFloor / robot network、经 `10.229.66.70` 操作机器人网络。

---

## 6. 运维入口（封装上述接口）

| 脚本 | 作用 |
|------|------|
| `clean_start_teleop_lerobot.sh` | 清残留 → 全量启动遥操+数采 |
| `lerobot_record/start_teleop_lerobot_all.sh` | `start\|stop\|status\|restart\|episode-*` |
| `restart_teleop.sh` | 停遥操 → 链路预检 → desk_prep → 拉起 → 健康检查 |
| `recover_arm_stack.sh` | 异常看护触发的全量恢复（含补齐相机/录制/viewer） |
| `link_preflight.sh` | 有线路由 +（启动前）`:1337` + RTT |
| `teleop_error_watchdog.py` | 异常持续 ≥10s → 调用 `recover_arm_stack.sh` |
| `lerobot_record_daemon.sh` | 单独起停 `record_server` |

一键：

```bash
bash /home/yao/gello_desk/clean_start_teleop_lerobot.sh
# 或
bash /home/yao/clean_start_teleop_lerobot.sh
```

---

## 7. 快速联调清单

```bash
# 录制 API
curl -sS http://127.0.0.1:8765/record/status

# 遥操关节
timeout 3 ros2 topic hz /franka_robot_state_broadcaster/measured_joint_states
timeout 3 ros2 topic hz /gello/joint_states

# 相机
timeout 3 ros2 topic hz /cam1/cam1/color/image_raw
timeout 3 ros2 topic hz /cam2/cam2/color/image_raw

# 控制器
ros2 control list_controllers

# Desk FCI（HTTP，控制存活期可用；勿再 nc :1337）
curl -sk -u franka:franka123 https://10.229.66.91/api/fci
```

四路流健康时，`/record/status` 中应有 `"streams":{"ok":true,...}`。

---

## 8. 相关源码

| 文件 | 内容 |
|------|------|
| `gello_desk/lerobot_record/record_server.py` | HTTP API + FEATURES |
| `gello_desk/lerobot_record/ros_bridge.py` | ROS 订阅与 freshness |
| `gello_desk/lerobot_record/lerobot_episode.sh` | curl 客户端 |
| `gello_desk/lerobot_record/start_dual_realsense.sh` | 双相机 launch |
| `gello_desk/desk_prep.py` | Desk REST |
| `gello_desk/teleop_error_watchdog.py` | 异常看护 |
| `docs/teleop_lerobot_launcher.md` | 整栈启动 runbook |

文档版本对应仓库 `gello_desk/lerobot_record` 录制栈（FastAPI app version `1.1`）与 125 实机部署。
