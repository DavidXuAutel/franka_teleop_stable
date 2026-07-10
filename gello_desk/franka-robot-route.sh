#!/bin/bash
# Force Franka robot traffic via wired eno1 (C2 Shop Floor network).
ROBOT="10.229.20.91"
WIRED="eno1"
WIRED_IP="10.229.20.125"

sudo ip route del 10.229.66.91/32 2>/dev/null || true
sudo ip route replace "${ROBOT}/32" dev "${WIRED}" src "${WIRED_IP}" metric 10 2>/dev/null || true
