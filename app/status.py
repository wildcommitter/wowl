"""Reachability checks used to tell whether a machine has woken up.

The MAC is the only required identifier. We find the machine's current IP
ourselves by sweeping the local subnet(s): send a throwaway UDP datagram to
every host address (which makes the *kernel* emit an ARP request), then read
/proc/net/arp for a completed entry whose MAC matches. ARP is mandatory at L2,
so any awake host on the subnet answers — no root / CAP_NET_RAW needed.

An explicit `ip` may still be set per machine to skip discovery; a discovered IP
is cached in-process so repeat checks probe just that one address.

Constraints: target must share an L2 subnet with this host, and the process
must see the host's ARP table (host networking) — which is how we deploy.
"""
from __future__ import annotations

import ipaddress
import socket
import struct
import threading
import time
from typing import Any

from .wol import normalize_mac

ARP_TABLE = "/proc/net/arp"
ROUTE_TABLE = "/proc/net/route"
ATF_COM = 0x2  # /proc/net/arp flag: entry is complete (resolved)
RTF_UP = 0x1
RTF_GATEWAY = 0x2
_INCOMPLETE_MAC = "00:00:00:00:00:00"

# Don't sweep networks larger than this (a /22). Bigger prefixes would mean
# thousands of probes per check; such hosts need an explicit `ip`.
MAX_SWEEP_HOSTS = 1024

# How often the background sweeper re-discovers IPs for all known machines.
DISCOVERY_INTERVAL = 15.0

# Discovered MAC -> IP, so we don't re-sweep on every poll. Kept fresh by the
# background discovery thread (see start_discovery).
_ip_cache: dict[str, str] = {}
_cache_lock = threading.Lock()
_discovery_started = False


def _provoke_arp(ip: str, sock: socket.socket) -> None:
    """Send a tiny UDP datagram so the kernel resolves the target's MAC.

    The packet is irrelevant (harmless if nothing listens); we only want the
    kernel to emit an ARP request as a side effect of addressing it.
    """
    try:
        sock.sendto(b"\x00", (ip, 9))
    except OSError:
        pass


def _new_udp_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    return sock


def _local_networks() -> list[ipaddress.IPv4Network]:
    """On-link IPv4 networks from /proc/net/route (skips loopback & gateways)."""
    nets: list[ipaddress.IPv4Network] = []
    try:
        with open(ROUTE_TABLE, "r", encoding="utf-8") as fh:
            rows = fh.read().splitlines()[1:]  # skip header
    except OSError:
        return nets
    for row in rows:
        f = row.split()
        if len(f) < 8:
            continue
        iface, dest_hex, gw_hex, flags_hex, mask_hex = f[0], f[1], f[2], f[3], f[7]
        if iface == "lo":
            continue
        try:
            flags = int(flags_hex, 16)
        except ValueError:
            continue
        if not (flags & RTF_UP) or (flags & RTF_GATEWAY):
            continue  # only directly-connected (on-link) subnets
        try:
            # /proc values are little-endian hex.
            dest = socket.inet_ntoa(struct.pack("<L", int(dest_hex, 16)))
            mask = socket.inet_ntoa(struct.pack("<L", int(mask_hex, 16)))
            net = ipaddress.IPv4Network(f"{dest}/{mask}", strict=False)
        except (ValueError, OSError):
            continue
        if net.prefixlen == 0:
            continue  # default route
        if net not in nets:
            nets.append(net)
    return nets


def _arp_table() -> dict[str, str]:
    """Map IP -> resolved MAC (uppercase) for completed /proc/net/arp entries."""
    out: dict[str, str] = {}
    try:
        with open(ARP_TABLE, "r", encoding="utf-8") as fh:
            rows = fh.read().splitlines()[1:]
    except OSError:
        return out
    for row in rows:
        p = row.split()
        if len(p) < 4:
            continue
        try:
            flags = int(p[2], 16)
        except ValueError:
            continue
        ip, mac = p[0], p[3]
        if flags & ATF_COM and mac != _INCOMPLETE_MAC:
            out[ip] = mac.upper()
    return out


def _sweep(settle: float = 0.8) -> int:
    """Probe every host on the local subnet(s) to prime the kernel ARP table."""
    sent = 0
    with _new_udp_socket() as sock:
        # Non-blocking: a blocking sendto stalls while the kernel ARP-resolves
        # each unreachable host (~seconds total for a /24). We don't care about
        # the datagram landing — only the ARP request it triggers.
        sock.setblocking(False)
        for net in _local_networks():
            if net.num_addresses - 2 > MAX_SWEEP_HOSTS:
                continue  # too large to sweep; needs an explicit ip
            for host in net.hosts():
                _provoke_arp(str(host), sock)
                sent += 1
    if sent:
        time.sleep(settle)
    return sent


