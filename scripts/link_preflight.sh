#!/usr/bin/env bash
# 链路预检 / Link preflight — MUST pass before teleop start.
#
# Checks (fail-fast):
#   1) Route to robot: wired eno1 / src 10.229.20.125 — NOT WiFi (wlx*) / src 10.229.66.70
#   2) TCP reachability nuance for robot:1337 (FCI)
#   3) Ping RTT/jitter sample (wired ~0.1ms; WiFi multi-ms → fail)
#
# Usage:
#   bash /home/yao/gello_desk/link_preflight.sh
#   bash /home/yao/gello_desk/link_preflight.sh --no-fix-wifi
#   ROBOT_IP=10.229.66.91 bash /home/yao/gello_desk/link_preflight.sh
#
# Exit 0 = PASS (safe to start teleop). Exit 1 = FAIL (do not start teleop).
set -eo pipefail

ROBOT_IP="${ROBOT_IP:-10.229.66.91}"
WIRED_IFACE="${WIRED_IFACE:-eno1}"
WIRED_SRC="${WIRED_SRC:-10.229.20.125}"
BAD_SRC="${BAD_SRC:-10.229.66.70}"
PING_COUNT="${PING_COUNT:-20}"
# Hard-fail thresholds (WiFi-like). Wired via gateway is typically ~0.1ms / mdev~0.01ms.
MAX_RTT_FAIL_MS="${MAX_RTT_FAIL_MS:-2.0}"
JITTER_FAIL_MS="${JITTER_FAIL_MS:-0.5}"
# Soft warnings (still pass if below fail thresholds)
MAX_RTT_WARN_MS="${MAX_RTT_WARN_MS:-0.5}"
JITTER_WARN_MS="${JITTER_WARN_MS:-0.15}"
AUTO_FIX_WIFI=1
SUDO_PASS="${SUDO_PASS:-cupcake777}"

for arg in "$@"; do
  case "$arg" in
    --no-fix-wifi) AUTO_FIX_WIFI=0 ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
  esac
done

PASS=0
FAIL=0
WARN=0

ok()   { PASS=$((PASS + 1)); echo "[PASS] $*"; }
fail() { FAIL=$((FAIL + 1)); echo "[FAIL] $*"; }
warn() { WARN=$((WARN + 1)); echo "[WARN] $*"; }
info() { echo "[INFO] $*"; }

sudo_ip() {
  if sudo -n true 2>/dev/null; then
    sudo "$@"
  else
    echo "$SUDO_PASS" | sudo -S "$@" 2>/dev/null
  fi
}

route_line() {
  ip route get "$ROBOT_IP" 2>/dev/null | head -1
}

parse_route() {
  # Sets: ROUTE_DEV ROUTE_SRC ROUTE_VIA
  local line
  line="$(route_line)"
  ROUTE_DEV="$(awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' <<<"$line")"
  ROUTE_SRC="$(awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' <<<"$line")"
  ROUTE_VIA="$(awk '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}' <<<"$line")"
}

is_wifi_dev() {
  [[ "$1" == wlx* || "$1" == wlan* || "$1" == wl* ]]
}

list_wifi_up() {
  ip -br link 2>/dev/null | awk '$2 ~ /UP|UNKNOWN/ && $1 ~ /^(wlx|wlan)/ {print $1}'
}

bring_wifi_down() {
  local iface
  local any=0
  while read -r iface; do
    [ -z "$iface" ] && continue
    info "Auto-fix: bringing WiFi $iface DOWN (local PC only; does not touch robot Desk network)"
    if sudo_ip ip link set "$iface" down; then
      any=1
      ok "WiFi $iface is DOWN"
    else
      fail "Could not bring down WiFi $iface (need sudo). 无法关闭 WiFi 接口。"
    fi
  done < <(list_wifi_up)
  # Also catch DOWN-but-still-routable edge? Usually route disappears when down.
  if [ "$any" -eq 0 ]; then
    # Force-down known pattern even if already DOWN (idempotent)
    while read -r iface; do
      [ -z "$iface" ] && continue
      sudo_ip ip link set "$iface" down 2>/dev/null || true
    done < <(ip -br link 2>/dev/null | awk '$1 ~ /^(wlx|wlan)/ {print $1}')
  fi
  sleep 1
  # Flush route cache for robot
  sudo_ip ip route flush cache 2>/dev/null || true
}

