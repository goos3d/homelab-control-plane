import unittest
from unittest.mock import AsyncMock

from textual.widgets import Button, DataTable

from controlplane.ui.app import ControlPlaneApp


class ControlPlaneAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_api_address_defaults_to_localhost(self):
        app = ControlPlaneApp()
        self.assertEqual(app.client.base_url, "http://127.0.0.1:8000")
        await app.client.close()

    async def test_phase_two_layout_loads_normalized_dashboard(self):
        app = ControlPlaneApp("http://control-plane")
        app.client.get_dashboard = AsyncMock(
            return_value=(
                {
                    "name": "proxmox-node",
                    "status": "online",
                    "cpu_cores": 8,
                    "cpu_percent": 12.5,
                    "memory_percent": 25.0,
                    "disk_percent": 50.0,
                },
                [
                    {
                        "vmid": 100,
                        "name": "control-plane",
                        "type": "vm",
                        "status": "running",
                        "node": "proxmox-node",
                    }
                ],
                {
                    "name": "Controller",
                    "hostname": "controller.example.test",
                    "status": "online",
                    "api_status": "online",
                    "cpu_percent": 3.0,
                    "memory_percent": 31.0,
                    "disk_percent": 18.0,
                    "uptime_seconds": 90061,
                },
                {
                    "name": "NAS",
                    "status": "online",
                    "mount_path": "/mnt/nas",
                    "storage_used_bytes": 5 * 1024 ** 4,
                    "storage_total_bytes": 20 * 1024 ** 4,
                    "storage_free_bytes": 15 * 1024 ** 4,
                },
            )
        )
        app.client.close = AsyncMock()
        app.client.get_automation = AsyncMock(
            return_value=(
                [
                    {
                        "id": "minecraft-vm-backup",
                        "enabled": True,
                        "vmid": 104,
                        "node": "proxmox-node",
                        "storage": "backup-storage",
                        "schedule": "daily 03:00",
                    }
                ],
                [],
            )
        )

        async with app.run_test(size=(100, 45)) as pilot:
            await pilot.pause()

            table = app.query_one("#guest-table", DataTable)
            self.assertEqual(table.row_count, 1)
            self.assertIn("ONLINE", str(app.query_one("#node-health").render()))
            self.assertIn("ONLINE", str(app.query_one("#controller-health").render()))
            self.assertIn(
                "ONLINE",
                str(app.query_one("#infrastructure-planned").render()),
            )
            self.assertIsNotNone(app.query_one("#recent-activity"))
            self.assertIn("VMID 104", str(app.query_one("#backup-job").render()))
            self.assertFalse(app.query_one("#backup-now", Button).disabled)
