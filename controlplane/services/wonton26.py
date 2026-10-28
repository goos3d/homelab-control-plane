from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Callable, Protocol


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,16}$")
_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-_][0-?]*[ -/]*[@-~]|\[[0-?]*[ -/]*[@-~])")
_WHITELIST_RESPONSE = re.compile(
    r"^There are (?P<count>\d+) whitelisted player\(s\):(?: (?P<names>[A-Za-z0-9_, ]+))?$"
)
_ADDED_RESPONSE = re.compile(r"^Added (?P<username>[A-Za-z0-9_]{3,16}) to the whitelist$")
_REMOVED_RESPONSE = re.compile(r"^Removed (?P<username>[A-Za-z0-9_]{3,16}) from the whitelist$")


class Wonton26WhitelistError(RuntimeError):
    """Base error for safe Wonton26 whitelist failures."""


class Wonton26ConfigurationError(Wonton26WhitelistError):
    pass


class Wonton26UnavailableError(Wonton26WhitelistError):
    pass


class Wonton26TimeoutError(Wonton26WhitelistError):
    pass


class Wonton26ProtocolError(Wonton26WhitelistError):
    pass


class InvalidMinecraftUsernameError(Wonton26WhitelistError):
    pass


class ProtectedPlayerError(Wonton26WhitelistError):
    pass


class PlayerAlreadyWhitelistedError(Wonton26WhitelistError):
    pass


class PlayerNotWhitelistedError(Wonton26WhitelistError):
    pass


def validate_minecraft_username(username: str) -> str:
    if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
        raise InvalidMinecraftUsernameError("Invalid Minecraft Java username")
    return username


@dataclass(frozen=True)
class Wonton26RconConfiguration:
    host: str
    port: int
    password: str

    @classmethod
    def from_environment(cls) -> "Wonton26RconConfiguration":
        host = os.getenv("WONTON26_RCON_HOST", "").strip()
        password = os.getenv("WONTON26_RCON_PASSWORD", "")
        port_text = os.getenv("WONTON26_RCON_PORT", "").strip()
        if not host or not password or not port_text:
            raise Wonton26ConfigurationError("Wonton26 RCON is not configured")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise Wonton26ConfigurationError("Wonton26 RCON is not configured") from exc
        if not 1 <= port <= 65535:
            raise Wonton26ConfigurationError("Wonton26 RCON is not configured")
        return cls(host=host, port=port, password=password)


class WhitelistTransport(Protocol):
    def list_whitelist(self) -> str: ...

    def add_whitelist_player(self, username: str) -> str: ...

    def remove_whitelist_player(self, username: str) -> str: ...


class MctoolsWhitelistTransport:
    """Private adapter for the only RCON operations this application permits."""

    OPERATION_TIMEOUT_SECONDS = 9

    def __init__(self, configuration: Wonton26RconConfiguration):
        self.configuration = configuration
        self._client_lock = Lock()
        self._client = None

    def _send_fixed(self, command: str) -> str:
        try:
            from mctools import RCONClient
            from mctools.errors import RCONAuthenticationError, RCONMalformedPacketError
        except ImportError as exc:
            raise Wonton26ConfigurationError("Wonton26 RCON support is unavailable") from exc

        client = None
        try:
            client = RCONClient(
                self.configuration.host,
                port=self.configuration.port,
                timeout=3,
            )
            with self._client_lock:
                self._client = client
            if not client.login(self.configuration.password):
                raise Wonton26ConfigurationError("Wonton26 RCON authentication failed")
            client.set_timeout(5)
            response = client.command(command)
            if not isinstance(response, str):
                raise Wonton26ProtocolError("Unexpected Wonton26 RCON response")
            return response.strip()
        except Wonton26WhitelistError:
            raise
        except RCONAuthenticationError as exc:
            raise Wonton26ConfigurationError("Wonton26 RCON authentication failed") from exc
        except RCONMalformedPacketError as exc:
            raise Wonton26ProtocolError("Unexpected Wonton26 RCON response") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise Wonton26TimeoutError("Wonton26 RCON timed out") from exc
        except OSError as exc:
            raise Wonton26UnavailableError("Wonton26 RCON is unavailable") from exc
        except Exception as exc:
            if "auth" in type(exc).__name__.lower():
                raise Wonton26ConfigurationError("Wonton26 RCON authentication failed") from exc
            if "timeout" in type(exc).__name__.lower():
                raise Wonton26TimeoutError("Wonton26 RCON timed out") from exc
            raise Wonton26UnavailableError("Wonton26 RCON is unavailable") from exc
        finally:
            self._stop_client(client)

    def _stop_client(self, expected_client=None) -> None:
        with self._client_lock:
            client = self._client
            if client is None or (expected_client is not None and client is not expected_client):
                return
            self._client = None
        try:
            client.stop()
        except Exception:
            pass

    def _run_fixed_with_deadline(self, command: str) -> str:
        result: Queue[tuple[bool, object]] = Queue(maxsize=1)

        def run() -> None:
            try:
                result.put((True, self._send_fixed(command)))
            except Exception as exc:
                result.put((False, exc))

        worker = Thread(target=run, daemon=True)
        worker.start()
        worker.join(self.OPERATION_TIMEOUT_SECONDS)
        if worker.is_alive():
            self._stop_client()
            raise Wonton26TimeoutError("Wonton26 RCON timed out")
        try:
            success, value = result.get_nowait()
        except Empty as exc:
            raise Wonton26UnavailableError("Wonton26 RCON is unavailable") from exc
        if success:
            return str(value)
        raise value

    def list_whitelist(self) -> str:
        return self._run_fixed_with_deadline("whitelist list")

    def add_whitelist_player(self, username: str) -> str:
        return self._run_fixed_with_deadline(f"whitelist add {username}")

    def remove_whitelist_player(self, username: str) -> str:
        return self._run_fixed_with_deadline(f"whitelist remove {username}")