check_route() {
  echo
  echo "=== 1) Route to $ROBOT_IP ==="
  parse_route
  local line
  line="$(route_line)"
  info "ip route get: $line"

  if [ -z "$ROUTE_DEV" ]; then
    fail "No route to $ROBOT_IP. 无到机器人的路由。"
    return
  fi

  local bad=0
  if is_wifi_dev "$ROUTE_DEV"; then
    fail "Route via WiFi ($ROUTE_DEV). FCI must use wired $WIRED_IFACE. 路由走了 WiFi，禁止遥操。"
    bad=1
  fi
  if [ "$ROUTE_SRC" = "$BAD_SRC" ]; then
    fail "Route src is $BAD_SRC (WiFi path). Prefer src $WIRED_SRC on $WIRED_IFACE. 源地址是 WiFi 侧，禁止遥操。"
    bad=1
  fi

  if [ "$bad" -eq 1 ] && [ "$AUTO_FIX_WIFI" -eq 1 ]; then
    info "Attempting auto-fix: down WiFi interfaces and re-check route..."
    bring_wifi_down
    parse_route
    line="$(route_line)"
    info "After fix: $line"
    bad=0
    if is_wifi_dev "$ROUTE_DEV"; then
      fail "Still via WiFi ($ROUTE_DEV) after auto-fix. 自动修复后仍走 WiFi。"
      bad=1
    fi
    if [ "$ROUTE_SRC" = "$BAD_SRC" ]; then
      fail "Still src $BAD_SRC after auto-fix. 自动修复后源地址仍是 WiFi。"
      bad=1
    fi
  elif [ "$bad" -eq 1 ]; then
    fail "Auto-fix disabled (--no-fix-wifi). Operator: ip link set <wlx…> down, then re-run."
  fi

  if [ "$bad" -eq 1 ]; then
    return
  fi

  if [ "$ROUTE_DEV" != "$WIRED_IFACE" ]; then
    warn "Route device is '$ROUTE_DEV' (expected $WIRED_IFACE). 路由设备不是预期有线口。"
  fi
  if [ -n "$ROUTE_SRC" ] && [ "$ROUTE_SRC" != "$WIRED_SRC" ]; then
    warn "Route src is '$ROUTE_SRC' (preferred $WIRED_SRC)."
  fi

  if [ "$ROUTE_DEV" = "$WIRED_IFACE" ] || { [ -n "$ROUTE_SRC" ] && [ "$ROUTE_SRC" = "$WIRED_SRC" ]; }; then
    ok "Wired route OK: dev=$ROUTE_DEV src=${ROUTE_SRC:--} via=${ROUTE_VIA:--}"
  else
    # Not WiFi, but also not clearly wired — fail safe
    fail "Route not clearly wired (dev=$ROUTE_DEV src=$ROUTE_SRC). 路由未确认走有线。"
  fi
}

check_tcp_1337() {
  echo
  echo "=== 2) TCP $ROBOT_IP:1337 (FCI) ==="
  local out rc
  set +e
  out="$(timeout 2 bash -c "echo >/dev/tcp/${ROBOT_IP}/1337" 2>&1)"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    ok "FCI port 1337 is open / reachable"
    return
  fi

  # Distinguish refused (robot up, FCI inactive) vs timeout (path bad)
  set +e
  out="$(timeout 2 nc -zv -w2 "$ROBOT_IP" 1337 2>&1)"
  rc=$?
  set -e
  if echo "$out" | grep -qiE 'refused|Connection refused'; then
    warn "Port 1337 refused — robot reachable but FCI likely Inactive. desk_prep --recover can activate. 端口拒绝：链路通但 FCI 可能未激活。"
    info "$out"
    return
  fi
  if echo "$out" | grep -qiE 'succeeded|open|Connected'; then
    ok "FCI port 1337 reachable (nc)"
    return
  fi
  fail "Cannot reach $ROBOT_IP:1337 (rc=$rc). 无法连通 FCI 端口。 $out"
}

float_ge() {
  # true if $1 >= $2
  awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 >= b + 0) }'
}

