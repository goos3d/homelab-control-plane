import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from types import ModuleType
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from controlplane.api import server
from controlplane.core.security import AuthService, UserStore
from controlplane.services.wonton26 import (
    InvalidMinecraftUsernameError,
    PlayerAlreadyWhitelistedError,
    PlayerNotWhitelistedError,
    ProtectedPlayerError,
    Wonton26ConfigurationError,
    Wonton26ProtocolError,
    Wonton26TimeoutError,
    Wonton26UnavailableError,
    MctoolsWhitelistTransport,
    Wonton26RconConfiguration,
    Wonton26WhitelistService,
)


class FakeTransport:
    def __init__(
        self,
        *,
        listed="There are 0 whitelisted player(s):",
        added="Added PlayerExample to the whitelist",
        removed="Removed PlayerExample from the whitelist",
    ):
        self.listed = listed
        self.added = added
        self.removed = removed
        self.calls = []

    def list_whitelist(self):
        self.calls.append(("list",))
        return self.listed

    def add_whitelist_player(self, username):
        self.calls.append(("add", username))
        return self.added

    def remove_whitelist_player(self, username):
        self.calls.append(("remove", username))
        return self.removed


class FakeRconAuthenticationError(Exception):
    pass


class FakeRconMalformedPacketError(Exception):
    pass


