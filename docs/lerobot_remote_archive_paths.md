# LeRobot 远程归档路径

- **主机**: `a25689@10.239.121.11`
- **SSH 端口**: `31126`
- **仓库 / repo**: `fr3_gello_teleop`
- **远程根目录**: `~/lerobot_datasets/fr3_gello_teleop/`
- **SSH 目标写法**: `a25689@10.239.121.11:31126:~/lerobot_datasets/fr3_gello_teleop/`

## 目录结构

| 内容 | 远程路径 |
|------|----------|
| 数据集根 | `~/lerobot_datasets/fr3_gello_teleop/` |
| 元信息 | `~/lerobot_datasets/fr3_gello_teleop/meta/info.json` |
| 关节 / 动作 parquet | `~/lerobot_datasets/fr3_gello_teleop/data/chunk-***/file-***.parquet` |
| 分集元数据 | `~/lerobot_datasets/fr3_gello_teleop/meta/episodes/chunk-***/file-***.parquet` |
| 关节 sidecar | `~/lerobot_datasets/fr3_gello_teleop/meta/sidecars/episode_XXXXXX.npz` |
| cam1 视频 | `~/lerobot_datasets/fr3_gello_teleop/videos/observation.images.cam1/chunk-***/file-***.mp4` |
| cam2 视频 | `~/lerobot_datasets/fr3_gello_teleop/videos/observation.images.cam2/chunk-***/file-***.mp4` |

## 示例（2026-07-14 episode `000005`）

| 内容 | 远程路径 |
|------|----------|
| 关节 / 动作 | `~/lerobot_datasets/fr3_gello_teleop/data/chunk-000/file-001.parquet` |
| sidecar | `~/lerobot_datasets/fr3_gello_teleop/meta/sidecars/episode_000005.npz` |
| cam1 | `~/lerobot_datasets/fr3_gello_teleop/videos/observation.images.cam1/chunk-000/file-001.mp4` |
| cam2 | `~/lerobot_datasets/fr3_gello_teleop/videos/observation.images.cam2/chunk-000/file-001.mp4` |

## 登录示例

```bash
ssh -p 31126 a25689@10.239.121.11
ls ~/lerobot_datasets/fr3_gello_teleop/
```

本地遥操机对应根目录：`/home/yao/lerobot_datasets/fr3_gello_teleop/`（录制结束后自动同步至上述远程路径）。