check_rtt() {
  echo
  echo "=== 3) RTT / jitter (ping -c $PING_COUNT) ==="
  local ping_out stats loss min avg max mdev
  set +e
  ping_out="$(ping -c "$PING_COUNT" -W 1 "$ROBOT_IP" 2>&1)"
  local rc=$?
  set -e
  echo "$ping_out" | tail -5

  if [ "$rc" -ne 0 ]; then
    fail "Ping to $ROBOT_IP failed. 机器人 ping 失败。"
    return
  fi

  loss="$(echo "$ping_out" | sed -n 's/.*, \([0-9.]*\)% packet loss.*/\1/p' | tail -1)"
  stats="$(echo "$ping_out" | sed -n 's/.*= \([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\)\/\([0-9.]*\).*/\1 \2 \3 \4/p' | tail -1)"
  min="$(awk '{print $1}' <<<"$stats")"
  avg="$(awk '{print $2}' <<<"$stats")"
  max="$(awk '{print $3}' <<<"$stats")"
  mdev="$(awk '{print $4}' <<<"$stats")"

  if [ -z "$max" ] || [ -z "$mdev" ]; then
    fail "Could not parse ping RTT stats. 无法解析 ping 统计。"
    return
  fi

  info "rtt min/avg/max/mdev = ${min}/${avg}/${max}/${mdev} ms; loss=${loss:-?}%"

  if [ -n "$loss" ] && float_ge "$loss" "1"; then
    fail "Packet loss ${loss}% (>=1%). 丢包过高，禁止遥操。"
  fi

  if float_ge "$max" "$MAX_RTT_FAIL_MS"; then
    fail "Max RTT ${max}ms >= ${MAX_RTT_FAIL_MS}ms (WiFi-like). 延迟过高，疑似 WiFi，禁止遥操。"
  elif float_ge "$max" "$MAX_RTT_WARN_MS"; then
    warn "Max RTT ${max}ms > ${MAX_RTT_WARN_MS}ms (preferred wired ~0.1ms)."
  fi

  local rtt_bad=0
  if float_ge "$mdev" "$JITTER_FAIL_MS"; then
    fail "Jitter mdev ${mdev}ms >= ${JITTER_FAIL_MS}ms (WiFi-like). 抖动过高，禁止遥操。"
    rtt_bad=1
  elif float_ge "$mdev" "$JITTER_WARN_MS"; then
    warn "Jitter mdev ${mdev}ms > ${JITTER_WARN_MS}ms."
  fi

  if float_ge "$max" "$MAX_RTT_FAIL_MS"; then
    rtt_bad=1
  fi
  if [ -n "$loss" ] && float_ge "$loss" "1"; then
    rtt_bad=1
  fi

  if [ "$rtt_bad" -eq 0 ]; then
    ok "RTT/jitter OK for FCI (max=${max}ms mdev=${mdev}ms)"
  fi
}

echo "=============================================="
echo "  Franka link preflight / 链路预检"
echo "  robot=$ROBOT_IP  wired=$WIRED_IFACE/$WIRED_SRC"
echo "  auto_fix_wifi=$AUTO_FIX_WIFI"
echo "=============================================="

# Pre-emptively down WiFi if any UP, so route check starts clean (same fix as before)
if [ "$AUTO_FIX_WIFI" -eq 1 ]; then
  if list_wifi_up | grep -q .; then
    info "WiFi interface(s) UP — bringing down before route check..."
    bring_wifi_down
  fi
fi

check_route
check_tcp_1337
check_rtt

echo
echo "=============================================="
if [ "$FAIL" -gt 0 ]; then
  echo "RESULT: FAIL ($FAIL failed, $WARN warnings, $PASS passed)"
  echo "链路预检失败 — 不要启动遥操 / DO NOT start teleop."
  echo "Fix wired path (eno1 / src $WIRED_SRC), then re-run:"
  echo "  bash /home/yao/gello_desk/link_preflight.sh"
  echo "=============================================="
  exit 1
fi

echo "RESULT: PASS ($PASS passed, $WARN warnings)"
echo "链路预检通过 — 可以继续 desk_prep / 遥操。"
echo "=============================================="
exit 0
