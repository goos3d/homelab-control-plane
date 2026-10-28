from __future__ import annotations

import os
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Header, Static

from controlplane.ui.client import (
    PROTECTED_VMID,
    ControlPlaneClient,
    ControlPlaneError,
)
from controlplane.ui.widgets import SystemCard


class ControlPlaneApp(App[None]):
    TITLE = "HOMELAB CONTROL PLANE"
    SUB_TITLE = "INFRASTRUCTURE STATUS"
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__()
        self.client = ControlPlaneClient(
            base_url or os.getenv("CONTROL_PLANE_API", "http://127.0.0.1:8000"),
            token=os.getenv("CONTROL_PLANE_TOKEN"),
        )
        self.guests: dict[int, dict[str, Any]] = {}
        self.selected_vmid: int | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="main"):
            yield Static("[ INFRASTRUCTURE ]", classes="section-title")
            yield SystemCard("PVE01", id="node-health")
            yield SystemCard("Controller", id="controller-health")
            yield SystemCard(
                "NAS",
                status="planned",
                id="infrastructure-planned",
                classes="planned",
            )
            yield Static("[ GUEST OPERATIONS ]", classes="section-title")
            yield DataTable(id="guest-table", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="actions"):
                yield Button("START", id="start", variant="success")
                yield Button("SHUTDOWN", id="shutdown", variant="warning")
                yield Button("REBOOT", id="reboot", variant="error")
                yield Button("REFRESH", id="refresh")
            yield Static("Select a guest to enable controls.", id="selection")
            yield Static("SYSTEM READY", id="task-status")
            yield Static("[ BACKUP AUTOMATION ]", classes="section-title")
            yield Static("Loading backup job...", id="backup-job")
            with Horizontal(id="backup-actions"):
                yield Button("RUN VM 104 BACKUP", id="backup-now")
            yield DataTable(id="backup-history", zebra_stripes=True)
            yield Static("[ RECENT ACTIVITY ]", classes="section-title")
            yield Static("No activity recorded yet.", id="recent-activity", classes="planned")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#guest-table", DataTable)
        table.add_columns("VMID", "NAME", "TYPE", "STATE", "NODE")
        self.query_one("#backup-history", DataTable).add_columns(
            "STARTED", "TRIGGER", "STATE", "VERIFY", "RESULT"
        )
        self._set_action_buttons(False)
        self.query_one("#backup-now", Button).disabled = True
        self.refresh_data()
        self.refresh_automation()
        self.set_interval(10.0, self.refresh_data)
        self.set_interval(2.0, self.refresh_automation)

    async def on_unmount(self) -> None:
        await self.client.close()

    def action_refresh(self) -> None:
        self.refresh_data()
        self.refresh_automation()

    @work(exclusive=True, group="automation-refresh")
    async def refresh_automation(self) -> None:
        panel = self.query_one("#backup-job", Static)
        button = self.query_one("#backup-now", Button)
        table = self.query_one("#backup-history", DataTable)
        try:
            jobs, executions = await self.client.get_automation()
        except ControlPlaneError as exc:
            panel.update(Text(f"BACKUP LINK FAILURE // {exc}", style="bold red"))
            button.disabled = True
            return
        job = next(
            (item for item in jobs if item.get("id") == "minecraft-vm-backup"),
            None,
        )
        if job is None:
            panel.update("MINECRAFT BACKUP // NOT CONFIGURED")
            button.disabled = True
            return
        state = "ENABLED" if job.get("enabled") else "DISABLED"
        panel.update(
            f"{state} // VMID {job.get('vmid')} // {job.get('node')} // "
            f"{job.get('storage')} // {job.get('schedule')} // CREATE+VERIFY ONLY"
        )
        active = any(
            item.get("state") in {"queued", "running", "verifying"}
            for item in executions
        )
        button.disabled = not job.get("enabled") or active
        table.clear()
        for item in executions[:10]:
            table.add_row(
                str(item.get("started_at") or item.get("queued_at") or "--"),
                str(item.get("trigger", "--")).upper(),
                str(item.get("state", "--")).upper(),
                str(item.get("verification", "--")).upper(),
                str(item.get("message", "--")),
            )

    @work(exclusive=True, group="refresh")
    async def refresh_data(self) -> None:
        health = self.query_one("#node-health", SystemCard)
        try:
            node, guests, controller, nas = await self.client.get_dashboard()
        except ControlPlaneError as exc:
            health.update(Text(f"PVE01  OFFLINE // {exc}", style="bold red"))
            self.query_one("#task-status", Static).update(f"LINK FAILURE: {exc}")
            return

        health.update_system(
            str(node["status"]),
            {
                "CPU": f"{node['cpu_percent']:.1f}%",
                "MEM": f"{node['memory_percent']:.1f}%",
                "DISK": f"{node['disk_percent']:.1f}%",
                "CORES": str(node["cpu_cores"]),
            },
        )
        self.query_one("#controller-health", SystemCard).update_system(
            str(controller["status"]),
            {
                "CPU": f"{controller['cpu_percent']:.1f}%",
                "MEM": f"{controller['memory_percent']:.1f}%",
                "DISK": f"{controller['disk_percent']:.1f}%",
                "UP": self._format_uptime(controller["uptime_seconds"]),
            },
        )
        self.query_one("#infrastructure-planned", SystemCard).update_system(
            str(nas["status"]),
            {
                "USED": self._format_bytes(nas["storage_used_bytes"]),
                "TOTAL": self._format_bytes(nas["storage_total_bytes"]),
                "FREE": self._format_bytes(nas["storage_free_bytes"]),
            },
        )

        self.guests = {int(guest["vmid"]): guest for guest in guests}
        table = self.query_one("#guest-table", DataTable)
        previous = self.selected_vmid
        table.clear()
        for guest in guests:
            protected = int(guest["vmid"]) == PROTECTED_VMID
            state = str(guest["status"]).upper()
            state_text = Text(state, style="#879965" if state == "RUNNING" else "#d1a34a")
            name = f"{guest['name']} [PROTECTED]" if protected else str(guest["name"])
            table.add_row(
                str(guest["vmid"]),
                name,
                str(guest["type"]).upper(),
                state_text,
                str(guest["node"]),
                key=str(guest["vmid"]),
            )
        if previous in self.guests:
            self._select_guest(previous)

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        days, remainder = divmod(int(seconds), 86400)
        hours, _ = divmod(remainder, 3600)
        return f"{days}D {hours}H" if days else f"{hours}H"

    @staticmethod
    def _format_bytes(value: int) -> str:
        tebibyte = 1024 ** 4
        gibibyte = 1024 ** 3
        if value >= tebibyte:
            return f"{value / tebibyte:.1f} TB"
        return f"{value / gibibyte:.1f} GB"

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        try:
            vmid = int(str(event.row_key.value))
        except (TypeError, ValueError):
            return
        self._select_guest(vmid)

    def _select_guest(self, vmid: int) -> None:
        guest = self.guests.get(vmid)
        if guest is None:
            return
        self.selected_vmid = vmid
        protected = vmid == PROTECTED_VMID
        self._set_action_buttons_for_guest(guest)
        suffix = " // POWER CONTROLS LOCKED" if protected else ""
        self.query_one("#selection", Static).update(
            f"TARGET {vmid} // {guest['name']} // {str(guest['status']).upper()}{suffix}"
        )

    def _set_action_buttons(self, enabled: bool) -> None:
        for button_id in ("start", "shutdown", "reboot"):
            self.query_one(f"#{button_id}", Button).disabled = not enabled

    def _set_action_buttons_for_guest(self, guest: dict[str, Any]) -> None:
        protected = int(guest["vmid"]) == PROTECTED_VMID
        running = str(guest["status"]).lower() == "running"
        self.query_one("#start", Button).disabled = protected or running
        self.query_one("#shutdown", Button).disabled = protected or not running
        self.query_one("#reboot", Button).disabled = protected or not running

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.refresh_data()
            self.refresh_automation()
        elif event.button.id == "backup-now":
            self.run_minecraft_backup()
        elif event.button.id in {"start", "shutdown", "reboot"}:
            self.run_power_action(event.button.id)

    @work(exclusive=True, group="backup-action")
    async def run_minecraft_backup(self) -> None:
        button = self.query_one("#backup-now", Button)
        status = self.query_one("#task-status", Static)
        button.disabled = True
        status.update("BACKUP REQUEST // VMID 104 // AWAITING CONTROL PLANE")
        try:
            execution_id = await self.client.run_minecraft_backup()
        except ControlPlaneError as exc:
            status.update(Text(f"BACKUP FAILED // {exc}", style="bold red"))
        else:
            status.update(f"BACKUP QUEUED // EXECUTION {execution_id[:12]}")
            self.refresh_automation()

    @work(exclusive=True, group="power-action")
    async def run_power_action(self, action: str) -> None:
        vmid = self.selected_vmid
        guest = self.guests.get(vmid) if vmid is not None else None
        status = self.query_one("#task-status", Static)
        if guest is None:
            status.update("NO TARGET SELECTED")
            return
        if vmid == PROTECTED_VMID:
            status.update("DENIED // VMID 100 CONTROL PLANE IS PROTECTED")
            self._set_action_buttons(False)
            return

        self._set_action_buttons(False)
        status.update(f"ACCEPTING {action.upper()} ON VMID {vmid} // AWAITING PROXMOX")

        def show_progress(elapsed: int, task_state: str) -> None:
            status.update(
                f"{action.upper()} ACCEPTED // VMID {vmid} // "
                f"TASK {task_state.upper()} // {elapsed}S ELAPSED"
            )

        try:
            result = await self.client.run_action(
                vmid,
                action,
                str(guest["node"]),
                progress=show_progress,
            )
        except ControlPlaneError as exc:
            status.update(Text(f"FAILED // {exc}", style="bold red"))
        else:
            status.update(
                Text(
                    f"SUCCESS // {result.action.upper()} VMID {result.vmid} // "
                    f"{result.exit_status} // {result.elapsed_seconds}S",
                    style="bold #879965",
                )
            )
            self.refresh_data()
        finally:
            current = self.guests.get(self.selected_vmid)
            if current is not None:
                self._set_action_buttons_for_guest(current)


def main() -> None:
    ControlPlaneApp().run()


if __name__ == "__main__":
    main()
