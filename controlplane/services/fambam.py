from __future__ import annotations

import os
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path


_MAC_ADDRESS = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_SSH_USERNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_HOSTNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


class FambamConfigurationError(RuntimeError):
    """Raised when FAMBAM22 cannot be controlled safely."""


@dataclass(frozen=True)
class FambamConfiguration:
    host: str
    port: int
    mac: str
    ssh_user: str
    ssh_key: Path

    @classmethod
    def from_environment(cls) -> "FambamConfiguration":
        port_text = os.getenv("FAMBAM22_PORT", "25565")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise FambamConfigurationError(
                "FAMBAM22_PORT must be an integer"
            ) from exc
        return cls(
            host=os.getenv("FAMBAM22_HOST", "").strip(),
            port=port,
            mac=os.getenv("FAMBAM22_MAC", "").strip(),
            ssh_user=os.getenv("FAMBAM22_SSH_USER", "").strip(),
            ssh_key=Path(
                os.getenv(
                    "FAMBAM22_SSH_KEY",
                    "~/.ssh/control-plane-fambam22",
                )
            ).expanduser(),
        )

    def validate_network_target(self) -> None:
        if not self.host or len(self.host) > 253 or not _HOSTNAME.fullmatch(self.host):
            raise FambamConfigurationError("FAMBAM22_HOST is not configured correctly")
        if not 1 <= self.port <= 65535:
            raise FambamConfigurationError("FAMBAM22_PORT must be between 1 and 65535")

    def validate_wake_on_lan(self) -> None:
        self.validate_network_target()
        if not _MAC_ADDRESS.fullmatch(self.mac):
            raise FambamConfigurationError("FAMBAM22_MAC is not configured correctly")

    def validate_ssh(self) -> None:
        self.validate_network_target()
        if not _SSH_USERNAME.fullmatch(self.ssh_user):
            raise FambamConfigurationError(
                "FAMBAM22_SSH_USER is not configured correctly"
            )
        if not self.ssh_key.is_absolute() or not self.ssh_key.is_file():
            raise FambamConfigurationError(
                "FAMBAM22_SSH_KEY must point to an absolute key file path"
            )
        if not os.access(self.ssh_key, os.R_OK):
            raise FambamConfigurationError("FAMBAM22_SSH_KEY is not readable")


class FambamService:
    def __init__(self, configuration: FambamConfiguration | None = None):
        self.configuration = configuration or FambamConfiguration.from_environment()

    def is_online(self) -> bool:
        self.configuration.validate_network_target()
        try:
            with socket.create_connection(
                (self.configuration.host, self.configuration.port), timeout=1
            ):
                return True
        except OSError:
            return False

    def snapshot(self) -> dict:
        try:
            online = self.is_online()
            unavailable_reason = "FAMBAM22 is already running." if online else None
            start_available = not online
            if start_available:
                self.configuration.validate_wake_on_lan()
        except FambamConfigurationError:
            online = False
            unavailable_reason = "FAMBAM22 start settings are not configured."
            start_available = False
        return {
            "id": "fambam22",
            "vmid": None,
            "name": "FAMBAM22",
            "node": self.configuration.host or "external",
            "host": self.configuration.host or None,
            "address": self.configuration.host or None,
            "port": self.configuration.port,
            "status": "running" if online else "stopped",
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "uptime_seconds": 0,
            "start_available": start_available,
            "unavailable_reason": unavailable_reason,
        }

    def start(self) -> str:
        self.configuration.validate_wake_on_lan()
        subprocess.run(
            ["wakeonlan", self.configuration.mac],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "Wake-on-LAN packet sent"

    def shutdown(self) -> str:
        self.configuration.validate_ssh()
        subprocess.Popen(
            [
                "ssh",
                "-i",
                str(self.configuration.ssh_key),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=8",
                f"{self.configuration.ssh_user}@{self.configuration.host}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return "Safe shutdown requested"
