from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROLES = {"admin", "minecraft_manager", "user", "guest"}
ROLE_PERMISSIONS = {
    "admin": {"*"},
    "user": {
        "minecraft.read",
        "minecraft.start",
        "minecraft.stop",
        "minecraft.reboot",
    },
    "minecraft_manager": {
        "minecraft.read",
        "minecraft.start",
        "minecraft.stop",
        "minecraft.reboot",
        "minecraft.wonton26.whitelist",
    },
    "guest": {"minecraft.read"},
}

PASSWORD_MIN_LENGTH = 12
SESSION_TTL_SECONDS = 8 * 60 * 60
SESSION_COOKIE = "control_plane_session"


class SecurityConfigurationError(RuntimeError):
    """Raised when authentication cannot be used safely."""


@dataclass(frozen=True)
class User:
    username: str
    role: str
    password_hash: str
    active: bool = True

    def can(self, permission: str) -> bool:
        permissions = ROLE_PERMISSIONS.get(self.role, set())
        return "*" in permissions or permission in permissions

    def public(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "role": self.role,
            "permissions": sorted(ROLE_PERMISSIONS.get(self.role, set())),
        }


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Passwords must contain at least {PASSWORD_MIN_LENGTH} characters"
        )
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt$v=1$n=16384,r=8,p=1${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, version, parameters, salt_text, digest_text = encoded.split("$")
        if algorithm != "scrypt" or version != "v=1":
            return False
        parsed = dict(part.split("=", 1) for part in parameters.split(","))
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt_text),
            n=int(parsed["n"]),
            r=int(parsed["r"]),
            p=int(parsed["p"]),
            dklen=len(_b64decode(digest_text)),
        )
        return hmac.compare_digest(candidate, _b64decode(digest_text))
    except (KeyError, TypeError, ValueError):
        return False


class UserStore:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def from_environment(cls) -> "UserStore":
        configured = os.getenv("CONTROL_PLANE_USERS_FILE")
        path = (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".config" / "control-plane" / "users.json"
        )
        return cls(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "users": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecurityConfigurationError(
                f"Cannot read user store {self.path}: {exc}"
            ) from exc
        if payload.get("version") != 1 or not isinstance(payload.get("users"), dict):
            raise SecurityConfigurationError("The user store has an unsupported format")
        return payload

    def get(self, username: str) -> User | None:
        record = self._read()["users"].get(username.casefold())
        if not isinstance(record, dict):
            return None
        role = record.get("role")
        password_hash = record.get("password_hash")
        if role not in ROLES or not isinstance(password_hash, str):
            raise SecurityConfigurationError(
                f"User {username!r} has an invalid account record"
            )
        return User(
            username=username.casefold(),
            role=role,
            password_hash=password_hash,
            active=bool(record.get("active", True)),
        )

    def save_user(
        self,
        username: str,
        password: str,
        role: str,
        *,
        active: bool = True,
    ) -> User:
        normalized = username.strip().casefold()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("Username must be non-empty and contain no whitespace")
        if role not in ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(ROLES))}")
        payload = self._read()
        password_hash = hash_password(password)
        payload["users"][normalized] = {
            "role": role,
            "password_hash": password_hash,
            "active": active,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return User(normalized, role, password_hash, active)

    def set_active(self, username: str, active: bool) -> User:
        normalized = username.strip().casefold()
        payload = self._read()
        record = payload["users"].get(normalized)
        if not isinstance(record, dict):
            raise ValueError(f"User {normalized!r} does not exist")
        record["active"] = active
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        user = self.get(normalized)
        if user is None:
            raise SecurityConfigurationError("Account update was not persisted")
        return user

    def set_role(self, username: str, role: str) -> User:
        normalized = username.strip().casefold()
        if role not in ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(ROLES))}")
        payload = self._read()
        record = payload["users"].get(normalized)
        if not isinstance(record, dict):
            raise ValueError(f"User {normalized!r} does not exist")
        record["role"] = role
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        user = self.get(normalized)
        if user is None:
            raise SecurityConfigurationError("Account update was not persisted")
        return user


class AuthService:
    def __init__(self, store: UserStore, secret: str, *, ttl: int = SESSION_TTL_SECONDS):
        if len(secret.encode("utf-8")) < 32:
            raise SecurityConfigurationError(
                "CONTROL_PLANE_SESSION_SECRET must contain at least 32 bytes"
            )
        self.store = store
        self.secret = secret.encode("utf-8")
        self.ttl = ttl

    @classmethod
    def from_environment(cls) -> "AuthService":
        secret = os.getenv("CONTROL_PLANE_SESSION_SECRET", "")
        return cls(UserStore.from_environment(), secret)

    @staticmethod
    def _credential_version(user: User) -> str:
        return hashlib.sha256(user.password_hash.encode("utf-8")).hexdigest()[:16]

    def authenticate(self, username: str, password: str) -> User | None:
        user = self.store.get(username.strip().casefold())
        if user is None or not user.active:
            # Equalize most of the cost for unknown and disabled accounts.
            hashlib.scrypt(
                password.encode("utf-8"),
                salt=b"control-plane!!",
                n=2**14,
                r=8,
                p=1,
                dklen=32,
            )
            return None
        return user if verify_password(password, user.password_hash) else None

    def issue_session(self, user: User) -> tuple[str, str, int]:
        issued_at = int(time.time())
        csrf = secrets.token_urlsafe(24)
        payload = {
            "sub": user.username,
            "iat": issued_at,
            "exp": issued_at + self.ttl,
            "jti": secrets.token_urlsafe(18),
            "ver": self._credential_version(user),
            "csrf": csrf,
        }
        header = {"alg": "HS256", "typ": "CPSESSION"}
        parts = [
            _b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
        signing_input = ".".join(parts).encode("ascii")
        signature = hmac.new(self.secret, signing_input, hashlib.sha256).digest()
        return f"{parts[0]}.{parts[1]}.{_b64encode(signature)}", csrf, self.ttl

    def verify_session(self, token: str) -> tuple[User, dict[str, Any]] | None:
        try:
            header_text, payload_text, signature_text = token.split(".")
            signing_input = f"{header_text}.{payload_text}".encode("ascii")
            expected = hmac.new(self.secret, signing_input, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _b64decode(signature_text)):
                return None
            header = json.loads(_b64decode(header_text))
            payload = json.loads(_b64decode(payload_text))
            if header != {"alg": "HS256", "typ": "CPSESSION"}:
                return None
            if int(payload["exp"]) <= int(time.time()):
                return None
            user = self.store.get(str(payload["sub"]))
            if user is None or not user.active:
                return None
            if payload.get("ver") != self._credential_version(user):
                return None
            return user, payload
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
