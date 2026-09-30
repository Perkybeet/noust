# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The shapes ``/api/server`` speaks.

Each mirrors a record of the managers field for field and adds nothing: the
managers decide what is true, these only describe it for the OpenAPI schema the
console is generated from. Models go through :mod:`noust.web.pydantic_compat`'s
rules (no ``Field(pattern=...)``, no v2-only methods) because Ubuntu 24.04 ships
pydantic 1.10.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# -- summary -------------------------------------------------------------------


class EolOut(BaseModel):
    """Where the operating system is in its support."""

    status: str
    end_date: str | None = None
    days_left: int | None = None
    source: str = "none"


class OsOut(BaseModel):
    """The operating system."""

    id: str
    name: str
    version: str
    eol: EolOut


class UpdatesSummary(BaseModel):
    """
    Pending updates, as the overview shows them.

    ``pending`` and ``security`` are null until the first computation finishes;
    ``error`` says why when it failed.
    """

    supported: bool
    security_scope: bool
    pending: int | None = None
    security: int | None = None
    kept_back: int | None = None
    broken: bool | None = None
    checked_at: str | None = None
    lists_age_seconds: int | None = None
    notes: list[str] = Field(default_factory=list)
    error: str | None = None
    reason: str | None = None


class RebootSummary(BaseModel):
    """Whether a reboot is due."""

    required: bool | None = None
    since: str | None = None
    reasons: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    detector_available: bool | None = None
    checked_at: str | None = None
    error: str | None = None


class AutoUpdatesSummary(BaseModel):
    """The state of the automatic updates."""

    mechanism: str | None = None
    supported: bool | None = None
    enabled: bool | None = None
    security_only: bool | None = None
    reboots: bool | None = None
    error: str | None = None


class DiskSummary(BaseModel):
    """The disks, by their worst."""

    worst_mount: str | None = None
    worst_percent: float | None = None
    inodes_percent: float | None = None
    free_bytes: int | None = None
    status: str | None = None
    mounts: int = 0
    error: str | None = None


class TimeSummary(BaseModel):
    """Whether the clock is right."""

    timezone: str | None = None
    synchronized: bool | None = None
    ntp_enabled: bool | None = None
    ntp_supported: bool | None = None
    error: str | None = None


class SwapSummary(BaseModel):
    """Swap in use."""

    total_bytes: int | None = None
    used_bytes: int | None = None
    recommended: bool | None = None
    error: str | None = None


class ScheduledPowerOut(BaseModel):
    """A reboot or shutdown that was asked for."""

    id: int
    action: str
    scheduled_for: str
    requested_at: str
    requested_by: str | None = None
    message: str | None = None
    boot_id: str
    status: str
    finished_at: str | None = None


class PowerSummary(BaseModel):
    """What is scheduled."""

    scheduled: ScheduledPowerOut | None = None


class SystemStateSummary(BaseModel):
    """Whether systemd is well."""

    state: str | None = None
    failed_units: list[str] | None = None
    error: str | None = None


class CapabilitiesOut(BaseModel):
    """
    What this machine can do, so the console degrades with a message and not
    with an error.
    """

    packages: str
    updates: bool
    security_updates: bool
    transactional: bool
    container: str | None = None
    systemd: bool
    swap: bool
    docker: bool


class SummaryOut(BaseModel):
    """
    The server in one look.

    What the overview and a fleet's server list read. Cheap by construction:
    what is slow is computed in the background and shows here with its age.
    ``hardening`` is filled by the security checks when they are installed.
    """

    hostname: str
    os: OsOut
    kernel: str
    uptime_seconds: float | None = None
    updates: UpdatesSummary
    reboot: RebootSummary
    stale_services: int | None = None
    auto_updates: AutoUpdatesSummary
    disk: DiskSummary
    time: TimeSummary
    swap: SwapSummary
    power: PowerSummary
    system: SystemStateSummary
    capabilities: CapabilitiesOut
    checked_at: str
    hardening: dict[str, Any] | None = None


# -- updates -------------------------------------------------------------------


class PackageOut(BaseModel):
    """One pending update."""

    name: str
    installed: str | None = None
    candidate: str
    security: bool
    kernel: bool
    origin: str = ""
    kind: str = "package"
    advisory: str | None = None
    severity: str | None = None


class RebootOut(BaseModel):
    """Whether a reboot is due, and why."""

    required: bool
    packages: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    since: str | None = None
    source: str = ""
    detector_available: bool = True


class AutoUpdatesOut(BaseModel):
    """The distribution's own automatic updates."""

    mechanism: str
    supported: bool
    installed: bool
    enabled: bool
    security_only: bool | None = None
    reboots: bool
    last_run: str | None = None
    detail: str = ""


class UpdateRunOut(BaseModel):
    """One run of an update, as it was written down."""

    id: str
    scope: str
    full: bool
    status: str
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None
    packages: list[str] = Field(default_factory=list)
    reboot_required: bool | None = None
    stale_services: list[str] = Field(default_factory=list)
    conffiles_kept: list[str] = Field(default_factory=list)
    error: str | None = None
    unit: str | None = None
    job_id: str | None = None
    actor: str | None = None
    tail: list[str] = Field(default_factory=list)


