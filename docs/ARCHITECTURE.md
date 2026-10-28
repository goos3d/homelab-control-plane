# Architecture

## Runtime components

```text
Browser ── session cookie + CSRF ─┐
Textual UI ── bearer token ────────┼── FastAPI application
                                  │     ├── Authentication and RBAC
                                  │     ├── Proxmox service
                                  │     ├── Minecraft safety service
                                  │     ├── Wonton26 whitelist service
                                  │     └── Backup scheduler and audit history
                                  └── Static web dashboard
```

The API is the authorization boundary. The browser and Textual clients present
credentials, but server-side dependencies enforce each permission. Cookie
commands additionally require a session-bound CSRF token. The frontend's
visibility rules are not security controls.

## Python packages

- `controlplane/api`: FastAPI routes, authentication dependencies, lifecycle
  handling, and the configurable `python -m controlplane.api` entry point.
- `controlplane/core`: API data models and account/session security primitives.
- `controlplane/services`: Proxmox HTTP client; VM safety and task correlation;
  physical-server wake/shutdown; system/NAS metrics; fixed backup automation;
  and constrained Wonton26 RCON operations.
- `controlplane/ui`: Textual application, HTTP client, widgets, and styles.
- `controlplane/web`: Static browser dashboard served by the API.
- `deploy`: Environment example, user-level systemd unit, and guarded
  bundle-based deployment helpers.
- `tests`: Unit and API tests using mocks, temporary files, and fake transports.

## Trust boundaries and safety invariants

1. Password hashes and signed sessions are managed by the application. Browser
   command requests require CSRF validation; API authorization is enforced on
   every protected route.
2. Generic Proxmox operations require admin permission. The API rejects power
   operations on VMID 100. Minecraft user actions are limited to the approved
   VMs and guarded by fresh Proxmox state; a local lock enforces the one-running
   constraint within the single API process.
3. Wonton26 accepts only validated player usernames and fixed whitelist
   commands. Its protected-player configuration is mandatory and validated;
   missing/invalid configuration fails closed for list, add, and remove.
4. Backup automation submits one fixed snapshot-mode backup and verifies its
   artifact. It does not expose arbitrary Proxmox commands, deletion, pruning,
   or restoration.
5. Proxmox TLS certificate verification is enabled by default. A private CA can
   be explicitly configured. Disabling verification is an explicit insecure
   opt-out and is not appropriate for production.
6. Deployment does not push into the production checkout. It verifies clean
   trees, exact approved commits, secret-file metadata, fast-forward-only
   updates, service health, and rollback behavior.

## Data flows

- The API reads its private configuration from its process environment or
  systemd EnvironmentFile. Proxmox credentials are sent only to the configured
  Proxmox HTTPS API. `PVE_NODE` names the node used by the dashboard and task
  correlation; backup node/storage are explicit settings when that job is
  enabled.
- Browser sessions use an HttpOnly, SameSite cookie. The browser obtains a CSRF
  token after authentication and supplies it for mutating cookie-authenticated
  requests.
- The Textual client sends an admin bearer token to the API and polls task
  status through authenticated API routes.
- Whitelist RCON credentials stay server-side; clients can invoke only the
  application’s fixed list/add/remove capabilities.
- Backup history is an append-only JSONL file in the user's state directory.

## Deployment topology

The systemd unit currently binds to `0.0.0.0` for LAN compatibility. This means
all network interfaces, so firewalling and private-network access are required.
Use loopback binding behind a suitably configured reverse proxy for deployments
that do not need direct LAN access. Production TLS trust and systemd-unit
changes are separate, explicitly reviewed deployment steps.

## Not implemented

The repository does not implement a public cloud control plane, multi-process
distributed Minecraft locking, backup retention/prune/restore, arbitrary RCON
commands, or a Windows desktop installer. Such work must not be inferred from
planned/placeholder UI elements.
