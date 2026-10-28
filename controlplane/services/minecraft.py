from __future__ import annotations

from threading import Lock
from time import monotonic
from typing import Any


MINECRAFT_SERVERS = {
    104: {"name": "Minecraft VM 104"},
    105: {"name": "Minecraft VM 105"},
}


class MinecraftConflictError(RuntimeError):
    """Raised when the exclusive Minecraft resource group blocks a start."""


class MinecraftService:
    TASK_TTL_SECONDS = 10 * 60

    def __init__(self, proxmox: Any):
        self.proxmox = proxmox
        self._start_lock = Lock()
        self._task_lock = Lock()
        self._known_tasks: dict[str, tuple[str, float]] = {}

    @property
    def node(self) -> str:
        node = getattr(self.proxmox, "node", "")
        return node if isinstance(node, str) else ""

    @staticmethod
    def require_approved(vmid: int) -> dict[str, Any]:
        profile = MINECRAFT_SERVERS.get(vmid)
        if profile is None:
            raise ValueError(f"VMID {vmid} is not an approved Minecraft server")
        return profile

    def snapshot(self, *, enforce_exclusivity: bool) -> list[dict[str, Any]]:
        guests = {guest.vmid: guest for guest in self.proxmox.get_guests()}
        servers: list[dict[str, Any]] = []
        for vmid, profile in MINECRAFT_SERVERS.items():
            guest = guests.get(vmid)
            status = str(guest.status).lower() if guest is not None else "unknown"
            other_vmid = next(candidate for candidate in MINECRAFT_SERVERS if candidate != vmid)
            other = guests.get(other_vmid)
            other_status = str(other.status).lower() if other is not None else "unknown"
            start_available = status == "stopped"
            unavailable_reason: str | None = None

            if status == "running":
                unavailable_reason = f"{profile['name']} is already running."
            elif status != "stopped":
                unavailable_reason = (
                    f"{profile['name']} is not confirmed stopped by Proxmox."
                )
            elif enforce_exclusivity and other_status != "stopped":
                other_name = MINECRAFT_SERVERS[other_vmid]["name"]
                if other_status == "running":
                    unavailable_reason = (
                        f"{other_name} is currently running. Stop {other_name} before "
                        f"starting {profile['name']}."
                    )
                else:
                    unavailable_reason = (
                        f"{other_name} is not yet confirmed stopped by Proxmox."
                    )
                start_available = False

            servers.append(
                {
                    "vmid": vmid,
                    "name": profile["name"],
                    "node": str(guest.node) if guest is not None else self.node,
                    "status": status,
                    "cpu_percent": float(guest.cpu_percent) if guest is not None else 0,
                    "memory_percent": (
                        float(guest.memory_percent) if guest is not None else 0
                    ),
                    "uptime_seconds": int(guest.uptime_seconds) if guest is not None else 0,
                    "start_available": start_available,
                    "unavailable_reason": unavailable_reason,
                }
            )
        return servers

    def action(self, vmid: int, action: str, *, admin_override: bool) -> str:
        profile = self.require_approved(vmid)
        action_map = {"start": "start", "stop": "shutdown", "reboot": "reboot"}
        proxmox_action = action_map.get(action)
        if proxmox_action is None:
            raise ValueError(f"Unsupported Minecraft action: {action}")

        if action != "start" or admin_override:
            return self.proxmox.guest_action(vmid, proxmox_action)

        with self._start_lock:
            guests = {guest.vmid: guest for guest in self.proxmox.get_guests()}
            target = guests.get(vmid)
            if target is None:
                raise MinecraftConflictError(
                    f"{profile['name']} is not currently visible in Proxmox"
                )
            if str(target.status).lower() != "stopped":
                raise MinecraftConflictError(
                    f"{profile['name']} is not confirmed stopped by Proxmox"
                )

            other_vmid = next(candidate for candidate in MINECRAFT_SERVERS if candidate != vmid)
            other = guests.get(other_vmid)
            other_name = MINECRAFT_SERVERS[other_vmid]["name"]
            other_status = str(other.status).lower() if other is not None else "unknown"
            if other_status != "stopped":
                if other_status == "running":
                    message = (
                        f"{other_name} is currently running. Stop {other_name} before "
                        f"starting {profile['name']}."
                    )
                else:
                    message = (
                        f"{other_name} is not yet confirmed stopped by Proxmox. "
                        f"Try again after Proxmox reports it as stopped."
                    )
                raise MinecraftConflictError(message)

            return self.proxmox.guest_action(vmid, proxmox_action)

    def track_task(self, task: str, username: str) -> None:
        with self._task_lock:
            self._remove_expired_tasks()
            self._known_tasks[task] = (username, monotonic() + self.TASK_TTL_SECONDS)

    def can_poll_task(self, *, node: str, task: str, username: str, admin: bool) -> bool:
        if not self.node or node != self.node:
            return False
        with self._task_lock:
            self._remove_expired_tasks()
            record = self._known_tasks.get(task)
        return record is not None and (admin or record[0] == username)

    def _remove_expired_tasks(self) -> None:
        now = monotonic()
        self._known_tasks = {
            task: record
            for task, record in self._known_tasks.items()
            if record[1] > now
        }
