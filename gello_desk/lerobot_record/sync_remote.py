"""Sync local LeRobot dataset repo to remote archive host (no remote rsync required)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def load_env(path: str | Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    candidates = []
    if path:
        candidates.append(Path(path))
    here = Path(__file__).resolve().parent
    candidates.append(here / ".env")
    candidates.append(Path.home() / ".config" / "lerobot_record.env")
    for p in candidates:
        if not p.is_file():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
        break
    return env


def _ssh_base(env: dict[str, str]) -> list[str]:
    port = env.get("REMOTE_PORT", "31126")
    key = env.get("SSH_KEY", "").strip()
    cmd = ["ssh", "-p", port, "-o", "StrictHostKeyChecking=accept-new"]
    if key:
        cmd.extend(["-i", key])
    return cmd


def _prefix_env(env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    run_env = os.environ.copy()
    sshpass = env.get("SSHPASS", "").strip()
    prefix: list[str] = []
    if sshpass and not env.get("SSH_KEY", "").strip():
        prefix = ["sshpass", "-e"]
        run_env["SSHPASS"] = sshpass
    return prefix, run_env


def rsync_repo(local_dir: str | Path, repo_name: str, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """Upload dataset via `tar | ssh tar` (works without remote rsync)."""
    env = env or load_env()
    local_dir = Path(local_dir).resolve()
    if not local_dir.is_dir():
        return False, f"local dataset missing: {local_dir}"

    remote_ssh = env.get("REMOTE_SSH", "a25689@10.239.121.11")
    remote_path = env.get("REMOTE_PATH", "~/lerobot_datasets").rstrip("/")
    prefix, run_env = _prefix_env(env)
    ssh = _ssh_base(env)

    # Clean remote repo then extract fresh snapshot
    remote_cmd = (
        f"rm -rf {remote_path}/{repo_name} && "
        f"mkdir -p {remote_path}/{repo_name} && "
        f"tar xzf - -C {remote_path}/{repo_name}"
    )
    try:
        tar = subprocess.Popen(
            ["tar", "czf", "-", "-C", str(local_dir), "."],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ssh_proc = subprocess.Popen(
            prefix + ssh + [remote_ssh, remote_cmd],
            stdin=tar.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=run_env,
        )
        if tar.stdout:
            tar.stdout.close()
        ssh_out, ssh_err = ssh_proc.communicate(timeout=600)
        tar_err = tar.stderr.read() if tar.stderr else b""
        tar.wait(timeout=10)
        if tar.returncode != 0:
            return False, f"tar failed: {tar_err.decode(errors='replace')}"
        if ssh_proc.returncode != 0:
            return False, f"remote extract failed: {ssh_err.decode(errors='replace') or ssh_out.decode(errors='replace')}"
        return True, "upload ok (tar+ssh)"
    except Exception as exc:  # noqa: BLE001
        return False, f"upload failed: {exc}"