class Wonton26WhitelistService:
    def __init__(
        self,
        transport_factory: Callable[[Wonton26RconConfiguration], WhitelistTransport] = MctoolsWhitelistTransport,
    ):
        self._transport_factory = transport_factory
        self._mutation_lock = Lock()

    @staticmethod
    def _protected_players() -> frozenset[str]:
        configured = os.getenv("WONTON26_PROTECTED_PLAYERS", "")
        players = set()
        for player in configured.split(","):
            candidate = player.strip()
            if not candidate:
                continue
            try:
                players.add(validate_minecraft_username(candidate).casefold())
            except InvalidMinecraftUsernameError as exc:
                raise Wonton26ConfigurationError(
                    "Wonton26 protected-player configuration is invalid"
                ) from exc
        if not players:
            raise Wonton26ConfigurationError(
                "Wonton26 protected-player configuration is required"
            )
        return frozenset(players)

    def _transport(self) -> WhitelistTransport:
        return self._transport_factory(Wonton26RconConfiguration.from_environment())

    def get_whitelist(self) -> list[dict[str, object]]:
        protected_players = self._protected_players()
        response = self._transport().list_whitelist()
        response = _ANSI_ESCAPE.sub("", response).strip()
        match = _WHITELIST_RESPONSE.fullmatch(response)
        if match is None:
            raise Wonton26ProtocolError("Unexpected Wonton26 RCON response")
        count = int(match.group("count"))
        names_text = match.group("names")
        names = [] if names_text is None else [name.strip() for name in names_text.split(",")]
        if len(names) != count or any(not USERNAME_PATTERN.fullmatch(name) for name in names):
            raise Wonton26ProtocolError("Unexpected Wonton26 RCON response")
        return [
            {"username": username, "protected": username.casefold() in protected_players}
            for username in names
        ]

    def add_whitelist_player(self, username: str) -> None:
        username = validate_minecraft_username(username)
        self._protected_players()
        with self._mutation_lock:
            response = self._transport().add_whitelist_player(username)
        if response == "That player is already whitelisted":
            raise PlayerAlreadyWhitelistedError("Player is already whitelisted")
        match = _ADDED_RESPONSE.fullmatch(response)
        if match is None or match.group("username").casefold() != username.casefold():
            raise Wonton26ProtocolError("Unexpected Wonton26 RCON response")

    def remove_whitelist_player(self, username: str) -> None:
        username = validate_minecraft_username(username)
        if username.casefold() in self._protected_players():
            raise ProtectedPlayerError("This player is protected from removal")
        with self._mutation_lock:
            response = self._transport().remove_whitelist_player(username)
        if response == "That player is not whitelisted":
            raise PlayerNotWhitelistedError("Player is not whitelisted")
        match = _REMOVED_RESPONSE.fullmatch(response)
        if match is None or match.group("username").casefold() != username.casefold():
            raise Wonton26ProtocolError("Unexpected Wonton26 RCON response")