class FakeRconClient:
    instances = []
    login_result = True
    login_error = None
    command_result = "There are 0 whitelisted player(s):"
    command_error = None

    def __init__(self, host, *, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.timeouts = []
        self.commands = []
        self.login_passwords = []
        self.stopped = False
        self.__class__.instances.append(self)

    def login(self, _password):
        self.login_passwords.append(_password)
        if self.__class__.login_error:
            raise self.__class__.login_error
        return self.__class__.login_result

    def set_timeout(self, timeout):
        self.timeouts.append(timeout)

    def command(self, command):
        self.commands.append(command)
        if self.__class__.command_error:
            raise self.__class__.command_error
        return self.__class__.command_result

    def stop(self):
        self.stopped = True


class BlockingRconClient(FakeRconClient):
    released = Event()

    def command(self, command):
        self.commands.append(command)
        self.__class__.released.wait(1)
        return self.__class__.command_result

    def stop(self):
        self.stopped = True
        self.__class__.released.set()


def mocked_mctools(client_type):
    package = ModuleType("mctools")
    package.RCONClient = client_type
    errors = ModuleType("mctools.errors")
    errors.RCONAuthenticationError = FakeRconAuthenticationError
    errors.RCONMalformedPacketError = FakeRconMalformedPacketError
    return patch.dict(sys.modules, {"mctools": package, "mctools.errors": errors})


class Wonton26WhitelistServiceTests(unittest.TestCase):
    def tearDown(self):
        self.environment.stop()

    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "WONTON26_RCON_HOST": "test-minecraft-server-105",
                "WONTON26_RCON_PORT": "25575",
                "WONTON26_RCON_PASSWORD": "test-password-not-production",
                "WONTON26_PROTECTED_PLAYERS": "ProtectedPlayer,AdminExample",
            },
            clear=False,
        )
        self.environment.start()
        self.transport = FakeTransport()
        self.service = Wonton26WhitelistService(lambda _configuration: self.transport)
        FakeRconClient.instances = []
        FakeRconClient.login_result = True
        FakeRconClient.login_error = None
        FakeRconClient.command_result = "There are 0 whitelisted player(s):"
        FakeRconClient.command_error = None
        BlockingRconClient.instances = []
        BlockingRconClient.released = Event()

    def test_list_parses_players_and_protection(self):
        self.transport.listed = (
            "There are 3 whitelisted player(s): ProtectedPlayer, AdminExample, MemberExample"
        )

        self.assertEqual(
            self.service.get_whitelist(),
            [
                {"username": "ProtectedPlayer", "protected": True},
                {"username": "AdminExample", "protected": True},
                {"username": "MemberExample", "protected": False},
            ],
        )
        self.assertEqual(self.transport.calls, [("list",)])

    def test_list_strips_ansi_sequences_from_real_server_response(self):
        self.transport.listed = (
            "There are 6 whitelisted player(s): PlayerAlpha, PlayerBeta, PlayerGamma, "
            "ProtectedPlayer, PlayerDelta, PlayerEpsilon\n\x1b[0m"
        )

        self.assertEqual(
            self.service.get_whitelist(),
            [
                {"username": "PlayerAlpha", "protected": False},
                {"username": "PlayerBeta", "protected": False},
                {"username": "PlayerGamma", "protected": False},
                {"username": "ProtectedPlayer", "protected": True},
                {"username": "PlayerDelta", "protected": False},
                {"username": "PlayerEpsilon", "protected": False},
            ],
        )
        self.assertEqual(self.transport.calls, [("list",)])

    def test_add_and_remove_issue_only_fixed_validated_operations(self):
        self.transport.added = "Added PlayerExample to the whitelist"
        self.transport.removed = "Removed PlayerExample from the whitelist"

        self.service.add_whitelist_player("PlayerExample")
        self.service.remove_whitelist_player("PlayerExample")

        self.assertEqual(
            self.transport.calls,
            [("add", "PlayerExample"), ("remove", "PlayerExample")],
        )

    def test_invalid_usernames_are_rejected_before_transport(self):
        for username in ("ab", "a" * 17, "name space", "name;stop", "na/me", "naïve"):
            with self.assertRaises(InvalidMinecraftUsernameError):
                self.service.add_whitelist_player(username)
        self.assertEqual(self.transport.calls, [])

    def test_duplicate_and_absent_players_are_reported(self):
        self.transport.added = "That player is already whitelisted"
        with self.assertRaises(PlayerAlreadyWhitelistedError):
            self.service.add_whitelist_player("PlayerExample")

        self.transport.removed = "That player is not whitelisted"
        with self.assertRaises(PlayerNotWhitelistedError):
            self.service.remove_whitelist_player("PlayerExample")

    def test_configured_protected_player_is_blocked_before_transport(self):
        with self.assertRaises(ProtectedPlayerError):
            self.service.remove_whitelist_player("PROTECTEDPLAYER")
        self.assertEqual(self.transport.calls, [])

    def test_unexpected_responses_are_rejected(self):
        self.transport.listed = "not a whitelist response"
        with self.assertRaises(Wonton26ProtocolError):
            self.service.get_whitelist()

        self.transport.added = "Added UnexpectedPlayer to the whitelist"
        with self.assertRaises(Wonton26ProtocolError):
            self.service.add_whitelist_player("PlayerExample")

    def test_invalid_protected_player_configuration_fails_closed(self):
        with patch.dict(
            os.environ,
            {"WONTON26_PROTECTED_PLAYERS": "ProtectedPlayer,bad player"},
        ):
            with self.assertRaises(Wonton26ConfigurationError):
                self.service.get_whitelist()
        self.assertEqual(self.transport.calls, [])

    def test_missing_protected_player_configuration_fails_closed(self):
        with patch.dict(os.environ, {"WONTON26_PROTECTED_PLAYERS": ""}):
            with self.assertRaises(Wonton26ConfigurationError):
                self.service.get_whitelist()
            with self.assertRaises(Wonton26ConfigurationError):
                self.service.add_whitelist_player("PlayerExample")
            with self.assertRaises(Wonton26ConfigurationError):
                self.service.remove_whitelist_player("PlayerExample")
        self.assertEqual(self.transport.calls, [])

    def test_transport_errors_are_preserved_for_safe_api_mapping(self):
        for error in (
            Wonton26UnavailableError("unavailable"),
            Wonton26TimeoutError("timeout"),
            Wonton26ConfigurationError("authentication failed"),
        ):
            transport = Mock()
            transport.list_whitelist.side_effect = error
            service = Wonton26WhitelistService(lambda _configuration: transport)
            with self.assertRaises(type(error)):
                service.get_whitelist()

    def test_mctools_transport_uses_bounded_client_lifecycle(self):
        configuration = Wonton26RconConfiguration("test-minecraft-server-105", 25575, "not-a-real-password")
        with mocked_mctools(FakeRconClient):
            response = MctoolsWhitelistTransport(configuration).list_whitelist()
        client = FakeRconClient.instances[0]
        self.assertEqual(response, "There are 0 whitelisted player(s):")
        self.assertEqual((client.host, client.port, client.timeout), ("test-minecraft-server-105", 25575, 3))
        self.assertEqual(client.timeouts, [5])
        self.assertEqual(len(client.login_passwords), 1)
        self.assertEqual(client.commands, ["whitelist list"])
        self.assertTrue(client.stopped)

    def test_mctools_transport_classifies_auth_timeout_and_protocol_failures(self):
        configuration = Wonton26RconConfiguration("test-minecraft-server-105", 25575, "not-a-real-password")
        cases = [
            ("login_result", False, Wonton26ConfigurationError),
            ("command_error", socket.timeout(), Wonton26TimeoutError),
            ("command_error", FakeRconMalformedPacketError(), Wonton26ProtocolError),
            ("command_error", OSError("connection refused"), Wonton26UnavailableError),
        ]
        for attribute, value, error_type in cases:
            with self.subTest(error_type=error_type):
                setattr(FakeRconClient, attribute, value)
                with mocked_mctools(FakeRconClient):
                    with self.assertRaises(error_type):
                        MctoolsWhitelistTransport(configuration).list_whitelist()
                self.assertTrue(FakeRconClient.instances[-1].stopped)
                setattr(FakeRconClient, attribute, None if attribute == "command_error" else True)

    def test_mctools_transport_enforces_operation_deadline_and_stops_client(self):
        configuration = Wonton26RconConfiguration("test-minecraft-server-105", 25575, "not-a-real-password")
        transport = MctoolsWhitelistTransport(configuration)
        transport.OPERATION_TIMEOUT_SECONDS = 0.01
        started = time.monotonic()
        with mocked_mctools(BlockingRconClient):
            with self.assertRaises(Wonton26TimeoutError):
                transport.list_whitelist()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(BlockingRconClient.instances[0].stopped)


