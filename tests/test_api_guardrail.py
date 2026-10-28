import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from controlplane.api.server import app, authenticated_user
from controlplane.core.security import User


class ApiGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.admin = User("admin-user", "admin", "test-hash")
        app.dependency_overrides[authenticated_user] = lambda: self.admin

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_wonton26_power_action_uses_existing_proxmox_path(self):
        task_id = "UPID:proxmox-node:00000001:00000002:00000003:qmshutdown:105:root@pam:"
        with patch(
            "controlplane.api.server.pve.guest_action",
            return_value=task_id,
        ) as action:
            response = TestClient(app).post(
                "/api/v1/proxmox/guests/105/shutdown"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "accepted": True,
                "vmid": 105,
                "action": "shutdown",
                "task": task_id,
            },
        )
        action.assert_called_once_with(105, "shutdown")
    def test_vmid_100_is_rejected_before_proxmox_call(self):
        with patch("controlplane.api.server.pve.guest_action") as action:
            response = TestClient(app).post(
                "/api/v1/proxmox/guests/100/shutdown"
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("protected", response.json()["detail"])
        action.assert_not_called()

    def test_disabled_backup_job_is_not_submitted(self):
        with (
            patch("controlplane.api.server.automation.enabled", False),
            patch("controlplane.api.server.automation._pool.submit") as submit,
        ):
            response = TestClient(app).post(
                "/api/v1/automation/jobs/minecraft-vm-backup/run"
            )
        self.assertEqual(response.status_code, 409)
        submit.assert_not_called()
