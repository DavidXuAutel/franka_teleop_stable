#!/usr/bin/env python3
"""Prepare Franka robot via Desk API before GELLO teleoperation."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

TOKEN_FILE = os.environ.get(
    "DESK_TOKEN_FILE",
    os.path.expanduser("~/gello_desk/.desk_token"),
)


class DeskClient:
    def __init__(self, host: str, username: str, password: str) -> None:
        self.base = f"https://{host}"
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json;charset=utf-8",
        }
        self.control_token: str | None = None

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        control: bool = False,
        accept_empty: bool = False,
    ) -> dict | list | None:
        headers = dict(self.headers)
        if control:
            if not self.control_token:
                raise RuntimeError("Control token required but not acquired")
            headers["X-Control-Token"] = self.control_token
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        ctx = urllib.request.ssl._create_unverified_context()
        try:
            # Keep modest; Desk take-control confirm can otherwise block callers ~30s+.
            with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
                raw = resp.read().decode()
                if accept_empty or not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            raise RuntimeError(f"{method} {path} failed ({exc.code}): {detail}") from exc

    def save_token(self) -> None:
        if not self.control_token:
            return
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as handle:
            handle.write(self.control_token)
        os.chmod(TOKEN_FILE, 0o600)

    def load_saved_token(self) -> bool:
        if not os.path.isfile(TOKEN_FILE):
            return False
        with open(TOKEN_FILE, encoding="utf-8") as handle:
            self.control_token = handle.read().strip()
        return bool(self.control_token)

    def clear_saved_token(self) -> None:
        if os.path.isfile(TOKEN_FILE):
            os.remove(TOKEN_FILE)

    def get_system(self) -> dict:
        result = self._request("GET", "/api/system")
        assert isinstance(result, dict)
        return result

    def get_fci(self) -> dict:
        result = self._request("GET", "/api/fci")
        assert isinstance(result, dict)
        return result

    def get_control_token_state(self) -> dict:
        result = self._request("GET", "/api/system/control-token")
        assert isinstance(result, dict)
        return result

    def release_control(self) -> bool:
        if not self.load_saved_token():
            print("No saved control token to release")
            return False
        try:
            self._request(
                "POST",
                "/api/system/control-token:release",
                control=True,
                accept_empty=True,
            )
            print("Released control token")
            self.clear_saved_token()
            self.control_token = None
            return True
        except RuntimeError as exc:
            print(f"Release failed: {exc}")
            self.clear_saved_token()
            self.control_token = None
            return False

    def take_control(self, owner: str, timeout: float = 8.0) -> bool:
        state = self.get_control_token_state()
        current_owner = state.get("owner")
        if current_owner == owner and self.load_saved_token():
            print(f"Reusing saved control token for {owner}")
            return True
        # Desk UI (owner typically "franka") requires on-screen confirm; do not
        # block startup for a full Desk timeout. Ask operator to Release first.
        if current_owner and current_owner != owner:
            print(
                f"Control owned by {current_owner!r}. "
                "In Desk: Release control, then retry; "
                f"trying take with short timeout={timeout}s ..."
            )
        try:
            result = self._request(
                "POST",
                "/api/system/control-token:take",
                {"owner": owner, "timeout": timeout},
            )
        except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
            msg = str(exc)
            if any(
                code in msg
                for code in ('"code":"Timeout"', '"code":"RequestOverride"', "timed out")
            ) or isinstance(exc, (TimeoutError, urllib.error.URLError)):
                current = self.get_control_token_state()
                print(
                    "Could not take control token; current owner:",
                    current.get("owner"),
                    f"({exc})",
                )
                return False
            raise
        assert isinstance(result, dict)
        self.control_token = result["token"]
        self.save_token()
        print(f"Took control token (id={result.get('tokenId')})")
        return True

    def unlock_joints(self) -> None:
        self._request("POST", "/api/arm/joints:unlock", control=True, accept_empty=True)
        print("Joints unlocked")

    def activate_fci(self) -> None:
        fci = self.get_fci()
        if fci.get("status") == "Active":
            print("FCI already active")
            return
        self._request("POST", "/api/fci:activate", control=True, accept_empty=True)
        print("FCI activated")

    def recover_safety(self) -> None:
        recovery = self._request("GET", "/api/safety/recovery", control=True)
        print("Recovery state:", recovery)
        self._request("POST", "/api/safety/recovery:start", control=True, accept_empty=True)
        print("Recovery started")
        self._request(
            "POST",
            "/api/safety/recovery:confirm",
            control=True,
            accept_empty=True,
        )
        print("Recovery confirmed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Franka via Desk API")
    parser.add_argument("--host", default=os.environ.get("DESK_HOST_WIFI") or os.environ.get("FRANKA_HOST", "10.229.20.91"))
    parser.add_argument("--user", default=os.environ.get("DESK_USER", "franka"))
    parser.add_argument("--password", default=os.environ.get("DESK_PASSWORD", ""))
    parser.add_argument("--owner", default="gello-teleop")
    parser.add_argument("--release", action="store_true", help="Release saved token")
    parser.add_argument("--recover", action="store_true", help="Run safety recovery")
    args = parser.parse_args()
    if not args.password and not args.release:
        print("DESK_PASSWORD is required", file=sys.stderr)
        return 1

    client = DeskClient(args.host, args.user, args.password)
    if args.release:
        return 0 if client.release_control() else 1

    system = client.get_system()
    print(
        "System:",
        system.get("status"),
        system.get("operatingMode", {}).get("status"),
    )

    if not client.take_control(args.owner):
        print("Continuing without Desk control token (FCI may already be active).")

    if client.control_token:
        if args.recover:
            try:
                client.recover_safety()
            except RuntimeError as exc:
                print(f"Recovery skipped: {exc}")
        try:
            client.unlock_joints()
        except Exception as exc:  # noqa: BLE001
            print(f"Unlock skipped: {exc}")
        try:
            client.activate_fci()
        except Exception as exc:  # noqa: BLE001
            print(f"FCI activate skipped: {exc}")
    else:
        fci = client.get_fci()
        print("FCI state:", fci.get("status"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
