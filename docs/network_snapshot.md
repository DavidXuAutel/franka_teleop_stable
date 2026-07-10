# Franka Network Configuration Snapshot

**Captured:** 2026-07-09 17:57:50 CST (2026-07-09 09:57:50 UTC)  
**Purpose:** Read-only reference snapshot — no settings were modified during capture.  
**Remote server:** 10.229.20.125 (user: yao)  
**Robot:** 10.229.66.91 (Desk: franka/franka123)

---

## 1. Robot — Desk API

### GET /api/configuration → networkConfiguration

```json
{
  "robot": {
    "network": "192.168.0.0"
  },
  "shopFloor": {
    "address": "10.229.66.91",
    "gateway": "10.229.66.1",
    "netmask": "255.255.255.0",
    "type": "Static"
  }
}
```

### GET /api/system

```json
{
    "cloud": {
        "account": "Agile Robots China",
        "connection": {
            "offlineReason": "NoNameserverConfigured",
            "status": "Offline"
        }
    },
    "controlSerialNumber": "295341-2600345",
    "operatingMode": {
        "status": "Execution"
    },
    "status": "Started"
}
```

---

## 2. Remote Server — Network Interfaces

### ip addr

```
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host 
       valid_lft forever preferred_lft forever
2: eno1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
    link/ether bc:fc:e7:64:4d:b9 brd ff:ff:ff:ff:ff:ff
    altname enp6s0
    inet 10.229.20.125/24 brd 10.229.20.255 scope global dynamic noprefixroute eno1
       valid_lft 85358sec preferred_lft 85358sec
    inet6 fe80::4e61:256c:aecd:e744/64 scope link noprefixroute 
       valid_lft forever preferred_lft forever
3: docker0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state DOWN group default 
    link/ether 5e:1e:0f:04:54:0e brd ff:ff:ff:ff:ff:ff
    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0
       valid_lft forever preferred_lft forever
```

### ip route

```
default via 10.229.20.1 dev eno1 proto dhcp metric 100 
10.229.20.0/24 dev eno1 proto kernel scope link src 10.229.20.125 metric 100 
169.254.0.0/16 dev eno1 scope link metric 1000 
172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1 linkdown 
```

### ip neigh (robot 10.229.66.91)

```

```

### ip neigh (all on eno1)

```
10.229.20.56 dev eno1 lladdr ac:b4:80:36:77:1a STALE
10.229.20.38 dev eno1 lladdr ac:b4:80:2d:78:10 STALE
10.229.20.53 dev eno1 lladdr ac:b4:80:36:71:51 STALE
10.229.20.69 dev eno1 lladdr ac:b4:80:2d:b1:12 STALE
10.229.20.1 dev eno1 lladdr d4:25:de:2f:ca:1c REACHABLE
10.229.20.42 dev eno1 lladdr ac:b4:80:2d:79:a3 STALE
fe80::82b7:4c84:987c:5a08 dev eno1 lladdr ac:b4:80:2d:78:10 STALE
fe80::61bd:22f3:13ba:c5a5 dev eno1 lladdr ac:b4:80:2d:b1:12 STALE
fe80::39ec:4123:9bb0:26e4 dev eno1 lladdr ac:b4:80:36:71:51 STALE
fe80::e273:35f:a9c:f29e dev eno1 lladdr ac:b4:80:2d:79:a3 STALE
fe80::27b2:5def:1e5f:cc30 dev eno1 lladdr ac:b4:80:36:77:1a STALE
```

### Robot reachability (ping)

```
PING 10.229.66.91 (10.229.66.91) 56(84) bytes of data.
64 bytes from 10.229.66.91: icmp_seq=1 ttl=63 time=0.126 ms
64 bytes from 10.229.66.91: icmp_seq=2 ttl=63 time=0.099 ms

--- 10.229.66.91 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1052ms
rtt min/avg/max/mdev = 0.099/0.112/0.126/0.013 ms
```

---

## 3. Remote Server — NetworkManager

### Active connections

```
NAME     UUID                                  TYPE      DEVICE  
配置 1   89ba1994-fead-41b6-b67d-a06b9bb9c2a2  ethernet  eno1    
docker0  3745e195-5cfb-4e3b-91f0-de41958eff4d  bridge    docker0 
```

### Wired connection (配置 1 / eno1)

