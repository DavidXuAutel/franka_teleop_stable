#!/usr/bin/env python3
"""
Activate Franka FCI via Desk API.

Usage:
    python3 activate_fci_deskapi.py --host 10.229.66.91 --user <desk-user>

The script prompts for the Desk password, requests a control token, switches
the robot to Execution mode, activates FCI, and prints the final FCI state.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any


class ApiFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Activate Franka FCI via Desk API.")
    parser.add_argument("--host", default="10.229.66.91", help="Robot IP or host.")
    parser.add_argument("--user", required=True, help="Desk username.")
    parser.add_argument(
        "--owner",
        default="cursor",
        help="Owner name to use for the control token. Default: cursor.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Control token request timeout in seconds. Default: 5.",
    )
    parser.add_argument(
        "--unlock-joints",
        action="store_true",
        help="Open brakes/unlock joints before activating FCI.",
    )
    return parser.parse_args()


def make_auth_header(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def request_json(
    *,
    method: str,
    url: str,
    auth_header: str | None = None,
    control_token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 20,
) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header
    if control_token:
        headers["X-Control-Token"] = control_token
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=utf-8"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    context = ssl._create_unverified_context()

    try:
        with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload) if payload else None
    except TimeoutError:
        return 0, {"code": "ClientTimeout", "message": f"Timed out after {timeout}s"}
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            parsed = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, parsed


def require_success(status: int, payload: Any, action: str, expected: set[int]) -> None:
    if status not in expected:
        raise ApiFailure(f"{action} failed: HTTP {status} {payload}")


def release_token(base_url: str, auth_header: str, token: str) -> None:
    status, payload = request_json(
        method="POST",
        url=f"{base_url}/api/system/control-token:release",
        auth_header=auth_header,
        control_token=token,
    )
    if status == 204:
        print("Control token released after failure.")
    else:
        print(f"Failed to release control token after failure: HTTP {status} {payload}")


def main() -> int:
    args = parse_args()
    password = os.environ.get("FRANKA_DESK_PASSWORD") or getpass.getpass(
        f"Desk password for {args.user}: "
    )
    auth_header = make_auth_header(args.user, password)
    base_url = f"https://{args.host}"
    token: str | None = None

    try:
        status, payload = request_json(method="GET", url=f"{base_url}/api/fci")
        require_success(status, payload, "Reading FCI state", {200})
        print(f"Initial FCI state: {payload}")

        status, payload = request_json(
            method="POST",
            url=f"{base_url}/api/system/control-token:take",
            auth_header=auth_header,
            body={"owner": args.owner, "timeout": args.timeout},
            timeout=args.timeout + 10,
        )
        require_success(status, payload, "Taking control token", {200})
        token = payload["token"]
        print(f"Control token acquired: tokenId={payload.get('tokenId')}")

        status, payload = request_json(
            method="POST",
            url=f"{base_url}/api/system/operating-mode:change",
            auth_header=auth_header,
            control_token=token,
            body={"desiredOperatingMode": "Execution"},
        )
        require_success(status, payload, "Changing operating mode to Execution", {204})
        print("Operating mode changed to Execution.")

        if args.unlock_joints:
            status, payload = request_json(
                method="POST",
                url=f"{base_url}/api/arm/joints:unlock",
                auth_header=auth_header,
                control_token=token,
            )
            require_success(status, payload, "Unlocking joints", {204})
            print("Joints unlocked.")

        status, payload = request_json(
            method="POST",
            url=f"{base_url}/api/fci:activate",
            auth_header=auth_header,
            control_token=token,
        )
        require_success(status, payload, "Activating FCI", {204})
        print("FCI activation request succeeded.")

        status, payload = request_json(method="GET", url=f"{base_url}/api/fci")
        require_success(status, payload, "Reading final FCI state", {200})
        print(f"Final FCI state: {payload}")
        return 0
    except ApiFailure as exc:
        if token:
            release_token(base_url, auth_header, token)
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
