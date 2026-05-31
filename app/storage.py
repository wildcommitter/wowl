"""YAML-backed storage for the machine list.

The file layout is a human-editable mapping:

    machines:
      - name: Desktop
        mac: "AA:BB:CC:DD:EE:FF"
        broadcast: "255.255.255.255"
        port: 9
"""
from __future__ import annotations

import os
import threading
from typing import Any

import yaml

from .wol import normalize_mac

DEFAULT_BROADCAST = "255.255.255.255"
DEFAULT_PORT = 9

# A single process-wide lock serializes read/modify/write cycles so concurrent
# requests can't clobber the YAML file.
_lock = threading.Lock()


def _data_path() -> str:
    return os.environ.get("WOLW_DATA_FILE", "/data/machines.yaml")


def _read() -> list[dict[str, Any]]:
    path = _data_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    machines = doc.get("machines") or []
    if not isinstance(machines, list):
        raise ValueError("'machines' must be a list in the YAML file")
    return machines


def _write(machines: list[dict[str, Any]]) -> None:
    path = _data_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"machines": machines}, fh, sort_keys=False, default_flow_style=False)
    os.replace(tmp, path)


def list_machines() -> list[dict[str, Any]]:
    with _lock:
        return _read()


def get_machine(mac: str) -> dict[str, Any] | None:
    mac = normalize_mac(mac)
    with _lock:
        for m in _read():
            if normalize_mac(m["mac"]) == mac:
                return m
    return None


def add_machine(
    name: str,
    mac: str,
    broadcast: str = DEFAULT_BROADCAST,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("Machine name is required")
    mac = normalize_mac(mac)
    broadcast = (broadcast or DEFAULT_BROADCAST).strip()
    port = int(port)
    if not (0 < port < 65536):
        raise ValueError("Port must be between 1 and 65535")

    entry = {"name": name, "mac": mac, "broadcast": broadcast, "port": port}
    with _lock:
        machines = _read()
        if any(normalize_mac(m["mac"]) == mac for m in machines):
            raise ValueError(f"A machine with MAC {mac} already exists")
        machines.append(entry)
        _write(machines)
    return entry


def delete_machine(mac: str) -> bool:
    mac = normalize_mac(mac)
    with _lock:
        machines = _read()
        kept = [m for m in machines if normalize_mac(m["mac"]) != mac]
        if len(kept) == len(machines):
            return False
        _write(kept)
    return True
