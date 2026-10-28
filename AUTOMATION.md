# Backup automation

The API service owns the scheduler and exposes the job to the Textual client.

The first job is deliberately fixed to one capability:

- Create a snapshot-mode Proxmox backup of VM 104.
- Write it to the configured backup storage.
- Poll the returned Proxmox UPID.
- Verify that a new VM 104 backup artifact exists.
- Append every state transition to the audit history.

It does not expose delete, prune, restore, retention, or arbitrary-command
operations.

Configuration lives in the private systemd EnvironmentFile. Set the node and storage
to the approved names for your Proxmox installation:

    PVE_BACKUP_NODE=<approved-proxmox-node>
    PVE_BACKUP_VMID=104
    PVE_BACKUP_STORAGE=<approved-backup-storage>
    PVE_BACKUP_TIME=03:00
    PVE_BACKUP_ENABLED=1

The scheduler runs only during the configured minute and at most once per
calendar day. It does not run missed jobs on API startup.

History is stored at:

    ~/.local/state/control-plane/automation.jsonl

API:

- GET /api/v1/automation
- POST /api/v1/automation/jobs/minecraft-vm-backup/run

The manual route can only submit this fixed job. Concurrent duplicate
executions are skipped and recorded.
