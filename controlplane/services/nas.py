from __future__ import annotations

import os
from pathlib import Path
import shutil

from controlplane.core.models import NasStatus


class NasService:
    """Report storage from the controller's existing NAS mount."""

    def __init__(self, mount_path: str | Path | None = None) -> None:
        configured_path = mount_path or os.getenv("NAS_MOUNT_PATH", "/mnt/nas")
        self.mount_path = Path(configured_path)
        self.mount_path_display = str(configured_path)

    @staticmethod
    def _percent(used: int | float, total: int | float) -> float:
        return round(used / total * 100, 1) if total else 0

    def _offline(self, detail: str) -> NasStatus:
        return NasStatus(
            name="NAS",
            status="offline",
            mount_path=self.mount_path_display,
            storage_used_bytes=0,
            storage_total_bytes=0,
            storage_free_bytes=0,
            storage_percent=0,
            detail=detail,
        )

    def get_status(self) -> NasStatus:
        if not os.path.ismount(self.mount_path):
            return self._offline("NAS share is not mounted")

        try:
            usage = shutil.disk_usage(self.mount_path)
        except OSError as exc:
            return self._offline(f"NAS storage unavailable: {exc}")

        return NasStatus(
            name="NAS",
            status="online",
            mount_path=self.mount_path_display,
            storage_used_bytes=usage.used,
            storage_total_bytes=usage.total,
            storage_free_bytes=usage.free,
            storage_percent=self._percent(usage.used, usage.total),
        )