class Wonton26WhitelistApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        store = UserStore(Path(cls.temporary.name) / "users.json")
        store.save_user("admin", "correct horse admin", "admin")
        store.save_user("manager", "correct horse manager", "minecraft_manager")
        store.save_user("user", "correct horse ordinary", "user")
        store.save_user("guest", "correct horse guest", "guest")
        cls.auth = AuthService(store, "test-secret-that-is-at-least-32-bytes-long")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.environment = patch.dict(os.environ, {"PVE_NODE": "proxmox-node"})
        self.environment.start()
        server._auth_service = self.auth
        self.client = TestClient(server.app)

    def tearDown(self):
        server._auth_service = None
        server.app.dependency_overrides.clear()
        self.environment.stop()

    def login(self, username, password):
        response = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf_token"]

    def test_unauthenticated_and_unprivileged_users_are_rejected(self):
        self.assertEqual(self.client.get("/api/v1/minecraft/wonton26/whitelist").status_code, 401)
        for username, password in (("guest", "correct horse guest"), ("user", "correct horse ordinary")):
            csrf = self.login(username, password)
            with patch.object(server.wonton26, "add_whitelist_player") as add:
                response = self.client.post(
                    "/api/v1/minecraft/wonton26/whitelist/players",
                    json={"username": "PlayerExample"},
                    headers={"X-CSRF-Token": csrf},
                )
            self.assertEqual(response.status_code, 403)
            add.assert_not_called()
            self.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

    def test_manager_and_admin_are_allowed(self):
        for username, password in (("manager", "correct horse manager"), ("admin", "correct horse admin")):
            csrf = self.login(username, password)
            with patch.object(server.wonton26, "get_whitelist", return_value=[]) as whitelist:
                response = self.client.get("/api/v1/minecraft/wonton26/whitelist")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["server"], "Wonton26")
            whitelist.assert_called_once_with()
            self.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

    def test_manager_retains_minecraft_controls_without_proxmox_access(self):
        csrf = self.login("manager", "correct horse manager")
        with patch.object(server.pve, "guest_action", return_value="UPID:test") as action:
            response = self.client.post(
                "/api/v1/minecraft/servers/105/stop",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 200)
        action.assert_called_once_with(105, "shutdown")

        with patch.object(server.pve, "get_dashboard") as dashboard:
            response = self.client.get("/api/v1/dashboard")
        self.assertEqual(response.status_code, 403)
        dashboard.assert_not_called()

        with patch.object(server.pve, "get_task_status", return_value={"status": "running"}) as task:
            response = self.client.get("/api/v1/minecraft/tasks/proxmox-node/UPID:test")
        self.assertEqual(response.status_code, 200)
        task.assert_called_once_with("proxmox-node", "UPID:test")

    def test_task_polling_rejects_unknown_tasks_and_wrong_nodes(self):
        csrf = self.login("manager", "correct horse manager")
        with patch.object(server.pve, "get_task_status") as task:
            unknown = self.client.get("/api/v1/minecraft/tasks/proxmox-node/UPID:unknown")
            wrong_node = self.client.get("/api/v1/minecraft/tasks/pve02/UPID:unknown")
        self.assertEqual(unknown.status_code, 403)
        self.assertEqual(wrong_node.status_code, 403)
        task.assert_not_called()

        with patch.object(server.pve, "guest_action", return_value="UPID:manager-task"):
            accepted = self.client.post(
                "/api/v1/minecraft/servers/105/stop",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(accepted.status_code, 200)

        with patch.object(server.pve, "get_task_status", return_value={"status": "running"}) as task:
            known = self.client.get("/api/v1/minecraft/tasks/proxmox-node/UPID:manager-task")
            known_wrong_node = self.client.get("/api/v1/minecraft/tasks/pve02/UPID:manager-task")
        self.assertEqual(known.status_code, 200)
        self.assertEqual(known_wrong_node.status_code, 403)
        task.assert_called_once_with("proxmox-node", "UPID:manager-task")

    def test_admin_can_poll_known_minecraft_task_from_another_user(self):
        manager_csrf = self.login("manager", "correct horse manager")
        with patch.object(server.pve, "guest_action", return_value="UPID:shared-task"):
            response = self.client.post(
                "/api/v1/minecraft/servers/105/stop",
                headers={"X-CSRF-Token": manager_csrf},
            )
        self.assertEqual(response.status_code, 200)
        self.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": manager_csrf})

        self.login("admin", "correct horse admin")
        with patch.object(server.pve, "get_task_status", return_value={"status": "running"}) as task:
            response = self.client.get("/api/v1/minecraft/tasks/proxmox-node/UPID:shared-task")
            unknown = self.client.get("/api/v1/minecraft/tasks/proxmox-node/UPID:admin-unknown")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(unknown.status_code, 403)
        task.assert_called_once_with("proxmox-node", "UPID:shared-task")


    def test_mutations_require_csrf_and_reject_malformed_input(self):
        csrf = self.login("manager", "correct horse manager")
        with patch.object(server.wonton26, "add_whitelist_player") as add:
            missing_csrf = self.client.post(
                "/api/v1/minecraft/wonton26/whitelist/players",
                json={"username": "PlayerExample"},
            )
            malformed = self.client.post(
                "/api/v1/minecraft/wonton26/whitelist/players",
                json={"username": "bad name", "command": "say no"},
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(malformed.status_code, 422)
        add.assert_not_called()

    def test_api_success_and_safe_error_mappings(self):
        csrf = self.login("manager", "correct horse manager")
        with patch.object(server.wonton26, "add_whitelist_player") as add:
            response = self.client.post(
                "/api/v1/minecraft/wonton26/whitelist/players",
                json={"username": "PlayerExample"},
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 201)
        add.assert_called_once_with("PlayerExample")

        cases = [
            ("add_whitelist_player", PlayerAlreadyWhitelistedError("Player is already whitelisted"), "POST", "/api/v1/minecraft/wonton26/whitelist/players", 409),
            ("remove_whitelist_player", PlayerNotWhitelistedError("Player is not whitelisted"), "DELETE", "/api/v1/minecraft/wonton26/whitelist/players/PlayerOne", 409),
            ("remove_whitelist_player", ProtectedPlayerError("protected"), "DELETE", "/api/v1/minecraft/wonton26/whitelist/players/ProtectedPlayer", 403),
            ("get_whitelist", Wonton26UnavailableError("refused"), "GET", "/api/v1/minecraft/wonton26/whitelist", 503),
            ("get_whitelist", Wonton26TimeoutError("timeout"), "GET", "/api/v1/minecraft/wonton26/whitelist", 504),
            ("get_whitelist", Wonton26ConfigurationError("auth"), "GET", "/api/v1/minecraft/wonton26/whitelist", 503),
            ("get_whitelist", Wonton26ProtocolError("bad"), "GET", "/api/v1/minecraft/wonton26/whitelist", 502),
        ]
        for method_name, error, method, path, expected_status in cases:
            with patch.object(server.wonton26, method_name, side_effect=error):
                response = self.client.request(
                    method,
                    path,
                    json={"username": "PlayerExample"} if method == "POST" else None,
                    headers={"X-CSRF-Token": csrf} if method != "GET" else {},
                )
            self.assertEqual(response.status_code, expected_status)
            self.assertNotIn("refused", response.text)

    def test_protected_path_username_is_rejected_before_service(self):
        csrf = self.login("manager", "correct horse manager")
        with patch.object(server.wonton26, "remove_whitelist_player") as remove:
            response = self.client.delete(
                "/api/v1/minecraft/wonton26/whitelist/players/no%20spaces",
                headers={"X-CSRF-Token": csrf},
            )
        self.assertEqual(response.status_code, 422)
        remove.assert_not_called()


class UserStoreRoleTests(unittest.TestCase):
    def test_set_role_persists_without_resetting_password_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserStore(Path(directory) / "users.json")
            before = store.save_user("standard-user", "correct horse manager", "user")
            updated = store.set_role("standard-user", "minecraft_manager")
            persisted = store.get("standard-user")

        self.assertEqual(updated.role, "minecraft_manager")
        self.assertEqual(updated.password_hash, before.password_hash)
        self.assertEqual(persisted.role, "minecraft_manager")
        self.assertEqual(persisted.password_hash, before.password_hash)

    def test_set_role_rejects_unknown_users_and_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            store = UserStore(Path(directory) / "users.json")
            store.save_user("standard-user", "correct horse manager", "user")
            with self.assertRaises(ValueError):
                store.set_role("missing", "minecraft_manager")
            with self.assertRaises(ValueError):
                store.set_role("standard-user", "adminish")


if __name__ == "__main__":
    unittest.main()
