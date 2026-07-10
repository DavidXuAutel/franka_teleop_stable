#!/usr/bin/env python3
"""Configure shop-floor static IP, reboot robot, and activate FCI."""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

DESK_WIFI = os.environ.get("DESK_HOST_WIFI", "10.229.66.91")
ROBOT_WIRED = os.environ.get("FRANKA_HOST", "10.229.20.91")
USER = os.environ["DESK_USER"]
PASSWORD = os.environ["DESK_PASSWORD"]
CTX = ssl._create_unverified_context()


def request(
    host: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    token: str | None = None,
) -> tuple[int, str]:
    headers = {
        "Authorization": f"Basic {base64.b64encode(f'{USER}:{PASSWORD}'.encode()).decode()}",
        "Content-Type": "application/json;charset=utf-8",
    }
    if token:
        headers["X-Control-Token"] = token
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"https://{host}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def wait_tcp(host: str, port: int, timeout_s: float = 180.0) -> bool:
    import subprocess

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            subprocess.run(
                ["timeout", "1", "bash", "-c", f"echo >/dev/tcp/{host}/{port}"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except subprocess.CalledProcessError:
            time.sleep(2)
    return False


def main() -> int:
    print("Taking control token via WiFi Desk...")
    code, body = request(
        DESK_WIFI,
        "POST",
        "/api/system/control-token:take",
        body={"owner": "gello-wired-setup", "timeout": 120},
    )
    if code != 200:
        print(f"take token failed: {code} {body}")
        return 1
    token = json.loads(body)["token"]
    print("Control token acquired")

    print("Setting shop-floor static network...")
    patch_body = {
        "networkConfiguration": {
            "shopFloor": {
                "type": "Static",
                "address": ROBOT_WIRED,
                "netmask": "255.255.255.0",
            }
        }
    }
    code, body = request(DESK_WIFI, "PATCH", "/api/configuration", body=patch_body, token=token)
    if code not in (200, 204):
        print(f"network patch failed: {code} {body}")
        return 1
    print("Shop-floor static IP configured:", ROBOT_WIRED)

    print("Rebooting robot to apply network settings...")
    code, body = request(DESK_WIFI, "POST", "/api/system:reboot", token=token)
    if code not in (200, 202, 204):
        print(f"reboot failed: {code} {body}")
        return 1
    print("Reboot requested; waiting for wired FCI on", ROBOT_WIRED)

    if not wait_tcp(ROBOT_WIRED, 443, timeout_s=240):
        print("Desk HTTPS on wired IP did not come up")
        return 1
    print("Desk HTTPS reachable on wired IP")

    desk_host = ROBOT_WIRED
    if not wait_tcp(desk_host, 1337, timeout_s=120):
        print("FCI port 1337 not open on wired IP yet; trying Desk activation...")
    else:
        print("FCI port 1337 already open on wired IP")
        return 0

    code, body = request(
        desk_host,
        "POST",
        "/api/system/control-token:take",
        body={"owner": "gello-wired-setup", "timeout": 120},
    )
    if code != 200:
        print(f"wired take token failed: {code} {body}")
        return 1
    token = json.loads(body)["token"]

    request(desk_host, "POST", "/api/arm/joints:unlock", token=token)
    code, body = request(desk_host, "POST", "/api/fci:activate", token=token)
    if code not in (200, 204):
        print(f"FCI activate failed: {code} {body}")
        return 1

    if wait_tcp(ROBOT_WIRED, 1337, timeout_s=60):
        print("FCI active on wired IP")
        return 0

    print("FCI still unavailable on wired IP after activation")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
