from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import time
from typing import Callable

from controlplane.core.models import ControllerStatus


class SystemService:
    """Collect health for the machine running the Control Plane API."""

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        disk_path: Path = Path("/"),
        hostname_getter: Callable[[], str] = socket.gethostname,
    ) -> None:
        self.proc_root = proc_root
        self.disk_path = disk_path
        self.hostname_getter = hostname_getter

    @staticmethod
    def _percent(used: int | float, total: int | float) -> float:
        return round(used / total * 100, 1) if total else 0

    def _read_cpu_times(self) -> tuple[int, int]:
        fields = (self.proc_root / "stat").read_text().splitlines()[0].split()
        if not fields or fields[0] != "cpu":
            raise RuntimeError("Unable to read aggregate CPU statistics")

        values = [int(value) for value in fields[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    def _cpu_percent(self, sample_seconds: float = 0.05) -> float:
        total_before, idle_before = self._read_cpu_times()
        time.sleep(sample_seconds)
        total_after, idle_after = self._read_cpu_times()
        total_delta = total_after - total_before
        idle_delta = idle_after - idle_before
        return self._percent(total_delta - idle_delta, total_delta)

    def _memory(self) -> tuple[int, int]:
        values = {}
        for line in (self.proc_root / "meminfo").read_text().splitlines():
            key, _, raw_value = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(raw_value.strip().split()[0]) * 1024

        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        return total - available, total

    def get_status(self) -> ControllerStatus:
        memory_used, memory_total = self._memory()
        disk = shutil.disk_usage(self.disk_path)
        uptime_seconds = int(
            float((self.proc_root / "uptime").read_text().split()[0])
        )

        return ControllerStatus(
            name="Controller",
            hostname=self.hostname_getter(),
            status="online",
            api_status="online",
            cpu_cores=os.cpu_count() or 0,
            cpu_percent=self._cpu_percent(),
            memory_used_bytes=memory_used,
            memory_total_bytes=memory_total,
            memory_percent=self._percent(memory_used, memory_total),
            disk_used_bytes=disk.used,
            disk_total_bytes=disk.total,
            disk_percent=self._percent(disk.used, disk.total),
            uptime_seconds=uptime_seconds,
        )
