# Franka FR3 + GELLO 遥操作稳定版

**Tag:** stable-v1.0-2026-07-10

## 环境

| 组件 | 值 |
|------|-----|
| 远端服务器 | 10.229.20.125 (SSH: yao) |
| 机器人 IP | 10.229.66.91 |
| 机器人序列号 | 295341-2600345 |
| ROS2 | Humble |
| 工作区 | /home/yao/franka_ros2_ws |
| GELLO 源码 | /home/yao/gello_software |

## 已验证功能

- FCI 激活与 libfranka 通信
- GELLO 手臂遥操作（关节误差 ~0.027 rad）
- Franka Hand 夹爪同步
- MuJoCo 双窗口仿真（Franka + GELLO）

## 快速启动

```bash
ssh -i ~/.ssh/franka_ros2_ed25519 yao@10.229.20.125

# 推荐：安全重启（先链路预检，通过后再 desk_prep + 遥操）
bash /home/yao/gello_desk/restart_teleop.sh

# 或分步：
bash /home/yao/gello_desk/link_preflight.sh   # 链路预检（必须 PASS）
bash /home/yao/franka_control.sh prepare      # FCI 未激活时
bash /home/yao/gello_launch.sh                # 完整遥操作（内置预检门禁）
bash /home/yao/gello_desk/gello_desk_launch.sh mujoco  # 仿真
bash /home/yao/gello_launch.sh stop           # 停止
```

**流程约定：** 任何重启后先做链路预检（有线路由 / RTT / :1337），通过后再启遥操。

## 恢复配置

```bash
STABLE=/home/yao/franka_teleop_stable
cp $STABLE/configs/example_fr3_config.yaml \
   /home/yao/gello_software/ros2/src/franka_fr3_arm_controllers/config/
cp $STABLE/install_configs/example_fr3_config.yaml \
   /home/yao/franka_ros2_ws/install/franka_fr3_arm_controllers/share/franka_fr3_arm_controllers/config/
cp $STABLE/configs/franka_gello_single.yaml \
   /home/yao/gello_software/ros2/src/franka_gello_state_publisher/config/
cp $STABLE/scripts/*.sh /home/yao/
```

## 注意事项

1. 必须用 gello_launch.sh（GELLO 先于手臂启动）
2. load_gripper 必须为 true
3. 勿修改机器人网络（shopFloor 10.229.66.91 static）
4. FCI 必须走有线 `eno1` / src `10.229.20.125`，禁止 WiFi `wlx*` / src `10.229.66.70`
5. 重启遥操前先跑 `link_preflight.sh`；失败则不要启动遥操