```
connection.id:                          配置 1
connection.uuid:                        89ba1994-fead-41b6-b67d-a06b9bb9c2a2
connection.stable-id:                   --
connection.type:                        802-3-ethernet
connection.interface-name:              --
connection.autoconnect:                 是
connection.autoconnect-priority:        0
connection.autoconnect-retries:         -1 (default)
connection.multi-connect:               0（default）
connection.auth-retries:                -1
connection.timestamp:                   1783590856
connection.read-only:                   否
connection.permissions:                 --
connection.zone:                        --
connection.master:                      --
connection.slave-type:                  --
connection.autoconnect-slaves:          -1（default）
connection.secondaries:                 --
connection.gateway-ping-timeout:        0
connection.metered:                     未知
connection.lldp:                        default
connection.mdns:                        -1（default）
connection.llmnr:                       -1（default）
connection.dns-over-tls:                -1（default）
connection.wait-device-timeout:         -1
802-3-ethernet.port:                    --
802-3-ethernet.speed:                   0
802-3-ethernet.duplex:                  --
802-3-ethernet.auto-negotiate:          否
802-3-ethernet.mac-address:             --
802-3-ethernet.cloned-mac-address:      --
802-3-ethernet.generate-mac-address-mask:--
802-3-ethernet.mac-address-blacklist:   --
802-3-ethernet.mtu:                     自动
802-3-ethernet.s390-subchannels:        --
802-3-ethernet.s390-nettype:            --
802-3-ethernet.s390-options:            --
802-3-ethernet.wake-on-lan:             default
802-3-ethernet.wake-on-lan-password:    --
802-3-ethernet.accept-all-mac-addresses:-1（default）
ipv4.method:                            auto
ipv4.dns:                               --
ipv4.dns-search:                        --
ipv4.dns-options:                       --
ipv4.dns-priority:                      0
ipv4.addresses:                         --
ipv4.gateway:                           --
ipv4.routes:                            --
ipv4.route-metric:                      -1
ipv4.route-table:                       0 (unspec)
ipv4.routing-rules:                     --
ipv4.ignore-auto-routes:                否
ipv4.ignore-auto-dns:                   否
ipv4.dhcp-client-id:                    --
ipv4.dhcp-iaid:                         --
ipv4.dhcp-timeout:                      0 (default)
ipv4.dhcp-send-hostname:                是
ipv4.dhcp-hostname:                     --
ipv4.dhcp-fqdn:                         --
ipv4.dhcp-hostname-flags:               0x0（none）
ipv4.never-default:                     否
ipv4.may-fail:                          是
ipv4.required-timeout:                  -1 (default)
ipv4.dad-timeout:                       -1 (default)
ipv4.dhcp-vendor-class-identifier:      --
ipv4.dhcp-reject-servers:               --
ipv6.method:                            auto
ipv6.dns:                               --
ipv6.dns-search:                        --
ipv6.dns-options:                       --
ipv6.dns-priority:                      0
ipv6.addresses:                         --
ipv6.gateway:                           --
ipv6.routes:                            --
ipv6.route-metric:                      -1
ipv6.route-table:                       0 (unspec)
ipv6.routing-rules:                     --
ipv6.ignore-auto-routes:                否
ipv6.ignore-auto-dns:                   否
ipv6.never-default:                     否
ipv6.may-fail:                          是
ipv6.required-timeout:                  -1 (default)
ipv6.ip6-privacy:                       -1（unknown）
ipv6.addr-gen-mode:                     stable-privacy
ipv6.ra-timeout:                        0 (default)
ipv6.dhcp-duid:                         --
ipv6.dhcp-iaid:                         --
ipv6.dhcp-timeout:                      0 (default)
ipv6.dhcp-send-hostname:                是
ipv6.dhcp-hostname:                     --
ipv6.dhcp-hostname-flags:               0x0（none）
ipv6.token:                             --
proxy.method:                           none
proxy.browser-only:                     否
proxy.pac-url:                          --
proxy.pac-script:                       --
GENERAL.NAME:                           配置 1
GENERAL.UUID:                           89ba1994-fead-41b6-b67d-a06b9bb9c2a2
GENERAL.DEVICES:                        eno1
GENERAL.IP-IFACE:                       eno1
GENERAL.STATE:                          已激活
GENERAL.DEFAULT:                        是
GENERAL.DEFAULT6:                       否
GENERAL.SPEC-OBJECT:                    --
GENERAL.VPN:                            否
GENERAL.DBUS-PATH:                      /org/freedesktop/NetworkManager/ActiveConnection/10
GENERAL.CON-PATH:                       /org/freedesktop/NetworkManager/Settings/6
GENERAL.ZONE:                           --
GENERAL.MASTER-PATH:                    --
IP4.ADDRESS[1]:                         10.229.20.125/24
IP4.GATEWAY:                            10.229.20.1
IP4.ROUTE[1]:                           dst = 10.229.20.0/24, nh = 0.0.0.0, mt = 100
IP4.ROUTE[2]:                           dst = 169.254.0.0/16, nh = 0.0.0.0, mt = 1000
IP4.ROUTE[3]:                           dst = 0.0.0.0/0, nh = 10.229.20.1, mt = 100
IP4.DNS[1]:                             10.239.101.100
DHCP4.OPTION[1]:                        dhcp_lease_time = 86400
DHCP4.OPTION[2]:                        dhcp_server_identifier = 10.229.20.1
DHCP4.OPTION[3]:                        domain_name_servers = 10.239.101.100
DHCP4.OPTION[4]:                        expiry = 1783676429
DHCP4.OPTION[5]:                        ip_address = 10.229.20.125
DHCP4.OPTION[6]:                        requested_broadcast_address = 1
DHCP4.OPTION[7]:                        requested_domain_name = 1
DHCP4.OPTION[8]:                        requested_domain_name_servers = 1
DHCP4.OPTION[9]:                        requested_domain_search = 1
DHCP4.OPTION[10]:                       requested_host_name = 1
DHCP4.OPTION[11]:                       requested_interface_mtu = 1
DHCP4.OPTION[12]:                       requested_ms_classless_static_routes = 1
DHCP4.OPTION[13]:                       requested_nis_domain = 1
DHCP4.OPTION[14]:                       requested_nis_servers = 1
DHCP4.OPTION[15]:                       requested_ntp_servers = 1
DHCP4.OPTION[16]:                       requested_rfc3442_classless_static_routes = 1
DHCP4.OPTION[17]:                       requested_root_path = 1
DHCP4.OPTION[18]:                       requested_routers = 1
DHCP4.OPTION[19]:                       requested_static_routes = 1
DHCP4.OPTION[20]:                       requested_subnet_mask = 1
DHCP4.OPTION[21]:                       requested_time_offset = 1
DHCP4.OPTION[22]:                       requested_wpad = 1
DHCP4.OPTION[23]:                       routers = 10.229.20.1
DHCP4.OPTION[24]:                       subnet_mask = 255.255.255.0
IP6.ADDRESS[1]:                         fe80::4e61:256c:aecd:e744/64
IP6.GATEWAY:                            --
IP6.ROUTE[1]:                           dst = fe80::/64, nh = ::, mt = 1024
```

