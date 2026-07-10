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
bash /home/yao/franka_control.sh prepare   # FCI 未激活时
bash /home/yao/gello_launch.sh             # 完整遥操作
bash /home/yao/gello_desk/gello_desk_launch.sh mujoco  # 仿真
bash /home/yao/gello_launch.sh stop        # 停止
```

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
