import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from controlplane.services.automation import AutomationService, ExecutionHistory


class FakeProxmox:
    def __init__(self, *, artifact=True, exit_status="OK"):
        self.artifact = artifact
        self.exit_status = exit_status
        self.calls = []
        self.status_checks = 0

    def start_guest_backup(self, **kwargs):
        self.calls.append(("start_guest_backup", kwargs))
        return "UPID:proxmox-node:test:"

    def get_task_status(self, node, upid):
        self.calls.append(("get_task_status", {"node": node, "upid": upid}))
        self.status_checks += 1
        if self.status_checks == 1:
            return {"status": "running"}
        return {"status": "stopped", "exitstatus": self.exit_status}

    def get_backup_artifacts(self, **kwargs):
        self.calls.append(("get_backup_artifacts", kwargs))
        if not self.artifact:
            return []
        return [
            {
                "volid": "backup-storage:backup/vzdump-qemu-104-test.vma.zst",
                "ctime": time.time(),
                "size": 3_580_000_000,
            }
        ]


class AutomationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.history = ExecutionHistory(Path(self.temp.name) / "automation.jsonl")
        self.environment = patch.dict(
            "os.environ",
            {
                "PVE_BACKUP_NODE": "proxmox-node",
                "PVE_BACKUP_VMID": "104",
                "PVE_BACKUP_STORAGE": "backup-storage",
                "PVE_BACKUP_TIME": "03:00",
                "PVE_BACKUP_ENABLED": "1",
            },
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def service(self, proxmox=None):
        return AutomationService(
            proxmox or FakeProxmox(),
            history=self.history,
            poll_seconds=0,
            timeout_seconds=1,
        )

    def test_job_is_fixed_to_minecraft_and_create_verify_only(self):
        service = self.service()
        job = service.job()
        self.assertEqual(
            (job.node, job.vmid, job.storage),
            ("proxmox-node", 104, "backup-storage"),
        )
        self.assertEqual(job.permissions, "create-and-verify-only")

    def test_full_backup_flow_tracks_task_verifies_and_audits(self):
        proxmox = FakeProxmox()
        service = self.service(proxmox)
        execution = service.submit(trigger="test")
        service.stop()

        current = self.history.records()[0]
        self.assertEqual(current.id, execution.id)
        self.assertEqual(current.state, "succeeded")
        self.assertEqual(current.verification, "passed")
        self.assertEqual(current.result["size_bytes"], 3_580_000_000)
        self.assertEqual(
            [name for name, _ in proxmox.calls],
            [
                "start_guest_backup",
                "get_task_status",
                "get_task_status",
                "get_backup_artifacts",
            ],
        )
        with self.history.path.open(encoding="utf-8") as source:
            states = [json.loads(line)["state"] for line in source]
        self.assertEqual(states, ["queued", "running", "verifying", "succeeded"])

    def test_missing_artifact_fails_verification(self):
        service = self.service(FakeProxmox(artifact=False))
        service.submit(trigger="test")
        service.stop()
        current = self.history.records()[0]
        self.assertEqual(current.state, "failed")
        self.assertEqual(current.verification, "failed")

    def test_scheduler_runs_once_at_scheduled_minute_without_catch_up(self):
        service = self.service()
        self.assertFalse(service.tick(datetime(2026, 9, 12, 12, 0)))
        self.assertTrue(service.tick(datetime(2026, 9, 12, 3, 0)))
        self.assertFalse(service.tick(datetime(2026, 9, 12, 3, 0, 30)))
        service.stop()

    def test_disabled_job_never_touches_proxmox(self):
        with patch.dict("os.environ", {"PVE_BACKUP_ENABLED": "0"}):
            proxmox = FakeProxmox()
            service = AutomationService(proxmox, history=self.history)
        with self.assertRaisesRegex(ValueError, "disabled"):
            service.submit()
        self.assertFalse(service.tick(datetime(2026, 9, 12, 3, 0)))
        self.assertEqual(proxmox.calls, [])
        service.stop()

    def test_enabled_job_requires_node_and_storage(self):
        with patch.dict(
            "os.environ",
            {
                "PVE_BACKUP_ENABLED": "1",
                "PVE_BACKUP_TIME": "03:00",
                "PVE_BACKUP_STORAGE": "backup-storage",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "PVE_BACKUP_NODE"):
                AutomationService(FakeProxmox(), history=self.history)

    def test_backup_node_falls_back_to_configured_dashboard_node(self):
        with patch.dict(
            "os.environ",
            {
                "PVE_BACKUP_ENABLED": "1",
                "PVE_BACKUP_TIME": "03:00",
                "PVE_NODE": "proxmox-node",
                "PVE_BACKUP_STORAGE": "backup-storage",
            },
            clear=True,
        ):
            service = AutomationService(FakeProxmox(), history=self.history)

        self.assertEqual(service.node, "proxmox-node")
        service.stop()


if __name__ == "__main__":
    unittest.main()
