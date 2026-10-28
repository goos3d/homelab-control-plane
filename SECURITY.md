# Authentication and authorization

The control plane uses application accounts stored in a local JSON file. Passwords are
never stored directly: each password is independently salted and hashed with scrypt.
Browser sessions are HMAC-SHA256 signed, expire after eight hours, and are carried in an
`HttpOnly`, `SameSite=Strict` cookie. Browser command requests also require a session-bound
CSRF token. Changing a password, disabling an account, or rotating the session secret
invalidates the affected sessions.

## Roles and permissions

| Role | Access |
|---|---|
| `admin` | Every existing dashboard, Proxmox, task, and automation endpoint. Admin may also control either Minecraft server and override the one-server limit. |
| `minecraft_manager` | The `user` Minecraft controls plus Wonton26 whitelist list/add/remove access. This role has no generic Proxmox access. |
| `user` | Read status and start, stop, or reboot approved Minecraft VMs 104 and 105 through the dedicated Minecraft API only. |
| `guest` | Read status for approved Minecraft VMs 104 and 105 only. |

The frontend follows these roles, but it is not the security boundary. Every API endpoint
checks the authenticated account. A user cannot gain generic Proxmox access by manually
calling an admin URL, and an unapproved VMID is rejected by the Minecraft API.

## Wonton26 whitelist

Only `minecraft_manager` and `admin` accounts may use the Wonton26 whitelist API. The
service reads its RCON host, port, and password from the private systemd EnvironmentFile,
opens a short-lived bounded connection for each operation, and sends only fixed whitelist
commands after validating a Java username (`[A-Za-z0-9_]{3,16}`). It never exposes an
arbitrary RCON command or RCON credentials to a client.

Set `WONTON26_PROTECTED_PLAYERS` in the private EnvironmentFile to a comma-separated
list of protected Java usernames. At least one username is required: missing or invalid
configuration fails closed for all whitelist operations. Each configured username is
validated, and removal checks are case-insensitive.

## Exclusive Minecraft resource group

VM 104 and VM 105 form the exclusive Minecraft resource group with `max_running = 1`
for `user` accounts. Before a user start, the API holds a local start
lock and asks Proxmox for fresh state. It starts the requested VM only when that VM and the
other VM are both reported exactly as `stopped`. Running, stopping, missing, and unknown
states all fail closed with HTTP 409. Admin accounts bypass this guard.

Run one Uvicorn worker. The in-process lock prevents simultaneous starts within that
service process; multiple independent API processes would require a shared/distributed
lock to provide the same race guarantee.

Minecraft task polling is limited to recently returned tasks from the dedicated Minecraft
action API on its designated node. A user may poll only tasks they initiated; admins may
poll any known Minecraft task. This in-memory correlation expires after ten minutes and is
cleared on an API service restart.
Set `PVE_NODE` to the Proxmox node used by the dashboard and Minecraft task polling.

## Deployment notes

- Keep the user file and environment file readable only by the service account.
- Use a random session secret of at least 32 bytes and never commit it.
- The existing direct HTTP LAN deployment needs `CONTROL_PLANE_SECURE_COOKIES=false`.
  Prefer HTTPS through a reverse proxy and set it to `true`; this prevents session cookies
  from being sent over plaintext HTTP.
- The bundled systemd unit binds to `0.0.0.0` for LAN compatibility. This listens on every
  network interface. Restrict access with a host/network firewall and keep the service on a
  trusted LAN or private VPN; do not expose it directly to the public internet. Set
  `CONTROL_PLANE_API_HOST=127.0.0.1` for a loopback-only listener.
- Proxmox TLS certificate verification is enabled by default. For a private CA, install
  its certificate on the service host and set `PVE_TLS_CA_FILE` to the readable certificate
  path in the private EnvironmentFile. The configured CA is added to the system trust
  roots. `PVE_TLS_VERIFY=false` is an explicit insecure opt-out and must not be used for
  production. A production certificate/CA migration requires separate approval and is not
  performed by application deployment.
- The bearer-token endpoint exists for the terminal client. Treat returned tokens like
  passwords and avoid placing either passwords or tokens in shell history.

## Physical server controls

FAMBAM22 connection and wake/shutdown details are supplied through `FAMBAM22_HOST`,
`FAMBAM22_PORT`, `FAMBAM22_MAC`, `FAMBAM22_SSH_USER`, and `FAMBAM22_SSH_KEY` in the
private EnvironmentFile. The host, valid port, and MAC are checked before Wake-on-LAN;
the host, valid port, SSH username, and existing key file are checked before SSH. The
SSH connection requires strict host-key checking and batch mode.
