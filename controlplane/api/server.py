from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from controlplane.services.fambam import FambamService
from controlplane.core.models import DashboardStatus
from controlplane.core.security import AuthService, SESSION_COOKIE, SecurityConfigurationError, User
from controlplane.services.automation import AutomationService
from controlplane.services.minecraft import MinecraftConflictError, MinecraftService
from controlplane.services.nas import NasService
from controlplane.services.proxmox import ProxmoxService
from controlplane.services.system import SystemService
from controlplane.services.wonton26 import (
    InvalidMinecraftUsernameError,
    PlayerAlreadyWhitelistedError,
    PlayerNotWhitelistedError,
    ProtectedPlayerError,
    Wonton26ConfigurationError,
    Wonton26ProtocolError,
    Wonton26TimeoutError,
    Wonton26UnavailableError,
    Wonton26WhitelistError,
    Wonton26WhitelistService,
    validate_minecraft_username,
)

pve = ProxmoxService()
system = SystemService()
nas = NasService()
automation = AutomationService(pve)
minecraft = MinecraftService(pve)
fambam = FambamService()
wonton26 = Wonton26WhitelistService()
WEB_INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"
_auth_service: AuthService | None = None
bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class WhitelistPlayerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str

    @field_validator("username")
    @classmethod
    def valid_minecraft_username(cls, value: str) -> str:
        try:
            return validate_minecraft_username(value)
        except InvalidMinecraftUsernameError as exc:
            raise ValueError("Invalid Minecraft Java username") from exc


def _secure_cookies() -> bool:
    return os.getenv("CONTROL_PLANE_SECURE_COOKIES", "false").lower() in {"1", "true", "yes", "on"}


def get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        try:
            _auth_service = AuthService.from_environment()
        except SecurityConfigurationError as exc:
            raise HTTPException(status_code=503, detail=f"Authentication is not configured: {exc}") from exc
    return _auth_service


def authenticated_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> User:
    using_cookie = credentials is None
    token = request.cookies.get(SESSION_COOKIE, "") if using_cookie else credentials.credentials
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})
    verified = get_auth_service().verify_session(token)
    if verified is None:
        raise HTTPException(status_code=401, detail="Session is invalid or expired", headers={"WWW-Authenticate": "Bearer"})
    user, claims = verified
    if using_cookie and request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        expected = str(claims.get("csrf", ""))
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=403, detail="CSRF validation failed")
    request.state.auth_claims = claims
    return user


def require_permission(permission: str) -> Callable[..., User]:
    def dependency(user: User = Depends(authenticated_user)) -> User:
        if not user.can(permission):
            raise HTTPException(status_code=403, detail=f"Permission required: {permission}")
        return user
    return dependency


require_admin = require_permission("admin.full")
require_minecraft_read = require_permission("minecraft.read")
require_wonton26_whitelist = require_permission("minecraft.wonton26.whitelist")


def wonton26_error_response(exc: Wonton26WhitelistError) -> HTTPException:
    if isinstance(exc, InvalidMinecraftUsernameError):
        return HTTPException(status_code=422, detail="Invalid Minecraft Java username")
    if isinstance(exc, ProtectedPlayerError):
        return HTTPException(status_code=403, detail="This player is protected from removal")
    if isinstance(exc, (PlayerAlreadyWhitelistedError, PlayerNotWhitelistedError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, Wonton26TimeoutError):
        return HTTPException(status_code=504, detail="Wonton26 RCON timed out")
    if isinstance(exc, (Wonton26ConfigurationError, Wonton26UnavailableError)):
        return HTTPException(status_code=503, detail="Wonton26 RCON is unavailable")
    if isinstance(exc, Wonton26ProtocolError):
        return HTTPException(status_code=502, detail="Unexpected Wonton26 RCON response")
    return HTTPException(status_code=503, detail="Wonton26 whitelist operation failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    automation.start()
    try:
        yield
    finally:
        automation.stop()


app = FastAPI(title="Control Plane", version="0.3.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def web_control_plane():
    return FileResponse(WEB_INDEX, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"status": "ok", "service": "control-plane", "version": "0.3.0"}


def _login(payload: LoginRequest) -> tuple[AuthService, User, str, str, int]:
    service = get_auth_service()
    user = service.authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password", headers={"WWW-Authenticate": "Bearer"})
    token, csrf, ttl = service.issue_session(user)
    return service, user, token, csrf, ttl


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, response: Response):
    _, user, token, csrf, ttl = _login(payload)
    response.set_cookie(
        SESSION_COOKIE, token, max_age=ttl, httponly=True,
        secure=_secure_cookies(), samesite="strict", path="/",
    )
    return {"user": user.public(), "csrf_token": csrf, "expires_in": ttl}


@app.post("/api/v1/auth/token")
def access_token(payload: LoginRequest):
    _, user, token, _, ttl = _login(payload)
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl, "user": user.public()}


@app.get("/api/v1/auth/me")
def auth_me(request: Request, user: User = Depends(authenticated_user)):
    return {"user": user.public(), "csrf_token": str(request.state.auth_claims.get("csrf", ""))}


@app.post("/api/v1/auth/logout")
def logout(response: Response, _user: User = Depends(authenticated_user)):
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=_secure_cookies(), httponly=True, samesite="strict"
    )
    return {"logged_out": True}


