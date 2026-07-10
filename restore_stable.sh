#!/bin/bash
# Restore stable-v1.0-2026-07-10 configs and scripts to live paths.
set -eo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "Restoring from $ROOT ..."
cp "$ROOT/configs/example_fr3_config.yaml" /home/yao/gello_software/ros2/src/franka_fr3_arm_controllers/config/
cp "$ROOT/configs/controllers.yaml" /home/yao/gello_software/ros2/src/franka_fr3_arm_controllers/config/
cp "$ROOT/configs/franka_gello_single.yaml" /home/yao/gello_software/ros2/src/franka_gello_state_publisher/config/
cp "$ROOT/install_configs/example_fr3_config.yaml" \
  /home/yao/franka_ros2_ws/install/franka_fr3_arm_controllers/share/franka_fr3_arm_controllers/config/
cp "$ROOT/scripts/"*.sh /home/yao/
cp "$ROOT/scripts/activate_fci_deskapi.py" /home/yao/
rsync -a --exclude="__pycache__" --exclude=".desk_token" "$ROOT/gello_desk/" /home/yao/gello_desk/
chmod +x /home/yao/*.sh /home/yao/gello_desk/*.sh /home/yao/gello_desk/*.py 2>/dev/null || true
echo "Restored. Restart teleop: bash /home/yao/gello_launch.sh"