class UpdatesOut(BaseModel):
    """
    Everything the updates tab shows.

    Served from the cache with its age (``checked_at``); ``POST
    /api/server/updates/refresh`` renews the package lists and this list.
    """

    supported: bool
    reason: str | None = None
    security_scope: bool
    pending: int
    security: int
    packages: list[PackageOut]
    kept_back: list[str]
    holds: list[str]
    broken: bool
    checked_at: str | None = None
    lists_age_seconds: int | None = None
    notes: list[str]
    error: str | None = None
    reboot: RebootOut
    stale_services: list[str]
    auto: AutoUpdatesOut
    running: UpdateRunOut | None = None


class ApplyPlanOut(BaseModel):
    """What applying updates would do, before anything is touched."""

    scope: str
    full: bool
    packages: list[PackageOut]
    removals: list[str]
    impact: list[str]
    restarts_console: bool
    command: str


class RefusedRestartOut(BaseModel):
    """A service on replaced libraries that is not restarted from Noust, and why."""

    unit: str
    reason: str


class RestartPlanOut(BaseModel):
    """
    Which services on replaced libraries restart, and which do not.

    Attributes:
        services: What the update check reported.
        restart: What restarts, in order; the console's own unit last.
        refused: What does not, each with why (a reboot restarts those).
        restarts_console: The console restarts at the end: the page reconnects.
    """

    services: list[str] = Field(default_factory=list)
    restart: list[str] = Field(default_factory=list)
    refused: list[RefusedRestartOut] = Field(default_factory=list)
    restarts_console: bool = False


class RestartServicesRequest(BaseModel):
    """
    Restart services on replaced libraries.

    Attributes:
        services: The units to restart, from the update check's list; every one
            that may be restarted when omitted.
    """

    services: list[str] | None = None


class ApplyUpdatesRequest(BaseModel):
    """
    Apply updates.

    Attributes:
        scope: ``security`` or ``all``.
        full: A full upgrade (``full-upgrade``, ``dist-upgrade``), the only kind
            that may remove packages. When it would, the request is refused with
            the list until it is repeated with ``allow_removals``.
        allow_removals: The caller has read the removal list and accepts it.
    """

    scope: str = "security"
    full: bool = False
    allow_removals: bool = False


class AutoUpdatesRequest(BaseModel):
    """
    Turn the automatic updates on or off.

    Attributes:
        enabled: The wanted state.
        security_only: Apply only security updates.
    """

    enabled: bool
    security_only: bool = True


# -- power ---------------------------------------------------------------------


class CheckOut(BaseModel):
    """One thing looked at before a reboot."""

    id: str
    status: str
    message: str


class PowerOut(BaseModel):
    """The power state, and what a reboot would break."""

    scheduled: ScheduledPowerOut | None = None
    boot_id: str
    uptime_seconds: float | None = None
    mode: str | None = None
    due_at: str | None = None
    checks: list[CheckOut]


class ScheduleRequest(BaseModel):
    """
    Schedule a reboot.

    Give ``in_minutes`` or ``at``, not both. Without either the reboot is in one
    minute: the response reaches the operator and there is time to cancel.

    Attributes:
        in_minutes: Minutes from now, at least 1.
        at: A moment, ISO 8601. Without an offset it is the server's local time.
        message: What to tell logged-in users.
        force: Go ahead although a check warned. The warnings are the answer of
            a request that did not say so.
    """

    in_minutes: int | None = None
    at: str | None = None
    message: str | None = None
    force: bool = False


class ShutdownRequest(ScheduleRequest):
    """
    Schedule a shutdown.

    A powered-off VPS cannot be started from Noust, only from the provider's
    panel, so the host name has to be typed.

    Attributes:
        confirm_hostname: The server's host name, as a person would type it.
    """

    confirm_hostname: str = ""


class CancelOut(BaseModel):
    """The result of cancelling."""

    cancelled: bool


# -- storage -------------------------------------------------------------------


class MountOut(BaseModel):
    """One real filesystem."""

    mount_point: str
    device: str
    fstype: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: float
    inodes_total: int
    inodes_free: int
    inodes_percent: float
    readonly: bool
    status: str


class CandidateOut(BaseModel):
    """Something that takes space and might be freed."""

    id: str
    size_bytes: int | None = None
    reclaimable_bytes: int | None = None
    action: str | None = None
    detail: str = ""
    measured_at: str | None = None


class StorageOut(BaseModel):
    """The storage page: filesystems and what takes their space."""

    mounts: list[MountOut]
    worst: MountOut | None = None
    candidates: list[CandidateOut]
    analysis_at: str | None = None


class AnalysisOut(BaseModel):
    """The last scan of the known places."""

    measured_at: str | None = None
    candidates: list[CandidateOut] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class DockerImageOut(BaseModel):
    """An image no container uses."""

    id: str
    repository: str
    tag: str
    size: str
    containers: str


