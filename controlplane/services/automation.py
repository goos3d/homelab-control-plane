from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from controlplane.core.models import AutomationExecution, AutomationJobStatus


class ExecutionHistory:
    """Append-only audit log of every execution state transition."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(
            path
            or os.getenv(
                "AUTOMATION_HISTORY_PATH",
                Path.home() / ".local/state/control-plane/automation.jsonl",
            )
        )
        self._lock = threading.Lock()

    def append(self, execution: AutomationExecution) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            **execution.model_dump(),
        }
        with self._lock, self.path.open("a", encoding="utf-8") as destination:
            destination.write(json.dumps(event, sort_keys=True) + "\n")

    def records(self, limit: int = 50) -> list[AutomationExecution]:
        latest: dict[str, dict] = {}
        try:
            with self.path.open(encoding="utf-8") as source:
                for line in source:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    execution_id = event.get("id")
                    if execution_id:
                        latest[execution_id] = event
        except FileNotFoundError:
            return []
        records = [AutomationExecution.model_validate(item) for item in latest.values()]
        records.sort(key=lambda item: item.queued_at, reverse=True)
        return records[:limit]

    def last_scheduled_run(self, job_id: str) -> datetime | None:
        timestamps = [
            item.started_at or item.queued_at
            for item in self.records(limit=1000)
            if item.job_id == job_id and item.trigger == "schedule"
        ]
        return datetime.fromisoformat(max(timestamps)) if timestamps else None


class AutomationService:
    JOB_ID = "minecraft-vm-backup"

    def __init__(
        self,
        proxmox,
        *,
        history: ExecutionHistory | None = None,
        poll_seconds: float = 2.0,
        timeout_seconds: float = 7200,
    ) -> None:
        self.proxmox = proxmox
        self.history = history or ExecutionHistory()
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.node = os.getenv("PVE_BACKUP_NODE") or os.getenv("PVE_NODE", "")
        self.vmid = int(os.getenv("PVE_BACKUP_VMID", "104"))
        self.storage = os.getenv("PVE_BACKUP_STORAGE", "")
        self.schedule = os.getenv("PVE_BACKUP_TIME", "03:00")
        self.enabled = os.getenv("PVE_BACKUP_ENABLED", "0") == "1"
        datetime.strptime(self.schedule, "%H:%M")
        if self.enabled and (not self.node or not self.storage):
            raise RuntimeError(
                "PVE_BACKUP_NODE and PVE_BACKUP_STORAGE are required when backups are enabled"
            )
        self._active = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backup")

    def job(self) -> AutomationJobStatus:
        return AutomationJobStatus(
            id=self.JOB_ID,
            name="Minecraft Reclamation VM backup",
            kind="proxmox-guest-backup",
            enabled=self.enabled,
            schedule=f"daily {self.schedule}",
            node=self.node,
            vmid=self.vmid,
            storage=self.storage,
        )

    def snapshot(self) -> dict:
        return {
            "jobs": [self.job()],
            "executions": self.history.records(),
        }

    def start(self) -> None:
        if self._scheduler and self._scheduler.is_alive():
            return
        self._stop.clear()
        self._scheduler = threading.Thread(
            target=self._schedule_loop,
            name="backup-scheduler",
            daemon=True,
        )
        self._scheduler.start()

    def stop(self) -> None:
        self._stop.set()
        if self._scheduler:
            self._scheduler.join(timeout=5)
        self._pool.shutdown(wait=True)

    def submit(self, trigger: str = "manual") -> AutomationExecution:
        if not self.enabled:
            raise ValueError("Backup job is disabled")
        execution = AutomationExecution(
            id=uuid4().hex,
            job_id=self.JOB_ID,
            trigger=trigger,
            state="queued",
            verification="pending",
            queued_at=self._now(),
        )
        self.history.append(execution)
        self._pool.submit(self._execute, execution)
        return execution

    def tick(self, now: datetime | None = None) -> bool:
        if not self.enabled:
            return False
        now = now or datetime.now()
        hour, minute = (int(part) for part in self.schedule.split(":"))
        last_run = self.history.last_scheduled_run(self.JOB_ID)
        if (
            now.hour != hour
            or now.minute != minute
            or (last_run and last_run.date() >= now.date())
        ):
            return False
        self.submit(trigger="schedule")
        return True

    def _schedule_loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(30)

    def _execute(self, execution: AutomationExecution) -> None:
        with self._lock:
            if self._active:
                execution.state = "skipped"
                execution.verification = "not-run"
                execution.finished_at = self._now()
                execution.message = "Another backup execution is already active"
                self.history.append(execution)
                return
            self._active = True

        try:
            execution.state = "running"
            execution.started_at = self._now()
            execution.message = "Submitting fixed-target Proxmox backup"
            self.history.append(execution)
            task_id = self.proxmox.start_guest_backup(
                node=self.node,
                vmid=self.vmid,
                storage=self.storage,
                mode="snapshot",
            )
            execution.task_id = task_id
            deadline = time.monotonic() + self.timeout_seconds
            while time.monotonic() < deadline:
                task = self.proxmox.get_task_status(self.node, task_id)
                if task.get("status") == "stopped":
                    exit_status = str(task.get("exitstatus", "UNKNOWN"))
                    if exit_status != "OK":
                        raise RuntimeError(f"Proxmox backup task failed: {exit_status}")
                    break
                time.sleep(self.poll_seconds)
            else:
                raise TimeoutError("Timed out waiting for the Proxmox backup task")

            execution.state = "verifying"
            execution.message = "Task completed; verifying backup artifact"
            self.history.append(execution)
            artifacts = self.proxmox.get_backup_artifacts(
                node=self.node,
                storage=self.storage,
                vmid=self.vmid,
            )
            started = datetime.fromisoformat(execution.started_at).timestamp()
            candidates = [
                item
                for item in artifacts
                if float(item.get("ctime", 0)) >= started - 5
            ]
            if not candidates:
                execution.verification = "failed"
                raise RuntimeError("No new backup artifact was found after task success")
            artifact = max(candidates, key=lambda item: item.get("ctime", 0))
            execution.state = "succeeded"
            execution.verification = "passed"
            execution.result = {
                "artifact": artifact.get("volid"),
                "size_bytes": artifact.get("size"),
            }
            execution.message = "Backup task and new artifact verified"
        except Exception as exc:
            execution.state = "failed"
            if execution.verification == "pending":
                execution.verification = "not-run"
            execution.message = str(exc)
        finally:
            execution.finished_at = self._now()
            self.history.append(execution)
            with self._lock:
                self._active = False

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")
