import os
import ssl
from urllib.parse import quote, urlencode

import httpx
from dotenv import load_dotenv

from controlplane.core.models import GuestStatus, NodeStatus, ProxmoxSnapshot

load_dotenv()


class ProxmoxService:
    def __init__(self):
        host = os.getenv("PVE_HOST")
        port = os.getenv("PVE_PORT", "8006")

        self.base_url = f"https://{host}:{port}/api2/json" if host else ""
        self.token_id = os.getenv("PVE_TOKEN_ID")
        self.token_secret = os.getenv("PVE_TOKEN_SECRET")
        verify_text = os.getenv("PVE_TLS_VERIFY", "true").strip().lower()
        if verify_text not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            raise RuntimeError("PVE_TLS_VERIFY must be a boolean value")
        verify_enabled = verify_text in {"1", "true", "yes", "on"}
        ca_file = os.getenv("PVE_TLS_CA_FILE", "").strip()
        if ca_file and not verify_enabled:
            raise RuntimeError("PVE_TLS_CA_FILE cannot be used when PVE_TLS_VERIFY is false")
        if not verify_enabled:
            self.tls_verify: bool | ssl.SSLContext = False
        elif ca_file:
            path = os.path.expanduser(ca_file)
            if not os.path.isfile(path):
                raise RuntimeError("PVE_TLS_CA_FILE does not point to a readable CA file")
            try:
                self.tls_verify = ssl.create_default_context(cafile=path)
            except (OSError, ssl.SSLError) as exc:
                raise RuntimeError("PVE_TLS_CA_FILE could not be loaded") from exc
        else:
            self.tls_verify = True

    @property
    def node(self) -> str:
        return os.getenv("PVE_NODE") or os.getenv("PVE_BACKUP_NODE", "")

    def _headers(self):
        if not self.base_url:
            raise RuntimeError("PVE_HOST is not configured")
        if not self.token_id or not self.token_secret:
            raise RuntimeError("Proxmox API credentials are not configured")

        return {
            "Authorization": (
                f"PVEAPIToken={self.token_id}={self.token_secret}"
            )
        }

    def _get(self, path: str):
        with httpx.Client(verify=self.tls_verify, timeout=5.0) as client:
            response = client.get(
                f"{self.base_url}{path}",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()["data"]

    @staticmethod
    def _percent(used: int | float, total: int | float) -> float:
        return round(used / total * 100, 1) if total else 0

    @classmethod
    def _normalize_node(cls, node: dict) -> NodeStatus:
        mem_used = node.get("mem", 0)
        mem_total = node.get("maxmem", 0)
        disk_used = node.get("disk", 0)
        disk_total = node.get("maxdisk", 0)

        return NodeStatus(
            name=node["node"],
            status=node.get("status", "unknown"),
            cpu_cores=node.get("maxcpu", 0),
            cpu_percent=round(node.get("cpu", 0) * 100, 1),
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=cls._percent(mem_used, mem_total),
            disk_used_bytes=disk_used,
            disk_total_bytes=disk_total,
            disk_percent=cls._percent(disk_used, disk_total),
            uptime_seconds=node.get("uptime", 0),
        )

    @classmethod
    def _normalize_guest(
        cls,
        guest: dict,
        *,
        node_name: str,
        guest_type: str,
    ) -> GuestStatus:
        mem_used = guest.get("mem", 0)
        mem_total = guest.get("maxmem", 0)
        prefix = "vm" if guest_type == "vm" else "ct"

        return GuestStatus(
            vmid=guest["vmid"],
            name=guest.get("name", f"{prefix}-{guest['vmid']}"),
            type=guest_type,
            status=guest.get("status", "unknown"),
            node=node_name,
            cpu_percent=round(guest.get("cpu", 0) * 100, 1),
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            memory_percent=cls._percent(mem_used, mem_total),
            uptime_seconds=guest.get("uptime", 0),
        )

    def get_nodes(self):
        return [self._normalize_node(node) for node in self._get("/nodes")]

    def get_guests(self):
        guests = []

        for node in self._get("/nodes"):
            node_name = node["node"]

            for vm in self._get(f"/nodes/{node_name}/qemu"):
                guests.append(self._normalize_guest(vm, node_name=node_name, guest_type="vm"))

            for ct in self._get(f"/nodes/{node_name}/lxc"):
                guests.append(self._normalize_guest(ct, node_name=node_name, guest_type="lxc"))

        return sorted(guests, key=lambda guest: guest.vmid)

    def get_dashboard(self, node_name: str | None = None) -> ProxmoxSnapshot:
        node_name = node_name or self.node
        if not node_name:
            raise RuntimeError("PVE_NODE is not configured")
        raw_nodes = self._get("/nodes")
        raw_node = next(
            (item for item in raw_nodes if item.get("node") == node_name),
            None,
        )
        if raw_node is None:
            raise ValueError(f"Node {node_name} was not returned by Proxmox")

        guests = [
            self._normalize_guest(vm, node_name=node_name, guest_type="vm")
            for vm in self._get(f"/nodes/{node_name}/qemu")
        ]
        guests.extend(
            self._normalize_guest(ct, node_name=node_name, guest_type="lxc")
            for ct in self._get(f"/nodes/{node_name}/lxc")
        )
        guests.sort(key=lambda guest: guest.vmid)
        return ProxmoxSnapshot(
            node=self._normalize_node(raw_node),
            guests=guests,
        )

    def _post(self, path: str, data: dict | None = None):
        with httpx.Client(verify=self.tls_verify, timeout=5.0) as client:
            response = client.post(
                f"{self.base_url}{path}",
                headers=self._headers(),
                data=data,
            )
            response.raise_for_status()
            return response.json()["data"]

    def guest_action(self, vmid: int, action: str):
        allowed_actions = {"start", "shutdown", "reboot"}

        if vmid == 100:
            raise ValueError(
                "VMID 100 is protected: control-plane power actions are disabled"
            )

        if action not in allowed_actions:
            raise ValueError(f"Unsupported action: {action}")

        guests = self.get_guests()

        guest = next(
            (guest for guest in guests if guest.vmid == vmid),
            None,
        )

        if guest is None:
            raise ValueError(f"Guest {vmid} does not exist")

        guest_type = "qemu" if guest.type == "vm" else "lxc"

        return self._post(
            f"/nodes/{guest.node}/{guest_type}/{vmid}/status/{action}"
        )

    def get_task_status(self, node: str, upid: str):
        encoded_upid = quote(upid, safe="")
        return self._get(
            f"/nodes/{quote(node, safe='')}/tasks/{encoded_upid}/status"
        )

    def start_guest_backup(
        self,
        *,
        node: str,
        vmid: int,
        storage: str,
        mode: str = "snapshot",
    ) -> str:
        if mode != "snapshot":
            raise ValueError("Only snapshot-mode backups are permitted")
        task = self._post(
            f"/nodes/{quote(node, safe='')}/vzdump",
            data={
                "vmid": vmid,
                "storage": storage,
                "mode": "snapshot",
                "compress": "zstd",
            },
        )
        if not isinstance(task, str) or not task.startswith("UPID:"):
            raise RuntimeError("Proxmox did not return a backup task UPID")
        return task

    def get_backup_artifacts(self, *, node: str, storage: str, vmid: int):
        query = urlencode({"content": "backup", "vmid": vmid})
        result = self._get(
            f"/nodes/{quote(node, safe='')}/storage/"
            f"{quote(storage, safe='')}/content?{query}"
        )
        if not isinstance(result, list):
            raise RuntimeError("Proxmox returned an invalid backup artifact list")
        return result
