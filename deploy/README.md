# Control Plane deployment

The deployment client transfers reviewed commits as Git bundles over SSH. It
does not push into the checked-out production repository. Configure the target
host, repository, and service account as PowerShell parameters or environment
variables:

```powershell
$env:CONTROL_PLANE_DEPLOY_SSH_TARGET = 'service-user@control-plane-host'
$env:CONTROL_PLANE_DEPLOY_REPO = '/home/service-user/apps/control-plane'
$env:CONTROL_PLANE_DEPLOY_USER = 'service-user'
```

The branch defaults to `main` and may be changed with `-Branch`. Service name,
health URL, and remote state directory can also be supplied as parameters.
Remote paths are restricted to safe absolute/relative path forms; health checks
must target the loopback Control Plane endpoint. The SSH account must be the
same account that owns the secret files and user-level systemd service.

## Preflight only

From PowerShell in the repository root:

```powershell
.\deploy\deploy.ps1
```

Preflight requires all of the following:

- the configured branch with a clean working tree;
- local `HEAD` exactly equal to the corresponding `origin/<branch>`;
- the production push guard still enabled;
- the complete local test suite passing; and
- a valid Git bundle for the exact approved commit.

Preflight does not contact or change production.

## Deploy

```powershell
.\deploy\deploy.ps1 -Deploy
```

The script repeats preflight, displays the exact commit, and requires typing
`DEPLOY <short-commit>` before it writes to production.

The remote helper then:

1. obtains an exclusive deployment lock;
2. requires a clean production checkout on the configured branch;
3. verifies both secret files exist, are owned by the configured service account, and have mode 600;
4. verifies `.env` remains untracked and ignored;
5. verifies the bundle and exact approved commit;
6. permits fast-forward deployments only;
7. refuses automatic dependency or systemd-unit changes;
8. restarts the user-level `control-plane-api.service`;
9. checks `http://127.0.0.1:8000/health`; and
10. returns to the previous commit automatically if activation fails.

Deployment records and retained bundles live under the configured state
directory in the remote user's home. Neither repository `.env` nor
`~/.config/control-plane/control-plane.env` is copied, modified, or included in
the bundle.

## Dependency changes

The normal deployment intentionally rejects a changed `requirements.txt`. For a reviewed
dependency update, first install the exact pinned requirements into the production virtual
environment through a separately approved maintenance procedure, verify the resulting
environment and full test suite, then deploy the application bundle normally. Do not weaken
or bypass the deployment guard for dependency changes.
