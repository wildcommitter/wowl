"""Wake-on-LAN magic packet construction and sending."""
from __future__ import annotations

import re
import socket

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$")


def normalize_mac(mac: str) -> str:
    """Normalize a MAC address to uppercase colon-separated form.

    Accepts colon- or hyphen-separated forms. Raises ValueError if invalid.
    """
    mac = mac.strip()
    if not _MAC_RE.match(mac):
        raise ValueError(f"Invalid MAC address: {mac!r}")
    hexbytes = re.split(r"[:\-]", mac)
    return ":".join(b.upper() for b in hexbytes)


def build_magic_packet(mac: str) -> bytes:
    """Build a WoL magic packet: 6 bytes of 0xFF followed by the MAC 16 times."""
    clean = normalize_mac(mac).replace(":", "")
    payload = bytes.fromhex(clean)
    return b"\xff" * 6 + payload * 16


def send_magic_packet(
    mac: str,
    broadcast: str = "255.255.255.255",
    port: int = 9,
) -> None:
    """Send a magic packet to wake the host with the given MAC address."""
    packet = build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))