**Key settings:**
- ipv4.method: auto (DHCP)
- ipv4.routes: (none — no custom static routes)
- ipv4.addresses: (none configured statically; DHCP assigned 10.229.20.125/24)
- ipv4.gateway: (from DHCP: 10.229.20.1)

---

## 4. Remote Server — sysctl

### /etc/sysctl.d/99-franka-eno1.conf

```
net.ipv4.conf.eno1.rp_filter=0
```

### Live sysctl values

```
net.ipv4.conf.eno1.rp_filter = 0
net.ipv4.ip_forward = 1
```

---

## 5. Remote Server — NetworkManager Dispatcher

### /etc/NetworkManager/dispatcher.d/ listing

```
总计 24
drwxr-xr-x 5 root root 4096  7月  9 17:22 .
drwxr-xr-x 7 root root 4096  5月  6 18:07 ..
-rwxr-xr-x 1 root root 2293 11月 27  2021 01-ifupdown
drwxr-xr-x 2 root root 4096 11月 27  2021 no-wait.d
drwxr-xr-x 2 root root 4096 11月 27  2021 pre-down.d
drwxr-xr-x 2 root root 4096 11月 27  2021 pre-up.d
```

### Custom Franka dispatcher scripts

```
(none — 99-franka-robot-route previously removed, not present)
```

---

## 6. Summary

| Component | Setting |
|-----------|---------|
| Robot shopFloor | Static 10.229.66.91/24, gateway 10.229.66.1 |
| Robot internal network | 192.168.0.0 |
| Server eno1 | DHCP 10.229.20.125/24, gateway 10.229.20.1 |
| Server custom routes | None |
| Server NM ipv4.routes | None |
| Dispatcher 99-franka-robot-route | Absent |
| rp_filter (eno1) | 0 (persisted in 99-franka-eno1.conf) |
| ip_forward | 1 |

---

*Snapshot generated read-only. No PATCH, nmcli modify, route add, sysctl change, or reboot performed.*