def discover_ip_for_mac(mac: str, settle: float = 0.8) -> str | None:
    """Sweep local subnets and return the IP currently using `mac`, or None."""
    target = normalize_mac(mac)
    _sweep(settle)
    for ip, found_mac in _arp_table().items():
        if normalize_mac(found_mac) == target:
            return ip
    return None


def refresh_cache(macs: list[str]) -> None:
    """One sweep, then update the IP cache for all given MACs at once.

    Adds/updates entries for MACs now visible on the LAN and drops those that
    have gone away — so a host that powers on is picked up within one interval.
    """
    if not macs:
        return
    _sweep()
    mac_to_ip = {normalize_mac(m): ip for ip, m in _arp_table().items()}
    with _cache_lock:
        for mac in macs:
            key = normalize_mac(mac)
            ip = mac_to_ip.get(key)
            if ip:
                _ip_cache[key] = ip
            else:
                _ip_cache.pop(key, None)


def start_discovery(get_macs, interval: float = DISCOVERY_INTERVAL) -> None:
    """Start a daemon thread that periodically re-discovers IPs for all machines.

    `get_macs` is a callable returning the current list of stored MACs. Idempotent
    — only the first call per process starts the thread.
    """
    global _discovery_started
    with _cache_lock:
        if _discovery_started:
            return
        _discovery_started = True

    def loop() -> None:
        while True:
            try:
                refresh_cache(list(get_macs() or []))
            except Exception:
                pass  # never let a transient error kill the loop
            time.sleep(interval)

    threading.Thread(target=loop, name="wowl-discovery", daemon=True).start()


def arp_reachable(
    ip: str,
    expected_mac: str | None = None,
    timeout: float = 2.0,
    interval: float = 0.25,
) -> dict[str, Any]:
    """Probe a single IP via kernel ARP. Returns {reachable, mac, mac_matches}."""
    deadline = time.monotonic() + timeout
    mac: str | None = None
    with _new_udp_socket() as sock:
        _provoke_arp(ip, sock)
        while True:
            mac = _arp_table().get(ip)
            if mac or time.monotonic() >= deadline:
                break
            time.sleep(interval)
            _provoke_arp(ip, sock)
    matches: bool | None = None
    if mac and expected_mac:
        matches = normalize_mac(mac) == normalize_mac(expected_mac)
    return {"reachable": mac is not None, "mac": mac, "mac_matches": matches}


def tcp_open(ip: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if a TCP connection to ip:port succeeds."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_ip(machine: dict[str, Any]) -> tuple[str | None, bool]:
    """Return (ip, discovered). Uses an explicit ip, else the (sweeper-kept)
    cache, else an on-demand sweep so manual checks / wake-polls stay responsive
    even before the background sweeper has run."""
    explicit = (machine.get("ip") or "").strip()
    if explicit:
        return explicit, False

    key = normalize_mac(machine["mac"])
    with _cache_lock:
        cached = _ip_cache.get(key)
    if cached:
        return cached, True

    found = discover_ip_for_mac(machine["mac"])
    if found:
        with _cache_lock:
            _ip_cache[key] = found
    return found, True


def check_machine(machine: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    """Combined reachability for a stored machine entry.

    The IP is auto-discovered from the MAC when not set explicitly. `online` is
    True if ARP resolves OR the optional TCP port is open. If the MAC can't be
    found on any local subnet, the host is treated as offline (asleep).
    """
    ip, discovered = _resolve_ip(machine)
    if not ip:
        return {
            "online": False,
            "ip": None,
            "discovered": discovered,
            "reason": "MAC not found on local subnet (asleep or off-subnet)",
            "arp": None,
            "tcp": None,
        }

    arp = arp_reachable(ip, machine.get("mac"), timeout=timeout)

    # If a cached/discovered IP no longer answers, drop it so the next check
    # re-sweeps (handles a host that changed IP via DHCP after sleeping).
    if discovered and not arp["reachable"]:
        with _cache_lock:
            _ip_cache.pop(normalize_mac(machine["mac"]), None)

    tcp: dict[str, Any] | None = None
    tcp_port = machine.get("tcp_port")
    if tcp_port:
        is_open = tcp_open(ip, int(tcp_port), timeout=min(1.0, timeout))
        tcp = {"port": int(tcp_port), "open": is_open}

    # "online" is driven by ARP discovery alone; the TCP port is reported
    # separately as a secondary signal (a host answers ARP whether or not the
    # probed service is up).
    online = bool(arp["reachable"])
    return {"online": online, "ip": ip, "discovered": discovered, "arp": arp, "tcp": tcp}