@app.get("/api/v1/proxmox/nodes")
def proxmox_nodes(_user: User = Depends(require_admin)):
    try:
        return {"nodes": pve.get_nodes()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/proxmox/guests")
def proxmox_guests(_user: User = Depends(require_admin)):
    try:
        return {"guests": pve.get_guests()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/dashboard", response_model=DashboardStatus)
def dashboard(_user: User = Depends(require_admin)):
    try:
        proxmox = pve.get_dashboard()
        return DashboardStatus(
            node=proxmox.node, guests=proxmox.guests,
            controller=system.get_status(), nas=nas.get_status(),
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/proxmox/guests/{vmid}/{action}")
def proxmox_guest_action(vmid: int, action: str, _user: User = Depends(require_admin)):
    if vmid == 100:
        raise HTTPException(status_code=403, detail="VMID 100 is protected: control-plane power actions are disabled")
    try:
        task = pve.guest_action(vmid, action)
        return {"accepted": True, "vmid": vmid, "action": action, "task": task}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/proxmox/tasks/{node}/{upid:path}")
def proxmox_task_status(node: str, upid: str, _user: User = Depends(require_admin)):
    try:
        return {"task": pve.get_task_status(node, upid)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/minecraft/servers")
def minecraft_servers(user: User = Depends(require_minecraft_read)):
    try:
        servers = minecraft.snapshot(enforce_exclusivity=user.role != "admin")
        servers.append(fambam.snapshot())

        return {
            "servers": servers,
            "exclusive_group": {
                "name": "minecraft-exclusive",
                "max_running": 1,
                "admin_override": user.role == "admin",
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/minecraft/servers/{vmid}/{action}")
def minecraft_server_action(vmid: int, action: str, user: User = Depends(authenticated_user)):
    try:
        profile = minecraft.require_approved(vmid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if action not in {"start", "stop", "reboot"}:
        raise HTTPException(status_code=400, detail=f"Unsupported Minecraft action: {action}")
    permission = f"minecraft.{action}"
    if not user.can(permission):
        raise HTTPException(status_code=403, detail=f"Permission required: {permission}")
    try:
        task = minecraft.action(vmid, action, admin_override=user.role == "admin")
        minecraft.track_task(task, user.username)
        return {
            "accepted": True, "vmid": vmid, "name": profile["name"],
            "node": minecraft.node, "action": action, "task": task,
        }
    except MinecraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/minecraft/wonton26/whitelist")
def wonton26_whitelist(_user: User = Depends(require_wonton26_whitelist)):
    try:
        return {"server": "Wonton26", "players": wonton26.get_whitelist()}
    except Wonton26WhitelistError as exc:
        raise wonton26_error_response(exc) from exc


@app.post("/api/v1/minecraft/wonton26/whitelist/players", status_code=201)
def add_wonton26_whitelist_player(
    payload: WhitelistPlayerRequest,
    _user: User = Depends(require_wonton26_whitelist),
):
    try:
        wonton26.add_whitelist_player(payload.username)
        return {
            "username": payload.username,
            "message": "Player added to the Wonton26 whitelist",
        }
    except Wonton26WhitelistError as exc:
        raise wonton26_error_response(exc) from exc


@app.delete("/api/v1/minecraft/wonton26/whitelist/players/{username}")
def remove_wonton26_whitelist_player(
    username: str,
    _user: User = Depends(require_wonton26_whitelist),
):
    try:
        validated_username = validate_minecraft_username(username)
        wonton26.remove_whitelist_player(validated_username)
        return {
            "username": validated_username,
            "message": "Player removed from the Wonton26 whitelist",
        }
    except Wonton26WhitelistError as exc:
        raise wonton26_error_response(exc) from exc


@app.post("/api/v1/minecraft/physical/fambam22/start", status_code=202)
def fambam22_start(user: User = Depends(authenticated_user)):
    if not user.can("minecraft.start"):
        raise HTTPException(
            status_code=403,
            detail="Permission required: minecraft.start",
        )

    try:
        if fambam.is_online():
            return {
                "accepted": False,
                "id": "fambam22",
                "name": "FAMBAM22",
                "action": "start",
                "message": "FAMBAM22 is already running",
            }
        message = fambam.start()
        return {
            "accepted": True,
            "id": "fambam22",
            "name": "FAMBAM22",
            "action": "start",
            "message": message,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/minecraft/physical/fambam22/stop", status_code=202)
def fambam22_stop(user: User = Depends(authenticated_user)):
    if not user.can("minecraft.stop"):
        raise HTTPException(
            status_code=403,
            detail="Permission required: minecraft.stop",
        )

    try:
        message = fambam.shutdown()
        return {
            "accepted": True,
            "id": "fambam22",
            "name": "FAMBAM22",
            "action": "stop",
            "message": message,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/minecraft/tasks/{node}/{upid:path}")
def minecraft_task_status(node: str, upid: str, user: User = Depends(authenticated_user)):
    if not any(user.can(permission) for permission in {"minecraft.start", "minecraft.stop", "minecraft.reboot"}):
        raise HTTPException(status_code=403, detail="Minecraft command access is required")
    if not minecraft.can_poll_task(
        node=node,
        task=upid,
        username=user.username,
        admin=user.role == "admin",
    ):
        raise HTTPException(status_code=403, detail="Minecraft task access is required")
    try:
        return {"task": pve.get_task_status(node, upid)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/automation")
def automation_status(_user: User = Depends(require_admin)):
    return automation.snapshot()


@app.post("/api/v1/automation/jobs/minecraft-vm-backup/run", status_code=202)
def run_minecraft_backup(_user: User = Depends(require_admin)):
    try:
        execution = automation.submit(trigger="manual-api")
        return {"accepted": True, "execution_id": execution.id, "state": "queued"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
