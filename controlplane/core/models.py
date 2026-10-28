from pydantic import BaseModel, Field


class NodeStatus(BaseModel):
    name: str
    status: str
    cpu_cores: int
    cpu_percent: float = 0
    memory_used_bytes: int
    memory_total_bytes: int
    memory_percent: float
    disk_used_bytes: int
    disk_total_bytes: int
    disk_percent: float
    uptime_seconds: int = 0


class GuestStatus(BaseModel):
    vmid: int
    name: str
    type: str
    status: str
    node: str
    cpu_percent: float = 0
    memory_used_bytes: int = 0
    memory_total_bytes: int = 0
    memory_percent: float = 0
    uptime_seconds: int = 0


class ControllerStatus(BaseModel):
    name: str
    hostname: str
    status: str
    api_status: str
    cpu_cores: int
    cpu_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    memory_percent: float
    disk_used_bytes: int
    disk_total_bytes: int
    disk_percent: float
    uptime_seconds: int


class NasStatus(BaseModel):
    name: str
    status: str
    mount_path: str
    storage_used_bytes: int
    storage_total_bytes: int
    storage_free_bytes: int
    storage_percent: float
    detail: str | None = None


class ProxmoxSnapshot(BaseModel):
    """Normalized live state consumed by dashboards and future collectors."""

    node: NodeStatus
    guests: list[GuestStatus] = Field(default_factory=list)


class DashboardStatus(ProxmoxSnapshot):
    controller: ControllerStatus
    nas: NasStatus


class AutomationJobStatus(BaseModel):
    id: str
    name: str
    kind: str
    enabled: bool
    schedule: str
    node: str
    vmid: int
    storage: str
    permissions: str = "create-and-verify-only"


class AutomationExecution(BaseModel):
    id: str
    job_id: str
    trigger: str
    state: str
    verification: str
    queued_at: str
    started_at: str | None = None
    finished_at: str | None = None
    message: str = ""
    task_id: str | None = None
    result: dict = Field(default_factory=dict)
