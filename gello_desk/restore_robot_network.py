#!/usr/bin/env python3
"""Restore Franka robot network to original DHCP shop-floor settings."""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request

ORIGINAL_NETWORK = {
    "robot": {"network": "192.168.0.0"},
    "shopFloor": {"type": "Dhcp"},
}
SERIAL = "295341-2600345"


class DeskClient:
    def __init__(self, host: str, user: str, password: str) -> None:
        self.host = host
        self.auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.ctx = ssl._create_unverified_context()
        self.token: str | None = None

    def _call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        token: bool = False,
        timeout: float = 20,
    ) -> tuple[int, str]:
        headers = {
            "Authorization": f"Basic {self.auth}",
            "Content-Type": "application/json;charset=utf-8",
        }
        if token:
            if not self.token:
                raise RuntimeError("Control token required")
            headers["X-Control-Token"] = self.token
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            f"https://{self.host}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=timeout) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def system_info(self) -> dict:
        code, body = self._call("GET", "/api/system")
        if code != 200:
            raise RuntimeError(f"GET /api/system failed ({code}): {body}")
        return json.loads(body)

    def take_control(self, owner: str = "gello-network-restore") -> None:
        code, body = self._call(
            "POST",
            "/api/system/control-token:take",
            {"owner": owner, "timeout": 120},
            timeout=15,
        )
        if code != 200:
            raise RuntimeError(f"take token failed ({code}): {body}")
        self.token = json.loads(body)["token"]
        token_path = os.path.expanduser("~/gello_desk/.desk_token")
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w", encoding="utf-8") as handle:
            handle.write(self.token)
        os.chmod(token_path, 0o600)

    def restore_network(self) -> None:
        code, body = self._call(
            "PATCH",
            "/api/configuration",
            {"networkConfiguration": ORIGINAL_NETWORK},
            token=True,
            timeout=60,
        )
        if code not in (200, 204):
            raise RuntimeError(f"restore network failed ({code}): {body}")

    def reboot(self, *, token: bool = True) -> None:
        code, body = self._call("POST", "/api/system:reboot", token=token)
        if code not in (200, 202, 204):
            raise RuntimeError(f"reboot failed ({code}): {body}")


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        subprocess.run(
            ["timeout", str(timeout), "bash", "-c", f"echo >/dev/tcp/{host}/{port}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def discover_hosts(
    user: str,
    password: str,
    candidates: list[str],
    subnets: list[str],
) -> list[str]:
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    ctx = ssl._create_unverified_context()
    found: list[str] = []

    hosts = list(candidates)
    for prefix in subnets:
        for i in range(1, 255):
            hosts.append(f"{prefix}.{i}")

    seen: set[str] = set()
    for host in hosts:
        if host in seen:
            continue
        seen.add(host)
        if not tcp_open(host, 443, timeout=0.5):
            continue
        try:
            req = urllib.request.Request(
                f"https://{host}/api/system",
                headers={"Authorization": f"Basic {auth}"},
            )
            with urllib.request.urlopen(req, context=ctx, timeout=3) as resp:
                data = json.loads(resp.read())
            if data.get("controlSerialNumber") == SERIAL:
                found.append(host)
                print(f"found robot desk at {host}")
        except Exception:
            continue
    return found


def wait_for_desk(
    user: str,
    password: str,
    candidates: list[str],
    subnets: list[str],
    timeout_s: float,
) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        hosts = discover_hosts(user, password, candidates, subnets)
        if hosts:
            return hosts[0]
        time.sleep(5)
    raise TimeoutError("robot Desk did not become reachable")


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore robot network to DHCP defaults")
    parser.add_argument(
        "--host",
        default=os.environ.get("DESK_HOST_WIFI")
        or os.environ.get("FRANKA_HOST")
        or "10.229.66.91",
    )
    parser.add_argument("--user", default=os.environ.get("DESK_USER", "franka"))
    parser.add_argument("--password", default=os.environ.get("DESK_PASSWORD", ""))
    parser.add_argument("--wait", type=float, default=300, help="Seconds to wait for Desk")
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Only scan for robot Desk hosts",
    )
    args = parser.parse_args()
    if not args.password:
        print("DESK_PASSWORD is required")
        return 1

    candidates = [
        args.host,
        os.environ.get("DESK_HOST_WIFI", "10.229.66.91"),
        os.environ.get("FRANKA_HOST", "10.229.20.91"),
        "10.229.66.91",
        "10.229.20.91",
    ]
    subnets = ["10.229.66", "10.229.20"]

    if args.discover_only:
        hosts = discover_hosts(args.user, args.password, candidates, subnets)
        print("hosts:", hosts or "none")
        return 0 if hosts else 1

    try:
        host = args.host
        if not tcp_open(host, 443):
            print(f"Desk not reachable at {host}, scanning...")
            host = wait_for_desk(args.user, args.password, candidates, subnets, args.wait)
    except TimeoutError as exc:
        print(exc)
        return 1

    client = DeskClient(host, args.user, args.password)
    info = client.system_info()
    print("connected:", host, info.get("status"), info.get("controlSerialNumber"))

    code, body = client._call("GET", "/api/configuration")
    if code == 200:
        current = json.loads(body).get("networkConfiguration")
        print("current network:", current)
    else:
        print("read config failed:", code, body[:200])

    try:
        client.take_control()
    except RuntimeError as exc:
        print(f"take control failed, trying reboot without token: {exc}")
        client.reboot(token=False)
        host = wait_for_desk(args.user, args.password, candidates, subnets, args.wait)
        client = DeskClient(host, args.user, args.password)
        client.take_control()

    print("restoring original network:", ORIGINAL_NETWORK)
    client.restore_network()
    print("network restored, rebooting...")
    client.reboot()
    print("reboot requested; original settings: shopFloor=Dhcp, robot=192.168.0.0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
