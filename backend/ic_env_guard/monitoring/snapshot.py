from __future__ import annotations

import socket
import time
from datetime import UTC, datetime

import psutil

MAX_DISKS = 16
MAX_INTERFACES = 16


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_average() -> list[float]:
    try:
        return [float(value) for value in psutil.getloadavg()]
    except (AttributeError, OSError):
        return []


def _disk_snapshots() -> list[dict[str, object]]:
    disks: list[dict[str, object]] = []
    seen: set[str] = set()
    for partition in psutil.disk_partitions(all=False):
        mount = partition.mountpoint
        if mount in seen:
            continue
        seen.add(mount)
        try:
            usage = psutil.disk_usage(mount)
        except (OSError, PermissionError):
            continue
        disks.append(
            {
                "mount": mount,
                "device": partition.device,
                "fstype": partition.fstype,
                "used_bytes": usage.used,
                "total_bytes": usage.total,
                "free_bytes": usage.free,
                "percent": usage.percent,
            }
        )
        if len(disks) >= MAX_DISKS:
            break
    if not disks:
        usage = psutil.disk_usage("/")
        disks.append(
            {
                "mount": "/",
                "device": "/",
                "fstype": "unknown",
                "used_bytes": usage.used,
                "total_bytes": usage.total,
                "free_bytes": usage.free,
                "percent": usage.percent,
            }
        )
    return disks


def _network_snapshots() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for interface, counters in psutil.net_io_counters(pernic=True).items():
        rows.append(
            {
                "interface": interface,
                "rx_bytes": counters.bytes_recv,
                "tx_bytes": counters.bytes_sent,
                "rx_packets": counters.packets_recv,
                "tx_packets": counters.packets_sent,
            }
        )
        if len(rows) >= MAX_INTERFACES:
            break
    return rows


def local_host_snapshot(host_id: str = "local", name: str = "Local host") -> dict[str, object]:
    memory = psutil.virtual_memory()
    try:
        swap = psutil.swap_memory()
        swap_snapshot = {
            "used_bytes": swap.used,
            "total_bytes": swap.total,
            "free_bytes": swap.free,
            "percent": swap.percent,
        }
    except (OSError, PermissionError):
        swap_snapshot = {
            "used_bytes": 0,
            "total_bytes": 0,
            "free_bytes": 0,
            "percent": 0,
        }
    boot_time = psutil.boot_time()
    return {
        "host_id": host_id,
        "name": name,
        "address": "127.0.0.1",
        "hostname": socket.gethostname(),
        "status": "online",
        "sampled_at": _now_iso(),
        "cpu": {
            "percent": psutil.cpu_percent(interval=None),
            "cores_logical": psutil.cpu_count(logical=True) or 0,
            "cores_physical": psutil.cpu_count(logical=False) or 0,
            "load_average": _load_average(),
        },
        "memory": {
            "used_bytes": memory.used,
            "total_bytes": memory.total,
            "available_bytes": memory.available,
            "percent": memory.percent,
        },
        "swap": swap_snapshot,
        "disks": _disk_snapshots(),
        "network": _network_snapshots(),
        "uptime_seconds": max(0, int(time.time() - boot_time)),
    }


def offline_snapshot(host_id: str, name: str, address: str, message: str) -> dict[str, object]:
    return {
        "host_id": host_id,
        "name": name,
        "address": address,
        "hostname": None,
        "status": "offline",
        "sampled_at": _now_iso(),
        "error": message,
        "cpu": {"percent": 0, "cores_logical": 0, "cores_physical": 0, "load_average": []},
        "memory": {"used_bytes": 0, "total_bytes": 0, "available_bytes": 0, "percent": 0},
        "swap": {"used_bytes": 0, "total_bytes": 0, "free_bytes": 0, "percent": 0},
        "disks": [],
        "network": [],
        "uptime_seconds": 0,
    }
