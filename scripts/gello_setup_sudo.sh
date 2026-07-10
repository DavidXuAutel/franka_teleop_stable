#!/bin/bash
# One-time sudo setup for Franka GELLO teleoperation on this host.
# Run with:  sudo bash /home/yao/gello_setup_sudo.sh
set -e

USER_NAME=yao

echo "[1/3] Adding $USER_NAME to dialout group (serial port access)..."
if id -nG "$USER_NAME" | grep -qw dialout; then
  echo "    already in dialout, skip."
else
  usermod -aG dialout "$USER_NAME"
  echo "    added. NOTE: takes effect on next login (re-login or reboot)."
fi

echo "[2/3] Installing udev rule for U2D2 / OpenRB-150 (permissions + 1ms latency)..."
cat > /etc/udev/rules.d/99-gello.rules <<'EOF'
# Lower latency_timer (1 instead of default 16 ms) & permission fix for U2D2 and OpenRB-150 devices
ACTION=="add", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6014", MODE="0666", ATTR{device/latency_timer}="1"
ACTION=="add", ATTRS{idVendor}=="2f5d", ATTRS{idProduct}=="2202", MODE="0666", ATTR{device/latency_timer}="1"
EOF
echo "    udev rule written to /etc/udev/rules.d/99-gello.rules"

echo "[3/3] Reloading udev rules..."
udevadm control --reload-rules
udevadm trigger
echo "    done."

echo ""
echo "Setup complete. Now:"
echo "  1. Plug OpenRB-150 USB-C into the PC AND connect external 5V power (jumper on VIN(DXL))."
echo "  2. Run:  ls /dev/serial/by-id/   to confirm the OpenRB-150 appears."
echo "  (If dialout group change did not take effect, re-login or reboot once.)"
