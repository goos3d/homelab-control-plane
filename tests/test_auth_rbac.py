import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from controlplane.api import server
from controlplane.core.models import GuestStatus
from controlplane.core.security import AuthService, UserStore


def guest(vmid: int, name: str, status: str) -> GuestStatus:
    return GuestStatus(
        vmid=vmid,
        name=name,
        type="vm",
        status=status,
        node="proxmox-node",
    )


class AuthenticationAndRbacTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.user_file = Path(cls.temporary.name) / "users.json"
        cls.store = UserStore(cls.user_file)
        cls.store.save_user("admin-user", "correct horse admin", "admin")
        cls.store.save_user("standard-user", "correct horse user!", "user")
        cls.store.save_user("guest-user", "correct horse guest", "guest")
        cls.auth = AuthService(cls.store, "test-secret-that-is-at-least-32-bytes-long")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        server._auth_service = self.auth

    def tearDown(self):
        server.app.dependency_overrides.clear()
        server._auth_service = None

    def login(self, username: str, password: str) -> tuple[TestClient, str]:
        client = TestClient(server.app)
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200)
        return client, response.json()["csrf_token"]

    def test_passwords_are_scrypt_hashed_and_cookie_is_http_only(self):
        contents = self.user_file.read_text(encoding="utf-8")
        self.assertNotIn("correct horse admin", contents)
        self.assertIn("scrypt$v=1$", contents)
        client = TestClient(server.app)
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin-user", "password": "correct horse admin"},
        )
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)

    def test_invalid_login_uses_generic_error(self):
        response = TestClient(server.app).post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "wrong password"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid username or password")

    def test_unauthenticated_requests_are_rejected(self):
        response = TestClient(server.app).get("/api/v1/minecraft/servers")
        self.assertEqual(response.status_code, 401)

    def test_guest_can_read_minecraft_but_cannot_issue_commands(self):
        client, csrf = self.login("guest-user", "correct horse guest")
        guests = [guest(104, "minecraft-server-104", "stopped"), guest(105, "minecraft-server-105", "running")]
        with patch.object(server.pve, "get_guests", return_value=guests):
            read = client.get("/api/v1/minecraft/servers")
        self.assertEqual(read.status_code, 200)
        self.assertEqual([item["vmid"] for item in read.json()["servers"]], [104, 105, None])

        with patch.object(server.pve, "guest_action") as action:
            command = client.post(
                "/api/v1/minecraft/servers/105/stop",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(command.status_code, 403)
        action.assert_not_called()

    def test_user_has_no_generic_proxmox_access(self):
        client, _ = self.login("standard-user", "correct horse user!")
        with patch.object(server.pve, "get_dashboard") as dashboard:
            response = client.get("/api/v1/dashboard")
        self.assertEqual(response.status_code, 403)
        dashboard.assert_not_called()

    def test_cookie_commands_require_csrf_token(self):
        client, _ = self.login("standard-user", "correct horse user!")
        with patch.object(server.pve, "guest_action") as action:
            response = client.post("/api/v1/minecraft/servers/105/stop")
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", response.json()["detail"])
        action.assert_not_called()

    def test_user_cannot_control_unapproved_vm(self):
        client, csrf = self.login("standard-user", "correct horse user!")
        with patch.object(server.pve, "guest_action") as action:
            response = client.post(
                "/api/v1/minecraft/servers/106/start",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 404)
        action.assert_not_called()

    def test_running_peer_blocks_user_start(self):
        client, csrf = self.login("standard-user", "correct horse user!")
        guests = [guest(104, "minecraft-server-104", "running"), guest(105, "minecraft-server-105", "stopped")]
        with (
            patch.object(server.pve, "get_guests", return_value=guests),
            patch.object(server.pve, "guest_action") as action,
        ):
            response = client.post(
                "/api/v1/minecraft/servers/105/start",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("Minecraft VM 104 is currently running", response.json()["detail"])
        action.assert_not_called()

    def test_peer_must_be_confirmed_stopped_before_user_start(self):
        client, csrf = self.login("standard-user", "correct horse user!")
        guests = [guest(104, "minecraft-server-104", "stopping"), guest(105, "minecraft-server-105", "stopped")]
        with (
            patch.object(server.pve, "get_guests", return_value=guests),
            patch.object(server.pve, "guest_action") as action,
        ):
            response = client.post(
                "/api/v1/minecraft/servers/105/start",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("not yet confirmed stopped", response.json()["detail"])
        action.assert_not_called()

    def test_user_start_succeeds_when_peer_is_stopped(self):
        client, csrf = self.login("standard-user", "correct horse user!")
        guests = [guest(104, "minecraft-server-104", "stopped"), guest(105, "minecraft-server-105", "stopped")]
        with (
            patch.object(server.pve, "get_guests", return_value=guests),
            patch.object(server.pve, "guest_action", return_value="UPID:test") as action,
        ):
            response = client.post(
                "/api/v1/minecraft/servers/105/start",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 200)
        action.assert_called_once_with(105, "start")

    def test_admin_can_override_exclusive_group(self):
        client, csrf = self.login("admin-user", "correct horse admin")
        with (
            patch.object(server.pve, "get_guests") as statuses,
            patch.object(server.pve, "guest_action", return_value="UPID:test") as action,
        ):
            response = client.post(
                "/api/v1/minecraft/servers/105/start",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 200)
        statuses.assert_not_called()
        action.assert_called_once_with(105, "start")


if __name__ == "__main__":
    unittest.main()
