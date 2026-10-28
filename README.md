# Homelab Control Plane

An authenticated FastAPI application for homelab status and carefully bounded
Proxmox operations. It includes a browser dashboard and a Textual terminal
interface. The application is designed for a trusted private network, not direct
public-internet exposure.

## Implemented features

- Signed browser sessions, CSRF protection for cookie-authenticated commands,
  scrypt password hashes, account management, and role-based API authorization.
- Proxmox node/guest status, administrative guest power operations, and a
  terminal UI. VMID 100 is protected from power operations.
- Dedicated Minecraft controls for approved VMs 104 and 105. Non-admin starts
  require both VMs to be confirmed stopped; admins retain the documented
  override. Physical-server controls use optional FAMBAM22 configuration.
- Restricted Wonton26 whitelist operations using fixed RCON commands, validated
  Java usernames, and a required configurable protected-player list.
- A fixed, disabled-by-default VM 104 snapshot-backup job that audits state
  transitions and verifies a new artifact. It does not expose prune, delete, or
  restore operations.
- A Textual dashboard with guest operations and backup status/history.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for component boundaries and
data flows. Planned features are not presented as implemented functionality.

## Requirements

- Linux host for the systemd deployment and local system metrics.
- Python 3.11 or newer.
- Access to a configured Proxmox API endpoint for live API functionality.
- Optional: `wakeonlan` and an SSH client for configured FAMBAM22 controls;
  `mctools` support for Wonton26 RCON is included in application dependencies.

Tests use mocks and local temporary data; they do not require live Proxmox,
Minecraft, RCON, or production credentials.

## Installation and first run

From a checkout on the host where the application will run:

```bash
mkdir -p "$HOME/projects"
# Place or clone this repository at "$HOME/projects/control-plane".
cd "$HOME/projects/control-plane"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p "$HOME/.config/control-plane"
install -m 600 deploy/control-plane.env.example \
  "$HOME/.config/control-plane/control-plane.env"
```

Edit the private EnvironmentFile using a secure editor. Fill in the Proxmox
endpoint and API token, generate a random session secret, and set at least one
protected Wonton26 player if whitelist access will be enabled. For example:

```bash
openssl rand -hex 32
```

Do not commit the resulting EnvironmentFile or copy real credentials into
commands, documentation, issue reports, or logs. It must remain readable only
by the service account. See [SECURITY.md](SECURITY.md) for TLS trust, network
exposure, role boundaries, and safety conditions.

Create initial accounts. Passwords are prompted without echo and must be at
least 12 characters:

```bash
python -m controlplane.manage_users create admin-user --role admin
python -m controlplane.manage_users create standard-user --role user
python -m controlplane.manage_users create guest-user --role guest
```

The default account store is `~/.config/control-plane/users.json`. Set
`CONTROL_PLANE_USERS_FILE` in the process environment if using another path.
Assign Wonton26 whitelist management only to trusted accounts:

```bash
python -m controlplane.manage_users set-role standard-user minecraft_manager
```

Run the API locally (localhost only by default) and, in another terminal, the UI:

```bash
python -m controlplane.api
python -m controlplane.ui
```

The API host and port are configurable through `CONTROL_PLANE_API_HOST` and
`CONTROL_PLANE_API_PORT`. The Textual client defaults to
`http://127.0.0.1:8000`; set `CONTROL_PLANE_API` to connect elsewhere and provide
an admin bearer token in `CONTROL_PLANE_TOKEN`. Do not put tokens in shell
history or share them.

The account-management commands use the default user file unless
`CONTROL_PLANE_USERS_FILE` is exported in their process environment. The API
module loads the default private EnvironmentFile at startup; set
`CONTROL_PLANE_ENV_FILE` to use another file. These variables should point only
to files with restrictive permissions.

## Configuration

`deploy/control-plane.env.example` documents the supported private settings.
Important configuration groups:

| Purpose | Settings |
|---|---|
| Proxmox API | `PVE_HOST`, `PVE_PORT`, `PVE_NODE`, `PVE_TOKEN_ID`, `PVE_TOKEN_SECRET` |
| Proxmox TLS | `PVE_TLS_VERIFY` (enabled by default), optional `PVE_TLS_CA_FILE` |
| Authentication | `CONTROL_PLANE_SESSION_SECRET`, optional `CONTROL_PLANE_USERS_FILE`, `CONTROL_PLANE_SECURE_COOKIES` |
| API listener | `CONTROL_PLANE_API_HOST`, `CONTROL_PLANE_API_PORT` |
| FAMBAM22 | `FAMBAM22_HOST`, `FAMBAM22_PORT`, `FAMBAM22_MAC`, `FAMBAM22_SSH_USER`, `FAMBAM22_SSH_KEY` |
| Wonton26 RCON | `WONTON26_RCON_HOST`, `WONTON26_RCON_PORT`, `WONTON26_RCON_PASSWORD`, required `WONTON26_PROTECTED_PLAYERS` |
| Backup job | `PVE_BACKUP_NODE`, `PVE_BACKUP_VMID`, `PVE_BACKUP_STORAGE`, `PVE_BACKUP_TIME`, `PVE_BACKUP_ENABLED` |
| NAS status | `NAS_MOUNT_PATH` |

FAMBAM22 settings are optional; its actions stay unavailable until their
required settings validate. Wonton26 whitelist operations fail closed when the
protected-player list is absent or invalid. The fixed backup job is disabled by
default; when enabled, both the backup node and storage must be configured.
`PVE_NODE` identifies the Proxmox node used by the dashboard and approved
Minecraft task polling. See [AUTOMATION.md](AUTOMATION.md).

## Tests and CI

Install development dependencies and run the complete suite:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q -c pytest.ini tests
```

GitHub Actions runs the same tests on Python 3.11 using repository dependencies
only. The workflow does not configure production secrets or contact live
infrastructure.

## Deployment design

The user-level systemd unit is in `deploy/control-plane-api.service`. Its
existing `0.0.0.0` listener is retained for private-LAN compatibility and
therefore listens on every interface. Restrict it with a host/network firewall
and keep it on a trusted LAN or private VPN; never expose it directly to the
public internet. The listener host and port can be overridden through the
private EnvironmentFile. HTTPS through a trusted reverse proxy is recommended.

The supplied service unit expects the checkout at
`$HOME/projects/control-plane`. Install it for the service account and enable
it with:

```bash
mkdir -p "$HOME/.config/systemd/user"
install -m 644 deploy/control-plane-api.service \
  "$HOME/.config/systemd/user/control-plane-api.service"
systemctl --user daemon-reload
systemctl --user enable --now control-plane-api.service
```

Review the bind address and firewall before enabling network access. For
automatic startup before login, a system administrator may enable lingering
for the service account.

The deployment workflow transfers a reviewed exact-commit Git bundle over SSH.
Preflight requires a clean checkout, branch/commit verification, complete tests,
and the production push guard. Actual activation requires explicit typed
approval; the remote helper verifies secret-file ownership and permissions,
allows fast-forward only, checks service health, and rolls back on activation
failure. Dependency and systemd-unit changes require separate reviewed
procedures. See [deploy/README.md](deploy/README.md).

## Security reporting

Please report vulnerabilities privately to the repository maintainers rather
than opening a public issue with exploit details. Do not include credentials,
tokens, private keys, or private network configuration in reports.
