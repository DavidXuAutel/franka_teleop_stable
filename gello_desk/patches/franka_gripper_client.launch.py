import os
import yaml
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def load_yaml(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    with open(file_path, "r") as file:
        return yaml.safe_load(file)


def generate_nodes(context):
    config_file_name = LaunchConfiguration("config_file").perform(context)
    skip_homing = LaunchConfiguration("skip_homing").perform(context).lower() in (
        "1",
        "true",
        "yes",
    )
    package_config_dir = FindPackageShare("franka_gripper_manager").perform(context)
    config_file = os.path.join(package_config_dir, "config", config_file_name)
    configs = load_yaml(config_file)
    nodes = []
    for item_name, config in configs.items():
        nodes.append(
            Node(
                package="franka_gripper_manager",
                executable="franka_gripper_client",
                name="franka_gripper_client",
                namespace=str(config["namespace"]),
                output="screen",
                parameters=[
                    {
                        "namespace": str(config["namespace"]),
                        "skip_homing": skip_homing,
                    }
                ],
            )
        )

    return nodes


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "config_file",
                default_value="example_fr3_config_franka_hand.yaml",
                description="Path to the franka_gripper_manager configuration file to load",
            ),
            DeclareLaunchArgument(
                "skip_homing",
                default_value="true",
                description=(
                    "Skip Gripper Homing on start. Homing can mode-switch the arm and "
                    "drop the libfranka TCP session used by ros2_control."
                ),
            ),
            OpaqueFunction(function=generate_nodes),
        ]
    )