class CleanupRequest(BaseModel):
    """
    Clean one thing.

    Attributes:
        action: One of ``journal``, ``pkg-cache``, ``docker-build-cache``,
            ``docker-dangling-images``, ``docker-image``, ``releases``.
        size_mb: For ``journal``: what to vacuum it to.
        days: For ``journal``: keep this many days instead.
        target: For ``docker-image``: the image id.
        confirm: The caller has read what the action takes. Required by the
            Docker actions; ``GET /api/server/storage/cleanup/plan`` says what
            an action would do without changing anything.
    """

    action: str
    size_mb: int | None = None
    days: int | None = None
    target: str | None = None
    confirm: bool = False


class CleanupPlanOut(BaseModel):
    """What a cleanup would do."""

    action: str
    commands: list[str]
    effect: str
    needs_confirmation: bool
    items: list[str] = Field(default_factory=list)


# -- swap ----------------------------------------------------------------------


class SwapDeviceOut(BaseModel):
    """One swap area."""

    name: str
    kind: str
    size_bytes: int
    used_bytes: int
    priority: int
    noust: bool


class SwapOut(BaseModel):
    """The swap of the machine."""

    devices: list[SwapDeviceOut]
    total_bytes: int
    used_bytes: int
    swappiness: int | None = None
    memory_bytes: int
    suggested_bytes: int
    recommended: bool
    supported: bool
    reason: str
    noust_swapfile: bool
    warnings: list[str]


class CreateSwapRequest(BaseModel):
    """
    Make a swap file.

    Attributes:
        size_mb: Its size in MiB, at least 256.
        swappiness: ``vm.swappiness`` to set with it; 10 when omitted.
    """

    size_mb: int
    swappiness: int | None = None


class SwappinessRequest(BaseModel):
    """
    Set how eagerly the kernel swaps.

    Attributes:
        value: 0 to 100.
    """

    value: int


class StepsOut(BaseModel):
    """What a change did, one sentence per step."""

    steps: list[str]


# -- system --------------------------------------------------------------------


class TimeOut(BaseModel):
    """The clock."""

    timezone: str
    local_time: str
    utc: str
    ntp_supported: bool
    ntp_enabled: bool
    synchronized: bool
    local_rtc: bool
    offset_seconds: float | None = None


class TimeChangeRequest(BaseModel):
    """
    Change the time zone, the synchronisation, or both.

    Attributes:
        timezone: A tz database name such as ``Europe/Madrid``.
        ntp: Turn synchronisation on or off.
        install_ntp: Install chrony when there is no time daemon to turn on.
    """

    timezone: str | None = None
    ntp: bool | None = None
    install_ntp: bool = False


class TimeChangeOut(BaseModel):
    """What a change of the clock did."""

    time: TimeOut
    previous_timezone: str | None = None
    moved_timers: list[str] = Field(default_factory=list)
    output: str = ""


class HostnameOut(BaseModel):
    """The names of the machine."""

    hostname: str
    static: str
    pretty: str | None = None
    machine_id: str
    boot_id: str
    chassis: str | None = None
    cloud_init: bool
    cloud_init_resets: bool


class IdentityOut(BaseModel):
    """What the machine is called and what it runs."""

    hostname: HostnameOut
    os_id: str
    os_version: str
    os_name: str
    codename: str
    kernel: str
    architecture: str
    container: str | None = None
    eol: EolOut
    uptime_seconds: float | None = None
    booted_at: str | None = None
    load: list[float]
    cpu_count: int


class HostnameRequest(BaseModel):
    """
    Rename the machine.

    Attributes:
        hostname: The new name: lower-case DNS labels.
        keep_against_cloud_init: Also tell cloud-init not to rename it back.
    """

    hostname: str
    keep_against_cloud_init: bool = False


class ProcessOut(BaseModel):
    """One process."""

    pid: int
    name: str
    cpu_percent: float
    memory_percent: float
    memory_mb: float
    status: str
    user: str
    unit: str | None = None
    command: str | None = None


class UnitProcessesOut(BaseModel):
    """The processes of one unit, added up."""

    unit: str
    processes: int
    cpu_percent: float
    memory_mb: float
    memory_percent: float


class ProcessesOut(BaseModel):
    """The process list, or its grouping by unit."""

    processes: list[ProcessOut]
    units: list[UnitProcessesOut]
    total: int


# -- logs ----------------------------------------------------------------------


class JournalEntryOut(BaseModel):
    """One line of the journal."""

    timestamp: str
    priority: int
    unit: str
    message: str
    pid: int | None = None
    cursor: str


class JournalOut(BaseModel):
    """A read of the journal."""

    entries: list[JournalEntryOut]
    next_cursor: str | None = None
    truncated: bool


class JournalUnitOut(BaseModel):
    """A unit the journal can be read for."""

    name: str
    active: str
    sub: str
    failed: bool


class BootOut(BaseModel):
    """One boot the journal remembers."""

    index: int
    boot_id: str
    first: str
    last: str
