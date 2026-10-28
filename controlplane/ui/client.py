from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

import httpx


PROTECTED_VMID = 100


class ControlPlaneError(RuntimeError):
    """A friendly error raised for API or task failures."""


@dataclass(frozen=True)
class ActionResult:
    vmid: int
    action: str
    node: str
    upid: str
    exit_status: str
    elapsed_seconds: int


class ControlPlaneClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        timeout: float = 8.0,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _json(self, method: str, path: str) -> dict[str, Any]:
        try:
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            response = await self._client.request(
                method, f"{self.base_url}{path}", headers=headers
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except ValueError:
                detail = exc.response.text
            raise ControlPlaneError(str(detail) or f"HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ControlPlaneError(f"Control Plane API unavailable: {exc}") from exc

    async def get_node(self, name: str | None = None) -> dict[str, Any]:
        payload = await self._json("GET", "/api/v1/proxmox/nodes")
        nodes = payload.get("nodes", [])
        node = next(
            (
                item
                for item in nodes
                if name is None or item.get("name") == name
            ),
            None,
        )
        if node is None:
            raise ControlPlaneError(f"Node {name} was not returned by the API")
        return node

    async def get_guests(self) -> list[dict[str, Any]]:
        payload = await self._json("GET", "/api/v1/proxmox/guests")
        return sorted(payload.get("guests", []), key=lambda guest: guest["vmid"])

    async def get_dashboard(
        self,
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        dict[str, Any],
        dict[str, Any],
    ]:
        payload = await self._json("GET", "/api/v1/dashboard")
        node = payload.get("node")
        if not isinstance(node, dict):
            raise ControlPlaneError("Dashboard response did not include PVE01")
        controller = payload.get("controller")
        if not isinstance(controller, dict):
            raise ControlPlaneError("Dashboard response did not include Controller")
        nas = payload.get("nas")
        if not isinstance(nas, dict):
            raise ControlPlaneError("Dashboard response did not include NAS")
        guests = sorted(payload.get("guests", []), key=lambda guest: guest["vmid"])
        return node, guests, controller, nas

    async def get_automation(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        payload = await self._json("GET", "/api/v1/automation")
        return payload.get("jobs", []), payload.get("executions", [])

    async def run_minecraft_backup(self) -> str:
        payload = await self._json(
            "POST", "/api/v1/automation/jobs/minecraft-vm-backup/run"
        )
        execution_id = payload.get("execution_id")
        if not payload.get("accepted") or not execution_id:
            raise ControlPlaneError("The API did not accept the backup job")
        return str(execution_id)

    async def run_action(
        self,
        vmid: int,
        action: str,
        node: str,
        *,
        poll_interval: float = 1.0,
        poll_timeout: float = 300.0,
        progress: Callable[[int, str], None] | None = None,
    ) -> ActionResult:
        if vmid == PROTECTED_VMID:
            raise ControlPlaneError(
                "VMID 100 is protected; the control-plane guest cannot be power-managed"
            )
        if action not in {"start", "shutdown", "reboot"}:
            raise ControlPlaneError(f"Unsupported action: {action}")

        accepted = await self._json(
            "POST", f"/api/v1/proxmox/guests/{vmid}/{action}"
        )
        upid = accepted.get("task")
        if not accepted.get("accepted") or not upid:
            raise ControlPlaneError("The API did not accept the power action")

        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + poll_timeout
        encoded_upid = quote(str(upid), safe="")
        while True:
            payload = await self._json(
                "GET", f"/api/v1/proxmox/tasks/{node}/{encoded_upid}"
            )
            task = payload.get("task", {})
            elapsed = int(loop.time() - started)
            if progress is not None:
                progress(elapsed, str(task.get("status", "pending")))
            if task.get("status") == "stopped":
                exit_status = str(task.get("exitstatus", "UNKNOWN"))
                if exit_status != "OK":
                    raise ControlPlaneError(
                        f"{action.title()} failed for VMID {vmid}: {exit_status}"
                    )
                return ActionResult(
                    vmid, action, node, str(upid), exit_status, elapsed
                )
            if loop.time() >= deadline:
                raise ControlPlaneError(
                    f"Timed out waiting for {action} on VMID {vmid}"
                )
            await asyncio.sleep(poll_interval)
