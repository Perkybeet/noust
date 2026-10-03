"""
System API endpoints.

Observability only. This module reports what the machine is doing; it does not
act on it. The previous version exposed ``POST /system/processes/{pid}/kill``,
which let anyone with a panel session send SIGTERM or SIGKILL to **any** pid on
the host as root - including sshd, the database and pid 1. Decision D5 of the
v1 design takes "acting on processes" out of the product: the monitor is
observability, not an antivirus, so the endpoint is gone rather than merely
restricted. Stopping something Noust manages is done through its service, which
is what ``/api/services/{name}/stop`` is for.

Handlers are synchronous: ``psutil.cpu_percent(interval=...)`` and
``disk_usage`` block, and on the event loop they would stall every other client
for the duration.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust import __version__
from noust.core.exceptions import DependencyError
from noust.managers.health import collect_health_report
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.auth import sees_command_lines
from noust.web.machine import read_machine

router = APIRouter(route_class=NoustErrorRoute)

#: Sort keys the process listing accepts.
PROCESS_SORT_KEYS = frozenset({"cpu", "memory", "pid", "name"})

#: Bytes in a gibibyte, used for every size reported by this module.
_GIB = 1024**3


class DiskInfo(BaseModel):
    """Disk usage of one mounted filesystem."""

    device: str
    mount_point: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent_used: float


class MemoryInfo(BaseModel):
    """Memory and swap usage."""

    total_gb: float
    used_gb: float
    free_gb: float
    available_gb: float
    percent_used: float
    swap_total_gb: float
    swap_used_gb: float
    swap_percent: float


class CpuInfo(BaseModel):
    """CPU count, utilisation and load average."""

    cores: int
    percent: float
    load_1min: float
    load_5min: float
    load_15min: float


class SystemInfo(BaseModel):
    """Everything the dashboard shows about the host."""

    hostname: str
    os: str
    kernel: str
    uptime: str
    cpu: CpuInfo
    memory: MemoryInfo
    disks: list[DiskInfo]


class ProcessInfo(BaseModel):
    """One process, as observed."""

    pid: int
    name: str
    cpu_percent: float
    memory_percent: float
    memory_mb: float
    status: str
    user: str
    command: str | None = None


class ProcessListResponse(BaseModel):
    """Response for the process listing."""

    processes: list[ProcessInfo]
    total: int


class InterfaceAddress(BaseModel):
    """One address configured on an interface."""

    type: str
    address: str
    netmask: str | None = None


class InterfaceInfo(BaseModel):
    """One network interface and its counters."""

    name: str
    addresses: list[InterfaceAddress] = Field(default_factory=list)
    is_up: bool = False
    speed_mbps: int = 0
    bytes_sent: int = 0
    bytes_recv: int = 0
    packets_sent: int = 0
    packets_recv: int = 0


class NetworkResponse(BaseModel):
    """Response for the network listing."""

    interfaces: list[InterfaceInfo]


class UpdateInfo(BaseModel):
    """Installed version and, when known, the one this server can install."""

    current_version: str
    #: The newest version the source this installation upgrades from (the
    #: apt or rpm repository, PyPI) can install: what an update is offered for.
    latest_version: str | None = None
    #: True only when ``latest_version`` is newer than the installed one.
    has_update: bool
    #: The latest GitHub release. Newer than ``latest_version`` while the
    #: package for this system is still being built and published.
    published_version: str | None = None
    #: ``up_to_date``, ``update_available``, ``on_the_way`` when only the
    #: published release is newer, or ``index_behind`` when a newer release
    #: exists that this server's package index has not seen yet. None when
    #: the check is disabled.
    update_state: Literal["up_to_date", "update_available", "on_the_way", "index_behind"] | None = (
        None
    )
    #: For a package manager, what this server's package index lists: what
    #: ``apt install`` (or dnf, zypper) installs until the index is refreshed.
    indexed_version: str | None = None
    #: The version the state is about: the one to install, the one the index
    #: has not seen, or the one on the way.
    announced_version: str | None = None
    update_command: str | None = None
    #: The CLI command that refreshes the package index, only while it is
    #: behind; ``POST /api/server/updates/refresh`` does the same.
    refresh_command: str | None = None
    release_url: str | None = None
    #: "checked" when the check actually ran (or the cache was used),
    #: "disabled" when the operator turned off ``updates.check`` - the panel
    #: shows that as the reason nothing about a new release is known, rather
    #: than a check that silently never happens - and "checking" when a
    #: concurrent call is already fetching and there is no cached result yet
    #: to answer with instead (:class:`~noust.core.update_checker.UpdateCheckInProgress`).
    status: str = "checked"


class MachineMemory(BaseModel):
    """Memory usage, in bytes."""

    used: int
    total: int
    percent: float


class MachineDisk(BaseModel):
    """Usage of the filesystem holding the applications, in bytes."""

    used: int
    total: int
    percent: float


class MachineUnits(BaseModel):
    """How many Noust-managed systemd units are in each state."""

    running: int
    failed: int
    stopped: int


class MachineApps(BaseModel):
    """How many deployed applications are in each state."""

    running: int
    failed: int
    stopped: int
    static: int
    unmanaged: int = Field(
        default=0,
        description="Compose stacks whose containers run while their unit is stopped",
    )


class MachineOut(BaseModel):
    """
    The machine snapshot the console's topbar reads, and the ``machine`` SSE
    event carries every five seconds. One implementation,
    :func:`noust.web.machine.read_machine`, composes it; this only describes
    its shape for the OpenAPI contract, so a REST poll and the stream can
    never disagree about what a field means.
    """

    hostname: str
    uptime_s: float
    load: tuple[float, float, float]
    load_history: list[float]
    cpu_percent: float
    memory: MachineMemory
    disk: MachineDisk
    units: MachineUnits
    apps: MachineApps


class HealthCheckOut(BaseModel):
    """One item of the health report - disk, a web server, apps, certs, memory."""

    name: str
    value: str
    status: str


class SystemHealthOut(BaseModel):
    """
    The same verdict and checks ``noust health`` prints, as JSON.

    :func:`noust.managers.health.collect_health_report` is the one
    implementation this and the CLI command both read; this model only
    describes its shape for the OpenAPI contract.
    """

    verdict: str
    checks: list[HealthCheckOut]
    issues: list[str]
    warnings: list[str]


def _psutil() -> Any:
    """
    Import psutil, turning its absence into an actionable error.

    Returns:
        The psutil module.

    Raises:
        DependencyError: When psutil is not installed.
    """
    try:
        import psutil
    except ImportError as exc:
        raise DependencyError(
            "psutil is not installed, so system metrics are unavailable",
            details="Install the web extra: pip install 'noust[web]'.",
        ) from exc
    return psutil


def _uptime() -> str:
    """
    Read the host uptime.

    Returns:
        Uptime as ``1d 2h 3m``, or ``unknown`` when /proc is unreadable.
    """
    try:
        with open("/proc/uptime") as handle:
            seconds = float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return "unknown"

    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _os_name() -> str:
    """
    Read the distribution name.

    Returns:
        The pretty name from /etc/os-release, or ``Linux``.
    """
    try:
        with open("/etc/os-release") as handle:
            for line in handle:
                key, _, value = line.strip().partition("=")
                if key == "PRETTY_NAME":
                    return value.strip('"')
    except OSError:
        pass
    return "Linux"


def _cpu_info(interval: float) -> CpuInfo:
    """
    Sample CPU utilisation.

    Args:
        interval: Sampling window in seconds. A longer window is more accurate
            and blocks the calling thread for that long, which is why these
            handlers run in the threadpool.

    Returns:
        The CPU description.
    """
    psutil = _psutil()
    load_1, load_5, load_15 = os.getloadavg()
    return CpuInfo(
        cores=psutil.cpu_count() or 1,
        percent=psutil.cpu_percent(interval=interval),
        load_1min=load_1,
        load_5min=load_5,
        load_15min=load_15,
    )


def _memory_info() -> MemoryInfo:
    """
    Sample memory and swap usage.

    Returns:
        The memory description.
    """
    psutil = _psutil()
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return MemoryInfo(
        total_gb=round(mem.total / _GIB, 2),
        used_gb=round(mem.used / _GIB, 2),
        free_gb=round(mem.free / _GIB, 2),
        available_gb=round(mem.available / _GIB, 2),
        percent_used=mem.percent,
        swap_total_gb=round(swap.total / _GIB, 2),
        swap_used_gb=round(swap.used / _GIB, 2),
        swap_percent=swap.percent,
    )


def _disk_info() -> list[DiskInfo]:
    """
    Sample usage of every mounted filesystem that can be read.

    Returns:
        One entry per readable mount point.
    """
    psutil = _psutil()
    disks: list[DiskInfo] = []
    for partition in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (PermissionError, OSError):
            # A mount point the panel cannot stat is reported by omission; it
            # is not an error worth failing the whole dashboard for.
            continue
        disks.append(
            DiskInfo(
                device=partition.device,
                mount_point=partition.mountpoint,
                total_gb=round(usage.total / _GIB, 2),
                used_gb=round(usage.used / _GIB, 2),
                free_gb=round(usage.free / _GIB, 2),
                percent_used=usage.percent,
            )
        )
    return disks


@router.get("/machine", response_model=MachineOut)
def get_machine(session: Annotated[dict, Depends(get_current_session)]) -> MachineOut:
    """
    Snapshot the host for the console's topbar.

    The same read the ``machine`` SSE event pushes every five seconds, so a
    freshly opened console has numbers before the first push, and a client
    that only ever polls this endpoint never disagrees with one that streams.

    Args:
        session: The authenticated session.

    Returns:
        The machine snapshot.
    """
    return MachineOut(**asdict(read_machine()))


@router.get("", response_model=SystemInfo)
def get_system_info(session: Annotated[dict, Depends(get_current_session)]) -> SystemInfo:
    """
    Describe the host.

    Args:
        session: The authenticated session.

    Returns:
        Hostname, kernel, uptime, CPU, memory and disks.
    """
    uname = os.uname()
    return SystemInfo(
        hostname=uname.nodename,
        os=_os_name(),
        kernel=uname.release,
        uptime=_uptime(),
        cpu=_cpu_info(0.1),
        memory=_memory_info(),
        disks=_disk_info(),
    )


@router.get("/cpu", response_model=CpuInfo)
def get_cpu_info(session: Annotated[dict, Depends(get_current_session)]) -> CpuInfo:
    """
    Sample CPU usage over half a second.

    Args:
        session: The authenticated session.

    Returns:
        The CPU description.
    """
    return _cpu_info(0.5)


@router.get("/memory", response_model=MemoryInfo)
def get_memory_info(session: Annotated[dict, Depends(get_current_session)]) -> MemoryInfo:
    """
    Report memory and swap usage.

    Args:
        session: The authenticated session.

    Returns:
        The memory description.
    """
    return _memory_info()


@router.get("/disks", response_model=list[DiskInfo])
def get_disk_info(session: Annotated[dict, Depends(get_current_session)]) -> list[DiskInfo]:
    """
    Report disk usage per mount point.

    Args:
        session: The authenticated session.

    Returns:
        One entry per readable mount point.
    """
    return _disk_info()


def _visible_command(cmdline: list[str], session: dict[str, Any]) -> str | None:
    """
    The command line of a process as this credential may see it.

    Args:
        cmdline: The process's argv, as psutil reported it.
        session: The authenticated session.

    Returns:
        The joined, truncated command line when
        :func:`~noust.web.auth.sees_command_lines` allows it; None otherwise,
        the field's existing "unknown" rather than a second representation.
    """
    if not sees_command_lines(session):
        return None
    return " ".join(cmdline[:5]) or None


@router.get("/processes", response_model=ProcessListResponse)
def get_processes(
    session: Annotated[dict, Depends(get_current_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    sort_by: Annotated[str, Query()] = "cpu",
) -> ProcessListResponse:
    """
    List running processes.

    This is a read-only view. There is deliberately no endpoint that signals a
    process; see the module docstring. The command line is only included for
    an admin-scoped credential - see :func:`_visible_command` - because argv
    routinely carries another process's secrets.

    Args:
        limit: How many processes to return after sorting.
        sort_by: One of ``cpu``, ``memory``, ``pid`` or ``name``. An unknown
            key falls back to ``cpu``.
        session: The authenticated session.

    Returns:
        The processes, sorted and truncated.
    """
    psutil = _psutil()

    processes: list[ProcessInfo] = []
    fields = [
        "pid",
        "name",
        "cpu_percent",
        "memory_percent",
        "memory_info",
        "status",
        "username",
        "cmdline",
    ]
    for proc in psutil.process_iter(fields):
        try:
            info = proc.info
            memory_info = info.get("memory_info")
            cmdline = info.get("cmdline") or []
            processes.append(
                ProcessInfo(
                    pid=info["pid"],
                    name=info.get("name") or "",
                    cpu_percent=info.get("cpu_percent") or 0.0,
                    memory_percent=info.get("memory_percent") or 0.0,
                    memory_mb=round((memory_info.rss if memory_info else 0) / (1024**2), 2),
                    status=info.get("status") or "unknown",
                    user=info.get("username") or "unknown",
                    command=_visible_command(cmdline, session),
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # The process list is a snapshot of a moving target; one entry that
            # vanished mid-iteration is normal.
            continue

    key = sort_by if sort_by in PROCESS_SORT_KEYS else "cpu"
    if key == "pid":
        processes.sort(key=lambda p: p.pid)
    elif key == "name":
        processes.sort(key=lambda p: p.name)
    elif key == "memory":
        processes.sort(key=lambda p: p.memory_percent, reverse=True)
    else:
        processes.sort(key=lambda p: p.cpu_percent, reverse=True)

    return ProcessListResponse(processes=processes[:limit], total=len(processes))


@router.get("/network", response_model=NetworkResponse)
def get_network_info(session: Annotated[dict, Depends(get_current_session)]) -> NetworkResponse:
    """
    Describe the network interfaces and their counters.

    Args:
        session: The authenticated session.

    Returns:
        One entry per interface.
    """
    psutil = _psutil()

    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True)

    interfaces: list[InterfaceInfo] = []
    for name, addresses in psutil.net_if_addrs().items():
        stat = stats.get(name)
        io = counters.get(name)

        entries: list[InterfaceAddress] = []
        for addr in addresses:
            if addr.family.name == "AF_INET":
                entries.append(
                    InterfaceAddress(type="IPv4", address=addr.address, netmask=addr.netmask)
                )
            elif addr.family.name == "AF_INET6":
                entries.append(InterfaceAddress(type="IPv6", address=addr.address))

        interfaces.append(
            InterfaceInfo(
                name=name,
                addresses=entries,
                is_up=bool(stat.isup) if stat else False,
                speed_mbps=int(stat.speed) if stat else 0,
                bytes_sent=io.bytes_sent if io else 0,
                bytes_recv=io.bytes_recv if io else 0,
                packets_sent=io.packets_sent if io else 0,
                packets_recv=io.packets_recv if io else 0,
            )
        )

    return NetworkResponse(interfaces=interfaces)


@router.get("/version", response_model=UpdateInfo)
def check_version(session: Annotated[dict, Depends(get_current_session)]) -> UpdateInfo:
    """
    Report the installed version and, when known, the released one.

    Args:
        session: The authenticated session.

    Returns:
        The version comparison and how to update, ``status="disabled"`` and
        nothing else when the operator turned ``updates.check`` off, or
        ``status="checking"`` when another call is already fetching and
        there is no cached result yet to answer with instead.
    """
    from noust.core.update_checker import UpdateChecker, UpdateCheckInProgress

    if not UpdateChecker.enabled():
        return UpdateInfo(current_version=__version__, has_update=False, status="disabled")

    try:
        check = UpdateChecker.check()
    except UpdateCheckInProgress:
        return UpdateInfo(current_version=__version__, has_update=False, status="checking")
    state = check.state
    return UpdateInfo(
        current_version=__version__,
        latest_version=check.installable,
        has_update=state == "update_available",
        published_version=check.published,
        update_state=state,
        indexed_version=check.indexed,
        announced_version=check.announced_version,
        refresh_command=check.refresh_command,
        # Only "update_available" names something installable: "on_the_way"
        # is a GitHub release the package manager has not built yet, and the
        # CLI banner (UpdateChecker._show_update_message) shows the command
        # for exactly the same state.
        update_command=check.update_command if state == "update_available" else None,
        release_url=check.release_url,
    )


@router.get("/health", response_model=SystemHealthOut)
def get_system_health(session: Annotated[dict, Depends(get_current_session)]) -> SystemHealthOut:
    """
    Report the same health verdict and checks as ``noust health``.

    Calls :func:`noust.managers.health.collect_health_report`, the function the
    CLI command itself calls, so the server card in the console can never
    disagree with what an operator sees at the terminal.

    Args:
        session: The authenticated session.

    Returns:
        The verdict, every check that ran, and the issues and warnings behind it.
    """
    report = collect_health_report(verbose=False)
    return SystemHealthOut(
        verdict=report.verdict,
        checks=[
            HealthCheckOut(name=check.name, value=check.value, status=check.status)
            for check in report.checks
        ],
        issues=report.issues,
        warnings=report.warnings,
    )


# ------------------------------------------------------------ self-update


class SelfUpdateOut(BaseModel):
    """
    What this server can do about its own Noust, and how the last update went.

    Attributes:
        current_version: The Noust answering.
        method: How it was installed: ``apt``, ``dnf``, ``yum``, ``zypper``,
            ``pip``, ``pipx``, ``source`` or ``unknown``.
        supported: Whether ``POST /api/system/update`` can update it.
        code: Why not, for a machine: ``unsupported_installation`` or
            ``container_image``.
        reason: Why not, in a sentence.
        hint: What to run instead.
        command: The command an update runs, exactly.
        last_run: The last update's record (``id``, ``status``:
            ``running``/``installed``/``succeeded``/``failed``,
            ``from_version``, ``to_version``, ``tail``, ``job_id``...).
    """

    current_version: str
    method: str
    supported: bool
    code: str | None = None
    reason: str | None = None
    hint: str | None = None
    command: list[str] | None = None
    last_run: dict[str, Any] | None = None


def _self_update_refused(refusal: Any) -> Any:
    """
    Turn a refusal into the API's error, with the command to run instead.

    Args:
        refusal: A :class:`~noust.managers.self_update.SelfUpdateRefused`.

    Returns:
        The ``HTTPException`` to raise: 409 for a run in progress, 501 for an
        installation that is not updated from here.
    """
    from fastapi import HTTPException

    return HTTPException(
        status_code=409 if refusal.code in ("already_running", "refresh_failed") else 501,
        detail={
            "error": refusal.code,
            "detail": refusal.message,
            "hint": refusal.hint,
            "fields": None,
            "output": refusal.output,
        },
    )


def self_update_job(actor: str | None = None, job_context: Any = None) -> dict[str, Any]:
    """
    Update this server's Noust, as a job that follows its transient unit.

    When the package restarts the console, this job's thread dies with it;
    the job recorded its unit, and the new console finishes it from that
    unit and the update's record (:mod:`noust.web.job_reconcile`), which
    stays the account a central reads (``GET /api/system/update``).

    Args:
        actor: Who asked.
        job_context: Injected by the job manager.

    Returns:
        The update's record.

    Raises:
        NoustError: The update was refused or failed, with its words.
    """
    from noust.core.exceptions import NoustError
    from noust.core.update_checker import UpdateChecker, UpdateCheckInProgress
    from noust.managers.self_update import SelfUpdate, SelfUpdateRefused

    context = job_context
    target: str | None = None
    try:
        target = UpdateChecker.check().installable
    except UpdateCheckInProgress:
        target = None
    manager = SelfUpdate()
    context.update(f"Refreshing and installing Noust through {manager.method()}", 10)
    try:
        record = manager.start(
            target_version=target,
            job_id=context.job_id,
            actor=actor,
            on_line=lambda line: context.log(line),
        )
    except SelfUpdateRefused as exc:
        raise NoustError(exc.message, details=exc.hint, output=exc.output) from exc
    if record.unit:
        # The package restarts this console; the one that comes back finishes
        # this job from the unit (noust.web.job_reconcile).
        context.set_unit(record.unit)
    context.update(f"Installing in {record.unit or 'this process'}", 30)
    record = manager.follow(record, lambda line: context.log(line))
    if record.status == "failed":
        raise NoustError(
            record.error or "The update failed",
            details="Read the installation's words below.",
            output="\n".join(record.tail) or None,
        )
    context.update(
        "Installed; the console restarts on the new version"
        if record.status in ("installed", "running")
        else f"Noust {record.to_version} is running",
        100,
    )
    return record.to_dict()


@router.get("/update", response_model=SelfUpdateOut)
def self_update_status(session: Annotated[dict, Depends(get_current_session)]) -> SelfUpdateOut:
    """
    Say whether this server can update its own Noust, and how the last update ended.

    A central polls this while a node updates: the record outlives the
    console's restart, and is settled by the console that comes back.

    Args:
        session: The authenticated session.

    Returns:
        The installation method, what an update runs and the last update.
    """
    from noust.managers.self_update import SelfUpdate

    return SelfUpdateOut(**SelfUpdate().status())


@router.post("/update", response_model=JobAcceptedResponse, status_code=202)
def start_self_update(
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Update this server's Noust to what its package source offers.

    Runs the one command of this installation's method, in its own systemd
    unit, as a job. Nothing in the request chooses what runs. Needs sudo mode.

    Args:
        session: The elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 501 for an installation Noust does not update itself
            (a source checkout, a container image), 409 while an update runs.
    """
    from noust.core.audit import record as record_audit
    from noust.managers.self_update import SelfUpdate, SelfUpdateRefused
    from noust.web.auth import actor_label
    from noust.web.jobs import JobType, get_job_manager

    manager = SelfUpdate()
    refusal = manager.refusal()
    if refusal is None:
        last = manager.read()
        if last is not None and manager.settle(last).status == "running":
            refusal = SelfUpdateRefused(
                "already_running",
                "Noust is already being updated on this server",
                f"Follow it with: journalctl -fu {last.unit}.service"
                if last.unit
                else "Wait for it to finish.",
            )
    if refusal is not None:
        raise _self_update_refused(refusal)
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SELF_UPDATE,
        name="Update Noust",
        description=f"Updating Noust {__version__} through {manager.method()}",
        func=self_update_job,
        kwargs={"actor": actor},
        metadata={"method": manager.method(), "from_version": __version__},
        actor=actor,
    )
    record_audit(
        "system.update",
        target="noust",
        details={"method": manager.method(), "from_version": __version__, "job": job.id},
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Update queued: it runs in its own unit and survives the console restarting",
        job=job.to_dict(),
    )
