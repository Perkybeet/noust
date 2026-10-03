"""
Background job system for the Noust web interface.

Long operations - a deploy, a certbot round trip, an ``apt install``, a backup
of a whole application - cannot run inside a request: the panel would hold the
connection open for minutes and, when the handler is ``async``, would freeze
the event loop for every other client at the same time. They are queued here
and the endpoint answers ``202 Accepted`` with a job id.

**These jobs call the managers directly.** They used to spawn the ``wasm``
binary with :mod:`subprocess` and scrape its console output for progress, which
made the web layer a third implementation of the product: it needed the CLI
installed on ``PATH``, it lost every typed error, it reported progress by
matching English words in log lines, and it ran as whatever user the panel ran
as instead of through the shared command runner. The job functions below are
thin compositions of :mod:`noust.managers` and :mod:`noust.deployers`, so the
panel and the CLI now perform the same operations through the same code.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from noust.core.audit import Actor
from noust.core.audit import bind as bind_audit_context
from noust.core.audit import record as record_audit
from noust.core.exceptions import (
    BackupError,
    DeploymentError,
    NoustError,
    RollbackError,
)
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, get_fs
from noust.core.redact import Scrubber, app_secret_values, scrubber_for, secret_env_values
from noust.core.store import DeploymentTrigger, JobRecord, NoustStore, get_store

logger = logging.getLogger(__name__)

#: How many jobs may run at the same time. Deploys are IO and CPU heavy and
#: they compete with the panel itself for the machine.
MAX_CONCURRENT_JOBS = 3

#: Only the last N log entries of a job are serialised, so a chatty build does
#: not turn every poll of the jobs API into a megabyte of JSON.
MAX_SERIALISED_LOGS = 100

#: Directory job logs live in, a sibling of the ``deploy-logs`` directory
#: :class:`noust.deployers.recorder.DeploymentRecorder` uses, both next to the
#: store's database file.
JOB_LOG_DIR_NAME = "job-logs"

#: What persisting a job transition can fail with: the store's own errors, the
#: SQLite errors underneath it, and filesystem trouble around the log file.
#: Persisting must never fail the job it records - the job is real work on the
#: machine, and its history is only an account of it, the same boundary
#: :class:`noust.deployers.recorder.DeploymentRecorder` draws for deployments.
_RECORDING_ERRORS: tuple[type[Exception], ...] = (NoustError, OSError, sqlite3.Error)

#: The reason recorded on every job a panel restart orphaned. A job in
#: ``pending`` or ``running`` when the process starts was not resumed - the
#: thread that was running it is gone - and this is the whole review focus of
#: Task 1.7: it must reappear as failed, never stay "running" forever.
INTERRUPTED_REASON = "Interrupted by a panel restart"


def _error_text(exc: BaseException) -> str:
    """
    Render a failure for a job's ``error``, keeping a tool's own output.

    ``str()`` of a :class:`NoustError` carries its message and fix but not its
    ``output``: certbot's or nginx's own words, which the job page shows
    verbatim under the diagnosis and which a job otherwise lost.

    Args:
        exc: The failure.

    Returns:
        The message, then the fix, then the tool's output, as present.
    """
    if isinstance(exc, NoustError) and exc.output:
        return f"{exc}\n\n{exc.output.rstrip()}"
    return str(exc)


class JobStatus(str, Enum):
    """Job execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Statuses that close a job's log file and stop expecting further transitions.
FINISHED_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})


class JobType(str, Enum):
    """Types of background jobs."""

    DEPLOY = "deploy"
    UPDATE = "update"
    BACKUP = "backup"
    # A backup restore. A rollback is a deployment, recorded and announced
    # by the deployment recorder, and has a type of its own so that nothing
    # reporting restores reports it a second time.
    RESTORE = "restore"
    ROLLBACK = "rollback"
    PUSH = "push"
    CERT_CREATE = "cert_create"
    CERT_RENEW = "cert_renew"
    SERVICE_ACTION = "service_action"
    SITE_ACTION = "site_action"
    DELETE = "delete"
    MIGRATE = "migrate"
    ZERO_DOWNTIME = "zero_downtime"
    # A trial build of an application in the build sandbox (backlog 44).
    SANDBOX_TEST = "sandbox_test"
    # Moving an application to its own system account (3.2).
    IDENTITY_MIGRATE = "identity_migrate"
    # The server itself (backlog 29): package lists, updates, cleanup, swap.
    OS_REFRESH = "os_refresh"
    OS_UPDATE = "os_update"
    CLEANUP = "cleanup"
    SWAP = "swap"
    DISK_SCAN = "disk_scan"
    SERVER_ACTION = "server_action"
    SERVER_SECURITY = "server_security"
    DATABASE = "database"
    # A central's bulk action over several nodes (noust.web.fleet_jobs).
    FLEET = "fleet"
    # Noust updating itself (noust.managers.self_update).
    SELF_UPDATE = "self_update"
    CUSTOM = "custom"


@dataclass
class JobLogEntry:
    """
    A single log entry for a job.

    Attributes:
        timestamp: When the entry was recorded.
        level: One of ``info``, ``warning``, ``error`` or ``success``.
        message: The message itself.
        step: Progress value at the time of the entry.
    """

    timestamp: datetime
    level: str
    message: str
    step: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the entry as JSON-serialisable data.

        Returns:
            The entry as a dictionary.
        """
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "message": self.message,
            "step": self.step,
        }


@dataclass
class Job:
    """
    A unit of work executed off the request path.

    Attributes:
        id: Short identifier handed to the client.
        type: What kind of operation this is.
        name: Short human-readable name.
        description: Longer description.
        status: Current status.
        progress: Progress between 0 and ``total_steps``.
        total_steps: Denominator of ``progress``.
        current_step: Name of the step in progress.
        created_at: When the job was queued.
        started_at: When the worker picked it up.
        completed_at: When it finished, whatever the outcome.
        result: Value returned by the job function.
        error: Error message when the job failed.
        logs: Everything the job reported.
        metadata: Free-form context, such as the domain being deployed.
        actor: Who queued the job - a session id prefix, an API token name,
            or ``master`` - never a secret. None for a job the system queued
            on its own, such as a webhook-triggered deploy.
        scrubber: Removes the secrets of what the job works on from every
            log line and from the error, before either is kept anywhere.
            Log lines and errors carry raw tool output, and a job's log file,
            row and live updates are readable with the ``read`` scope.
    """

    id: str
    type: JobType
    name: str
    description: str
    status: JobStatus = JobStatus.PENDING
    progress: int = 0
    total_steps: int = 100
    current_step: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: Any | None = None
    error: str | None = None
    logs: list[JobLogEntry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    actor: str | None = None
    scrubber: Scrubber = field(default_factory=Scrubber, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the job as JSON-serialisable data.

        Returns:
            The job as a dictionary, with its log tail.
        """
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "progress": self.progress,
            "total_steps": self.total_steps,
            "current_step": self.current_step,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": self.result,
            "error": self.error,
            "logs": [log.to_dict() for log in self.logs[-MAX_SERIALISED_LOGS:]],
            "metadata": self.metadata,
            "actor": self.actor,
        }

    def add_log(self, message: str, level: str = "info", step: int | None = None) -> None:
        """
        Append a log entry to the job, scrubbed of its secrets.

        The one way a line enters a job's log, so the scrubbing here covers
        the in-memory tail, the log file and every live update alike.

        Args:
            message: The message to record.
            level: Severity, one of ``info``, ``warning``, ``error``, ``success``.
            step: Progress value to attach, defaulting to the current progress.
        """
        self.logs.append(
            JobLogEntry(
                timestamp=datetime.now(),
                level=level,
                message=self.scrubber.scrub(message),
                step=step if step is not None else self.progress,
            )
        )


class JobContext:
    """
    Handle a job function uses to report progress.

    Usage in a job function::

        def my_job(domain: str, job_context: JobContext) -> dict[str, str]:
            job_context.update("Starting", 10)
            job_context.log("Fetched source", "success")
            return {"domain": domain}
    """

    def __init__(self, job: Job, notify: Callable[[Job], None]):
        """
        Args:
            job: The job being executed.
            notify: Callback invoked after every change, used to push updates
                to subscribed WebSocket clients.
        """
        self._job = job
        self._notify = notify

    @property
    def job_id(self) -> str:
        """Identifier of the running job."""
        return self._job.id

    @property
    def is_cancelled(self) -> bool:
        """True once the job has been cancelled."""
        return self._job.status == JobStatus.CANCELLED

    def update(self, step_name: str, progress: int) -> None:
        """
        Record the step in progress.

        Args:
            step_name: Name of the step.
            progress: Progress value, clamped to the job's total.
        """
        self._job.current_step = self._job.scrubber.scrub(step_name)
        self._job.progress = min(progress, self._job.total_steps)
        self._job.add_log(step_name, "info", progress)
        self._notify(self._job)

    def log(self, message: str, level: str = "info") -> None:
        """
        Record a message without changing progress.

        Args:
            message: The message.
            level: Severity.
        """
        self._job.add_log(message, level)
        self._notify(self._job)

    def add_secret_env(self, env: Mapping[str, str]) -> None:
        """
        Scrub the secrets of these variables from every later line of the log.

        For variables the job itself makes (a new database's connection
        string), which neither its arguments nor its application's files held
        when it started. The one classifier decides what is secret: a URL's
        password, a variable whose name says so.

        Args:
            env: Variable name to value.
        """
        self._job.scrubber.add(secret_env_values(env))

    def set_result(self, value: Any) -> None:
        """
        Publish the job's result so far, before it returns.

        For a job whose result is its progress (a fleet job's state per node):
        every client watching, and the store, see it as it changes.

        Args:
            value: JSON-serialisable value; what the job returns replaces it.
        """
        self._job.result = value
        self._notify(self._job)

    def set_unit(self, unit: str) -> None:
        """
        Record the transient systemd unit this job's work runs in.

        Written to the store at once rather than with the next snapshot: the
        work in that unit can restart this console a moment later (the
        ``noust`` package among an update's), and the console that comes back
        reconciles the job with the unit instead of declaring it interrupted
        (:mod:`noust.web.job_reconcile`).

        Args:
            unit: The unit's name, without ``.service``.
        """
        self._job.metadata["unit"] = unit
        try:
            get_store().update_job(self._job.id, unit=unit)
        except _RECORDING_ERRORS as exc:
            logger.warning("Could not record the unit of job %s: %s", self._job.id, exc)
        self._notify(self._job)

    def set_metadata(self, key: str, value: Any) -> None:
        """
        Attach context to the job.

        Naming the job's ``domain`` also makes that application's secrets
        known to the job's scrubber, from this line on.

        Args:
            key: Metadata key.
            value: JSON-serialisable value.
        """
        self._job.metadata[key] = value
        if key == "domain" and isinstance(value, str) and value:
            self._job.scrubber.add(app_secret_values(value))


class JobManager:
    """
    Runs queued jobs on a worker thread, at most :data:`MAX_CONCURRENT_JOBS` at
    a time, and notifies subscribers of every state change.
    """

    _instance: JobManager | None = None

    #: Guards the singleton's one-time setup: ``__init__`` runs on every
    #: ``JobManager()`` call, but only the first one may build the queue.
    _initialized: bool = False

    def __new__(cls) -> JobManager:
        """
        Return the process-wide job manager.

        Returns:
            The singleton instance.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """Initialise the queue and start the worker, once per process."""
        if self._initialized:
            return

        self._jobs: dict[str, Job] = {}
        self._job_queue: queue.Queue[
            tuple[str, Callable[..., Any], tuple[Any, ...], dict[str, Any]]
        ] = queue.Queue()
        self._max_concurrent = MAX_CONCURRENT_JOBS
        self._running_count = 0
        self._lock = threading.Lock()
        # Serialises recording a job: the request thread that queues it and
        # the worker that starts it notify at once, and the snapshot each one
        # writes must be taken and stored as one step, or the older snapshot
        # can land last and put a running job back to pending.
        self._persist_lock = threading.Lock()
        self._subscribers: dict[str, list[Callable[[Job], None]]] = {}
        self._global_subscribers: list[Callable[[Job], None]] = []
        self._worker_thread: threading.Thread | None = None
        self._shutdown = False
        self._log_handles: dict[str, TextIO] = {}
        self._log_paths: dict[str, str] = {}
        self._initialized = True

        self._fail_interrupted_jobs()
        self._start_worker()

    @classmethod
    def reset_instance(cls) -> None:
        """
        Drop the singleton, so the next call to ``JobManager()`` starts fresh.

        This is what lets a test - and, conceptually, a real process restart -
        simulate "the panel came back up": the next construction runs
        :meth:`_fail_interrupted_jobs` again, against whatever the store says
        was pending or running.

        The worker thread of the discarded instance is asked to stop; nothing
        joins it; it is daemonic so the process does not wait on it either.
        """
        if cls._instance is not None:
            cls._instance._shutdown = True
        cls._instance = None

    def _fail_interrupted_jobs(self) -> None:
        """
        Mark jobs the previous process left pending or running as failed.

        Called once, at startup: whatever thread was running those jobs is
        gone, and a job stuck at "running" forever is how a history screen
        comes to lie about the state of the machine. A job whose work runs in a
        transient unit is the exception: the unit outlived the process (it is
        often what restarted it), so the job is reconciled with how the unit
        ends instead (:mod:`noust.web.job_reconcile`). Recording is an error
        boundary of its own - a store that cannot be reached at startup must
        not stop the panel from starting.
        """
        from noust.web import job_reconcile

        try:
            store = get_store()
            unfinished = [
                job
                for status in (JobStatus.RUNNING.value, JobStatus.PENDING.value)
                for job in store.list_jobs(limit=1000, status=status)
            ]
            restores = [job for job in unfinished if job.type == JobType.RESTORE.value]
            followed = job_reconcile.units_to_reconcile(unfinished)
            changed = store.fail_interrupted_jobs(
                INTERRUPTED_REASON, keep=[item.job.id for item in followed]
            )
        except _RECORDING_ERRORS as exc:
            logger.warning("Could not check for interrupted jobs at startup: %s", exc)
            return
        if changed:
            logger.warning("%d job(s) marked failed after a panel restart", changed)
        self._report_interrupted_restores(store, restores)
        job_reconcile.reattach(store, followed, publish=self._publish_reconciled)

    def _publish_reconciled(self, job_id: str) -> None:
        """
        Tell whoever listens that a reconciled job ended, as if it ran here.

        The event stream, the notifications and the overview hear about jobs
        through this manager's subscribers; a job that ended in a unit the
        previous console started is announced the same way.

        Args:
            job_id: The job, already recorded as ended.
        """
        # ValueError: a type or status this version does not know, or a
        # malformed date or result in the row.
        failures: tuple[type[Exception], ...] = (*_RECORDING_ERRORS, ValueError)
        try:
            record = get_store().get_job(job_id)
            if record is None:
                return
            job = Job(
                id=record.id,
                type=JobType(record.type),
                name=record.name,
                description=record.description,
                status=JobStatus(record.status),
                progress=record.progress,
                total_steps=record.total_steps,
                created_at=datetime.fromisoformat(record.created_at or datetime.now().isoformat()),
                started_at=datetime.fromisoformat(record.started_at) if record.started_at else None,
                completed_at=(
                    datetime.fromisoformat(record.finished_at) if record.finished_at else None
                ),
                result=json.loads(record.result_json) if record.result_json else None,
                error=record.error,
                metadata={"domain": record.domain} if record.domain else {},
                actor=record.actor,
            )
        except failures as exc:
            logger.warning("Could not announce the end of job %s: %s", job_id, exc)
            return
        self._notify_subscribers(job)

    @staticmethod
    def _report_interrupted_restores(store: NoustStore, jobs: list[JobRecord]) -> None:
        """
        Put on each interrupted database restore how to get its database back.

        A restore runs inside this process, so a restart mid-restore (a
        package upgrade) can leave a database dropped. Its safety copy was
        recorded before the drop, in the restore journal; this reads what is
        left of it, announces each one, and gives the job the exact command
        that loads the copy back.

        Args:
            store: The store.
            jobs: The restore jobs the previous process left running or queued.
        """
        from noust.managers.database.backups import reconcile_interrupted_restores

        try:
            found = reconcile_interrupted_restores(store)
        except _RECORDING_ERRORS as exc:
            logger.warning("Could not check for interrupted database restores: %s", exc)
            return
        for restore in found:
            for job in jobs:
                if job.name == f"Restore {restore.target}":
                    try:
                        store.update_job(job.id, error=f"{INTERRUPTED_REASON}. {restore.message}")
                    except _RECORDING_ERRORS as exc:
                        logger.warning("Could not record how to recover %s: %s", job.id, exc)

    def _start_worker(self) -> None:
        """Start the background worker thread if it is not already running."""
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._shutdown = False
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

    def _worker_loop(self) -> None:
        """Pull jobs off the queue and execute them until shutdown."""
        while not self._shutdown:
            try:
                job_id, func, args, kwargs = self._job_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            with self._lock:
                if self._running_count >= self._max_concurrent:
                    self._job_queue.put((job_id, func, args, kwargs))
                    continue
                self._running_count += 1

            try:
                self._execute_job(job_id, func, args, kwargs)
            finally:
                with self._lock:
                    self._running_count -= 1

    def _execute_job(
        self,
        job_id: str,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        """
        Run one job and record its outcome.

        Args:
            job_id: Identifier of the queued job.
            func: The job function.
            args: Positional arguments for the function.
            kwargs: Keyword arguments for the function.
        """
        job = self._jobs.get(job_id)
        if not job:
            return

        domain = job.metadata.get("domain")
        job.scrubber = scrubber_for(domain if isinstance(domain, str) else None)
        job.scrubber.add(_argument_secrets(kwargs))
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now()
        job.add_log("Job started", "info")
        self._notify_subscribers(job)

        try:
            call_kwargs = dict(kwargs)
            call_kwargs["job_context"] = JobContext(job, self._notify_subscribers)
            # A job runs on a worker thread, outside the request that queued
            # it: its host actions are linked by the job's own id instead,
            # which job.queue ties back to the request.
            with bind_audit_context(
                actor=Actor.from_label(job.actor) if job.actor else Actor.system("jobs"),
                correlation_id=f"job-{job.id}",
            ):
                job.result = func(*args, **call_kwargs)
            job.status = JobStatus.COMPLETED
            job.progress = job.total_steps
            job.add_log("Job completed successfully", "success")
        # This is the worker's error boundary: a job function is arbitrary
        # product code and a crash here must mark the job failed, never kill
        # the only worker thread.
        except Exception as exc:
            domain = job.metadata.get("domain")
            if isinstance(domain, str) and domain:
                # A deploy writes the application's .env as it runs; a value
                # generated there is only on disk by now.
                job.scrubber.add(app_secret_values(domain))
            self._log_failure(job, exc)
            job.status = JobStatus.FAILED
            job.error = job.scrubber.scrub(_error_text(exc))
            job.add_log(f"Job failed: {exc}", "error")
        finally:
            job.completed_at = datetime.now()
            self._notify_subscribers(job)

    @staticmethod
    def _log_failure(job: Job, exc: Exception) -> None:
        """
        Record a failed job in the server log, at the weight the failure deserves.

        A :class:`NoustError` is an outcome the product anticipated - a renewal
        refused because DNS does not point here, a build that failed - and it
        already carries the explanation and the fix, so it is one line: a
        traceback would only bury it. The failing tool's own output goes at
        debug. Anything else is a defect in Noust, and its traceback is what
        finding it needs.

        Args:
            job: The job that failed; its scrubber has every secret known so far.
            exc: What the job function raised.
        """
        if isinstance(exc, NoustError):
            summary = exc.message
            if exc.details:
                summary = f"{summary} ({exc.details})"
            logger.error("Job %s failed: %s", job.id, " ".join(job.scrubber.scrub(summary).split()))
            if exc.output:
                logger.debug("Job %s output:\n%s", job.id, job.scrubber.scrub(exc.output))
            return
        logger.exception("Job %s failed unexpectedly", job.id)

    def create_job(
        self,
        job_type: JobType,
        name: str,
        description: str,
        func: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        total_steps: int = 100,
        actor: str | None = None,
    ) -> Job:
        """
        Create and queue a background job.

        Args:
            job_type: Type of job.
            name: Short name.
            description: Detailed description.
            func: Function to execute. It must accept a ``job_context`` keyword.
            args: Positional arguments for the function.
            kwargs: Keyword arguments for the function.
            metadata: Additional job metadata.
            total_steps: Denominator for progress reporting.
            actor: Who queued the job, as recorded by the endpoint that
                called this - see :attr:`Job.actor`.

        Returns:
            The queued job.
        """
        job_id = str(uuid.uuid4())[:8]

        job = Job(
            id=job_id,
            type=job_type,
            name=name,
            description=description,
            total_steps=total_steps,
            metadata=metadata or {},
            actor=actor,
        )

        self._jobs[job_id] = job
        self._open_log(job_id)
        record_audit(
            "job.queue",
            actor=Actor.from_label(actor) if actor else None,
            target=f"job:{job_id}",
            details={"type": job_type.value, "name": name, "correlation": f"job-{job_id}"},
        )
        self._job_queue.put((job_id, func, args, kwargs or {}))
        self._notify_subscribers(job)

        return job

    def get_job(self, job_id: str) -> Job | None:
        """
        Look a job up by identifier.

        Args:
            job_id: The identifier.

        Returns:
            The job, or None when it is unknown or already cleaned up.
        """
        return self._jobs.get(job_id)

    def get_all_jobs(self, limit: int = 50) -> list[Job]:
        """
        List jobs, most recent first.

        Args:
            limit: Maximum number of jobs to return.

        Returns:
            The most recent jobs.
        """
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def get_active_jobs(self) -> list[Job]:
        """
        List jobs that are queued or running.

        Returns:
            The active jobs.
        """
        return [
            job
            for job in self._jobs.values()
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING)
        ]

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a job that has not started yet.

        A running job is not interrupted: it is halfway through changing the
        system, and there is no safe generic point to stop it.

        Args:
            job_id: The identifier.

        Returns:
            True when the job moved to cancelled.
        """
        job = self._jobs.get(job_id)
        if not job or job.status != JobStatus.PENDING:
            return False

        job.status = JobStatus.CANCELLED
        job.completed_at = datetime.now()
        job.add_log("Job cancelled", "warning")
        self._notify_subscribers(job)
        return True

    def subscribe(self, job_id: str, callback: Callable[[Job], None]) -> None:
        """
        Receive updates for one job.

        Args:
            job_id: The job to watch.
            callback: Called with the job after every change.
        """
        self._subscribers.setdefault(job_id, []).append(callback)

    def subscribe_all(self, callback: Callable[[Job], None]) -> None:
        """
        Receive updates for every job.

        Args:
            callback: Called with the job after every change.
        """
        self._global_subscribers.append(callback)

    def unsubscribe(self, job_id: str, callback: Callable[[Job], None]) -> None:
        """
        Stop receiving updates for one job.

        Args:
            job_id: The job being watched.
            callback: The callback to remove.
        """
        if job_id in self._subscribers:
            try:
                self._subscribers[job_id].remove(callback)
            except ValueError:
                pass

    def unsubscribe_all(self, callback: Callable[[Job], None]) -> None:
        """
        Stop receiving updates for every job.

        The counterpart to :meth:`subscribe_all`, and not optional: the panel's
        event stream subscribes once per open browser tab and an operator
        leaves the panel open for days across reconnections. Without a way to
        withdraw, every one of those leaves a callback holding a queue that
        nothing will ever read again.

        Args:
            callback: The callback to remove. Removing one that was never
                registered is not an error, so a stream can unsubscribe on the
                way out without tracking whether it got that far.
        """
        try:
            self._global_subscribers.remove(callback)
        except ValueError:
            pass

    def _notify_subscribers(self, job: Job) -> None:
        """
        Persist the job's current state and push a snapshot to everyone watching it.

        Persistence happens here, at the one chokepoint every transition and
        every log line already passes through, rather than being sprinkled
        across every place that changes a job: a step this method does not see
        is a step neither the store nor a panel restart will ever know about.

        Args:
            job: The job that changed.
        """
        self._persist(job)

        for callback in [*self._subscribers.get(job.id, []), *self._global_subscribers]:
            # A subscriber is a WebSocket push that can fail at any moment; one
            # dead client must not stop the others from being notified.
            try:
                callback(job)
            except Exception:
                logger.debug("Job subscriber failed for job %s", job.id, exc_info=True)

    def _persist(self, job: Job) -> None:
        """
        Write a job's current state to the store and its newest line to disk.

        The store write is one atomic upsert, so the very first notification -
        queueing the job, before it has run a single step - creates the row and
        every later one updates it, whichever thread gets there first. The
        snapshot is taken under :attr:`_persist_lock`, so the last write is
        always the newest state. A store that cannot be reached must not fail
        the job it is only recording.

        Args:
            job: The job that changed.
        """
        with self._persist_lock:
            if job.logs:
                latest = job.logs[-1]
                self._write_log_line(job.id, latest.message, latest.level)

            domain = job.metadata.get("domain")
            try:
                get_store().save_job(
                    JobRecord(
                        id=job.id,
                        type=job.type.value,
                        name=job.name,
                        description=job.description,
                        status=job.status.value,
                        progress=job.progress,
                        total_steps=job.total_steps,
                        domain=domain if isinstance(domain, str) else None,
                        error=job.error,
                        result_json=(
                            self._safe_json(job.result) if job.result is not None else None
                        ),
                        created_at=job.created_at.isoformat(),
                        started_at=job.started_at.isoformat() if job.started_at else None,
                        finished_at=job.completed_at.isoformat() if job.completed_at else None,
                        log_path=self._log_paths.get(job.id),
                        actor=job.actor,
                    )
                )
            except _RECORDING_ERRORS as exc:
                logger.warning("Could not persist job %s: %s", job.id, exc)

            if job.status in FINISHED_STATUSES:
                self._close_log(job.id)
                self._log_paths.pop(job.id, None)

    @staticmethod
    def _safe_json(value: Any) -> str:
        """
        Serialise a job's result for storage, without ever raising.

        Args:
            value: The job function's return value.

        Returns:
            JSON text. A value that ``json.dumps`` refuses is stringified
            first rather than losing the whole row over one field the caller
            could not have predicted.
        """
        try:
            return json.dumps(value)
        except TypeError:
            return json.dumps(str(value))

    def _log_root(self) -> Path:
        """
        Returns:
            Where job logs live: a ``job-logs`` directory next to the store's
            database file, the sibling of
            :class:`noust.deployers.recorder.DeploymentRecorder`'s
            ``deploy-logs``. Resolved fresh on every call rather than cached,
            because the store singleton it reads from can be swapped out from
            under a long-lived manager - in tests, and in principle across a
            reconfiguration.
        """
        return get_store().db_path.parent / JOB_LOG_DIR_NAME

    def _open_log(self, job_id: str) -> None:
        """
        Create the job's log file through the filesystem seam and open it.

        Mirrors :class:`noust.deployers.recorder.DeploymentRecorder`: the file
        is created empty via the seam so its mode is applied at creation and a
        rehearsal leaves nothing behind, then appended to with a plain handle.

        Args:
            job_id: Identifier of the job the log belongs to.
        """
        fs = get_fs()
        try:
            directory = self._log_root()
            fs.make_dir(directory, mode=SECRET_DIR_MODE, parents=True)
            path = directory / f"{job_id}.log"
            fs.write_text(path, "", mode=SECRET_MODE)
            if not path.exists():
                # The seam declined to create it (a rehearsal); nothing to log to.
                return
            self._log_handles[job_id] = path.open("a", encoding="utf-8")
            self._log_paths[job_id] = str(path)
        except _RECORDING_ERRORS as exc:
            logger.warning("Could not open the log file for job %s: %s", job_id, exc)

    def _write_log_line(self, job_id: str, message: str, level: str) -> None:
        """
        Append one timestamped line to the job's captured log.

        Args:
            job_id: Identifier of the job the line belongs to.
            message: The log message.
            level: Severity the line was reported at.
        """
        handle = self._log_handles.get(job_id)
        if handle is None:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            for piece in message.replace("\r", "").split("\n"):
                handle.write(f"[{stamp}] [{level.upper()}] {piece}\n")
            handle.flush()
        except OSError as exc:
            logger.warning("Could not write to the log file for job %s: %s", job_id, exc)
            self._close_log(job_id)

    def _close_log(self, job_id: str) -> None:
        """
        Close a job's log handle, tolerating one that is already gone.

        Args:
            job_id: Identifier of the job whose log is being closed.
        """
        handle = self._log_handles.pop(job_id, None)
        if handle is None:
            return
        try:
            handle.close()
        except OSError as exc:
            logger.debug("Could not close the log file for job %s: %s", job_id, exc)

    def cleanup_old_jobs(self, max_age_hours: int = 24) -> int:
        """
        Forget finished jobs older than a cutoff.

        Args:
            max_age_hours: Age above which a finished job is dropped.

        Returns:
            How many jobs were removed.
        """
        cutoff = datetime.now().timestamp() - (max_age_hours * 3600)

        to_remove = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in FINISHED_STATUSES
            and job.completed_at
            and job.completed_at.timestamp() < cutoff
        ]

        for job_id in to_remove:
            del self._jobs[job_id]
            self._subscribers.pop(job_id, None)
            self._close_log(job_id)

        return len(to_remove)


def _argument_secrets(kwargs: Mapping[str, Any]) -> list[str]:
    """
    Pick the secret values a job was handed as arguments.

    A fresh deploy's variables arrive as ``env_vars`` before any ``.env``
    exists, and the build runs with them; other jobs take a ``password``
    directly. Both are classified by name, the same way an ``.env`` is.

    Args:
        kwargs: The job function's keyword arguments.

    Returns:
        The secret values among the top-level string arguments and inside
        every mapping argument.
    """
    values = secret_env_values(kwargs)
    for argument in kwargs.values():
        if isinstance(argument, Mapping):
            values.extend(secret_env_values(argument))
    return values


def get_job_manager() -> JobManager:
    """
    Get the process-wide job manager.

    ``JobManager()`` is already the singleton constructor - ``__new__`` and
    ``__init__`` guard the one-time setup themselves - so this used to cache
    it a second time in a module global. That second cache was a second
    answer to "which manager is current": it kept handing out the previous
    instance after :meth:`JobManager.reset_instance` had already moved on,
    which is exactly the moment a test - or a real restart - needs the new
    one.

    Returns:
        The job manager, created on first use.
    """
    return JobManager()


def _require_context(job_context: JobContext | None) -> JobContext:
    """
    Assert that the job manager supplied a context.

    Args:
        job_context: The context the manager injects.

    Returns:
        The context.

    Raises:
        ValueError: When the function was called outside the job manager.
    """
    if job_context is None:
        raise ValueError("job_context is required; job functions run under the job manager")
    return job_context


def deploy_app_job(
    domain: str,
    source: str,
    app_type: str,
    port: int | None = None,
    branch: str | None = None,
    env_vars: dict[str, str] | None = None,
    webserver: str = "nginx",
    ssl: bool = True,
    subdomain_overrides: dict[str, str] | None = None,
    workspace_filter: list[str] | None = None,
    skip_database: bool = False,
    compose_file: str | None = None,
    compose_profiles: list[str] | None = None,
    layout: str | None = None,
    include_www: bool = False,
    persistent_paths: list[str] | None = None,
    memory_max_mb: int | None = None,
    cpu_quota_percent: int | None = None,
    tasks_max: int | None = None,
    package_manager: str | None = None,
    trigger: str = "panel",
    github_installation_id: int | None = None,
    preview_parent: str | None = None,
    env_secret_marks: dict[str, bool] | None = None,
    recipe: str | None = None,
    health_path: str | None = None,
    health_expect: str | None = None,
    health_timeout: int | None = None,
    database: dict[str, Any] | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Deploy an application through the deployer registry.

    The deployer-specific options are handed to ``configure`` for every type:
    the deployer interface's contract is that a deployer ignores the options
    that do not concern it, so the monorepo knobs reach the monorepo deployer
    and cost a Next.js one nothing.

    Args:
        domain: Target domain.
        source: Git URL or local path.
        app_type: Application type, or ``auto`` to detect it.
        port: Application port, assigned by the deployer when omitted.
        branch: Git branch.
        env_vars: Environment variables for the service.
        webserver: Web server to configure.
        ssl: Whether to obtain a certificate.
        subdomain_overrides: Monorepo: workspace name to subdomain overrides.
        workspace_filter: Monorepo: deploy only these workspaces.
        skip_database: Monorepo: skip database provisioning.
        compose_file: Docker Compose: compose file, relative to the project.
        compose_profiles: Docker Compose: profiles to activate.
        layout: ``inplace`` or ``releases``; None for the server's
            configured layout.
        include_www: Also answer on ``www.<domain>``, as a redirect.
        persistent_paths: Paths, relative to the application, linked into
            ``shared/`` and kept across every release.
        memory_max_mb: ``MemoryMax`` the unit is created with, in MB. Every
            caller of this job function is a fresh deployment (``POST
            /api/apps`` refuses a domain that already exists before queuing
            it), so these three are always "given": None means no limit, not
            "leave it alone".
        cpu_quota_percent: ``CPUQuota`` the unit is created with, in percent
            of one CPU.
        tasks_max: ``TasksMax`` the unit is created with.
        package_manager: Node package manager to install and build with
            (npm, pnpm, yarn, bun); None detects it from the project's lock
            file. Ignored by deployers that do not use one (monorepo and
            docker-compose install through their own tooling).
        trigger: What the deployment history says started it: ``panel``
            for the console's new-application wizard, ``webhook`` for a
            preview a pull request asked for.
        github_installation_id: The GitHub App installation the wizard read
            the repository through, recorded on the application once it
            exists so every update clones with it.
        preview_parent: The application this one is the pull request preview
            of, recorded on its row before the deployment runs.
        env_secret_marks: The secret marks its row starts with.
        recipe: Deploy this recipe (:mod:`noust.recipes`): its plan, the same
            one ``noust create --recipe`` uses, provisions the database and
            gives the source, type, variables (``env_vars`` over them),
            layout, persistent paths and settings; ``source``, ``app_type``,
            ``branch``, ``layout`` and ``persistent_paths`` are not used.
        health_path: Path the first health gate probes.
        health_expect: Statuses the first health gate accepts.
        health_timeout: Seconds the first health gate waits.
        database: A database to create before the first build: ``engine``,
            ``name``, ``env_var`` and ``extra_vars``, as
            :meth:`~noust.managers.database.service.DatabaseService.prepare_for_new_app`
            takes them. Its variables are written, marked secret, with
            ``env_vars``; it is linked once the application exists, and kept
            (the error says how to drop it) when the deploy fails.
        job_context: Injected by the job manager.

    Returns:
        Summary of the deployment, including ``deployment_id``: the history
        row this run wrote, or None if recording it failed. A recipe adds
        ``recipe`` and ``notes``: what to tell the operator next; a
        ``database`` adds what was created and linked, as ``database``.

    Raises:
        DeploymentError: When the deployer reports failure.
    """
    from noust.deployers import get_deployer
    from noust.deployers.helpers.layout import CONFIGURED as CONFIGURED_LAYOUT

    context = _require_context(job_context)
    context.set_metadata("domain", domain)
    context.set_metadata("app_type", app_type)

    context.update("Preparing deployment", 5)
    plan = None
    settings: dict[str, Any] = {
        "source": source,
        "branch": branch,
        "env_vars": env_vars or {},
        "layout": layout or CONFIGURED_LAYOUT,
        "persistent_paths": persistent_paths,
        "initial_health": (health_path, health_expect, health_timeout),
    }
    if recipe is not None:
        from noust.core.logger import Logger
        from noust.recipes.deploy import plan_recipe

        context.update(f"Preparing the {recipe} recipe", 7)
        plan = plan_recipe(
            recipe,
            domain,
            port=port,
            ssl=ssl,
            env_overrides=env_vars or {},
            logger=Logger(verbose=False),
        )
        app_type = plan.app_type
        context.set_metadata("app_type", app_type)
        context.set_metadata("recipe", recipe)
        # The recipe's check, with what the operator gave over it: the first
        # release is judged by it, whatever the type.
        settings = plan.configure_arguments(health=(health_path, health_expect, health_timeout))

    databases = None
    new_database = None
    database_options: dict[str, Any] = {"env_secret_marks": env_secret_marks}
    if database is not None:
        from noust.deployers.recorder import CapturingLogger
        from noust.managers.database.service import DatabaseService

        context.update(f"Creating the {database['engine']} database", 8)
        logger = CapturingLogger(verbose=False)
        logger.attach_sink(context.log)
        databases = DatabaseService(logger=logger)
        new_database = databases.prepare_for_new_app(
            domain,
            database["engine"],
            name=database.get("name"),
            env_var=database.get("env_var"),
            extra_vars=bool(database.get("extra_vars")),
            env=settings["env_vars"],
        )
        context.add_secret_env(new_database.values)
        database_options = new_database.configure_options(env_secret_marks)

    deployer = get_deployer(app_type, verbose=False)
    deployer.configure(
        domain=domain,
        port=port,
        webserver=webserver,
        ssl=ssl,
        subdomain_overrides=subdomain_overrides or {},
        workspace_filter=workspace_filter,
        skip_database=skip_database,
        compose_file=compose_file,
        compose_profiles=compose_profiles,
        trigger=trigger,
        job_id=context.job_id,
        include_www=include_www,
        **settings,
        memory_max_mb=memory_max_mb,
        cpu_quota_percent=cpu_quota_percent,
        tasks_max=tasks_max,
        resource_limits_given=True,
        package_manager=package_manager or "auto",
        preview_parent=preview_parent,
        **database_options,
    )

    context.update("Deploying", 10)
    try:
        if not deployer.deploy():
            raise DeploymentError(
                f"Deployment failed for {domain}",
                details="Check the job log and the application's unit "
                "(journalctl -u <unit>; 'noust service list' names it) for the failing step.",
            )
    except NoustError as exc:
        if databases is not None and new_database is not None:
            databases.keep_after_failed_deploy(new_database, exc)
        raise
    linked = (
        databases.link_new_app(new_database)
        if databases is not None and new_database is not None
        else None
    )
    if github_installation_id is not None:
        get_store().set_github_installation(domain, github_installation_id)

    result: dict[str, Any] = {
        "domain": domain,
        "app_type": app_type,
        "port": port,
        "status": "deployed",
        # getattr: a duck-typed test double implements configure/deploy only,
        # per the AppDeployer contract, and must not have to grow this
        # attribute just to be deployable.
        "deployment_id": getattr(deployer, "last_deployment_id", None),
    }
    if linked is not None:
        result["database"] = linked.to_dict()
    if plan is not None:
        from noust.core.logger import Logger
        from noust.recipes.deploy import finish_recipe

        result["recipe"] = recipe
        result["notes"] = finish_recipe(plan, logger=Logger(verbose=False))
        for note in result["notes"]:
            context.log(note)
    context.update("Deployment complete", 100)
    return result


def update_app_job(
    domain: str,
    commit: str | None = None,
    job_context: JobContext | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """
    Update a deployed application.

    Args:
        domain: Domain of the application to update.
        commit: Deploy this commit instead of the head of the branch: the
            console's "rebuild this deployment".
        job_context: Injected by the job manager.
        tag: Deploy the commit this tag points at.

    Returns:
        Summary of the update.

    Raises:
        NoustError: When the application is unknown or a step fails.
    """
    return run_update(domain, trigger="panel", job_context=job_context, commit=commit, tag=tag)


def run_update(
    domain: str,
    *,
    trigger: str,
    job_context: JobContext | None,
    commit: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """
    Run the shared update sequence as a job, reporting its phases as progress.

    This used to re-run the whole deploy pipeline, whose fetch deletes the
    application directory and clones it again: the ``.env`` edited here, the
    files the application had written into its own tree and its generated
    secrets were replaced on every update. The sequence is now
    :func:`noust.deployers.lifecycle.update_app`, the same one the CLI runs.

    Args:
        domain: Domain of the application to update.
        trigger: Who asked for it, recorded in the deployment history.
        job_context: Injected by the job manager.
        commit: Deploy this commit instead of the head of the branch.
        tag: Deploy the commit this tag points at: what a webhook announcing a
            release or a tag push asks for.

    Returns:
        Summary of the update.

    Raises:
        NoustError: When the application is unknown or a step fails.
    """
    from noust.deployers.lifecycle import update_app

    context = _require_context(job_context)
    context.set_metadata("domain", domain)

    if get_store().get_app(domain) is None:
        raise DeploymentError(
            f"Application not found: {domain}",
            details="Deploy it first, or check 'noust list' for the exact domain.",
        )

    outcome = update_app(
        domain,
        commit=commit,
        tag=tag,
        trigger=trigger,
        on_phase=lambda index, total, message: context.update(message, 100 * (index - 1) // total),
        on_step=context.log,
        job_id=context.job_id,
    )

    if outcome.restarted and not outcome.active:
        context.log("Restarted, but the unit is not running: check its logs", "warning")
    if outcome.warnings:
        context.log(f"Deployed with warnings: {outcome.warnings}", "warning")

    context.update("Update complete", 100)
    return {
        "domain": domain,
        "status": "updated",
        "trigger": trigger,
        "commit": commit,
        "tag": tag,
        "restarted": list(outcome.restarted),
        "active": outcome.active,
        "deployment_id": outcome.deployment_id,
        "prisma_updated": outcome.prisma_updated,
        "hooks": list(outcome.hooks),
        "schema_changed": outcome.schema_changed,
        "warnings": outcome.warnings,
    }


def delete_app_job(
    domain: str,
    remove_files: bool = True,
    remove_ssl: bool = True,
    remove_volumes: bool = False,
    remove_adopted_directory: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Remove an application, its service, its site and optionally its files.

    The same removal ``noust delete`` runs,
    :func:`noust.deployers.lifecycle.delete_app`. This job used to have its
    own: it never took a Docker Compose stack down, so the containers kept
    running from the directory it then deleted, and a unit that would not
    stop kept its unit file.

    Args:
        domain: Domain of the application.
        remove_files: Also delete the application directory.
        remove_ssl: Also delete the certificate.
        remove_volumes: Also remove a Docker Compose stack's named volumes,
            which hold its databases. Never unless asked for.
        remove_adopted_directory: The application's directory, named, when
            it is outside Noust's apps directory: only then does it go with
            the files (see :func:`noust.deployers.lifecycle.delete_app`).
        job_context: Injected by the job manager.

    Returns:
        Summary of what was removed.

    Raises:
        DeploymentError: When the application is unknown.
        AppBusyError: Another operation is running on the application.
    """
    from noust.deployers.lifecycle import delete_app

    context = _require_context(job_context)
    context.set_metadata("domain", domain)

    if get_store().get_app(domain) is None:
        raise DeploymentError(
            f"Application not found: {domain}",
            details="Nothing to delete; check 'noust list' for the exact domain.",
        )

    outcome = delete_app(
        domain,
        remove_files=remove_files,
        remove_certificate=remove_ssl,
        remove_volumes=remove_volumes,
        remove_adopted_directory=remove_adopted_directory,
        on_phase=lambda index, total, message: context.update(message, 100 * (index - 1) // total),
    )
    for warning in outcome.warnings:
        context.log(warning, "warning")
    for note in outcome.kept:
        context.log(note)
    if outcome.kept_directory is not None:
        context.log(
            f"{outcome.kept_directory} is outside Noust's apps directory: it was adopted, not "
            "created by Noust, and was kept. Name it to remove it with the application.",
            "warning",
        )

    context.update("Deletion complete", 100)
    return {
        "domain": domain,
        "status": "deleted",
        "files_removed": outcome.files_removed,
        "kept_directory": outcome.kept_directory,
        "kept": list(outcome.kept),
        "ssl_removed": remove_ssl,
        "containers_stopped": outcome.containers_stopped,
        "volumes_removed": outcome.volumes_removed,
    }


def migrate_app_job(
    domain: str,
    persist: list[str] | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Move an in-place application onto the release layout.

    Queued by ``POST /api/apps/{domain}/migrate``, like an update: the tree
    moves, the unit is stopped and started and the health check waits for it,
    which is longer than a request should hold a connection, and a job keeps
    its log when the browser goes away. The plan is worked out again when the
    job runs, so what is migrated is what is on disk then.

    Args:
        domain: Domain of the application.
        persist: Paths to keep in ``shared/``; None detects them.
        job_context: Injected by the job manager.

    Returns:
        What was done: the first release, what moved to ``shared/`` and the
        file counts before and after, plus the deployment history row.

    Raises:
        DeploymentError: A step failed or the application did not answer on
            the release layout; everything was put back, and the details say
            what, if anything, could not be.
        AppBusyError: Another operation is running on the application.
    """
    from noust.deployers import migrate as migration

    context = _require_context(job_context)
    context.set_metadata("domain", domain)

    context.update("Planning the migration", 10)
    plan = migration.plan_migration(domain, persist)
    for warning in plan.warnings:
        context.log(warning, "warning")

    context.update("Moving the tree onto releases", 30)
    result = migration.migrate(domain, plan, trigger="panel")

    context.update("Migration complete", 100)
    return {
        "domain": result.domain,
        "status": "migrated",
        "release_id": result.release_id,
        "persistent": list(result.persistent),
        "env_files": list(result.env_files),
        "files_before": result.before.files,
        "files_after": result.after.files,
        "bytes_before": result.before.bytes,
        "bytes_after": result.after.bytes,
        "unit_rewritten": result.unit_rewritten,
        "site_rewritten": result.site_rewritten,
        "deployment_id": result.deployment_id,
    }


def backup_app_job(
    domain: str,
    description: str = "",
    include_env: bool = True,
    include_node_modules: bool = False,
    include_build: bool = False,
    include_databases: bool = False,
    include_docker_volumes: bool = False,
    schemas: list[str] | None = None,
    redis_method: str = "rdb",
    tags: list[str] | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Create a backup of an application.

    Args:
        domain: Domain of the application.
        description: Free-form description stored with the backup.
        include_env: Include ``.env`` files.
        include_node_modules: Include ``node_modules``.
        include_build: Include build artefacts.
        include_databases: Include database dumps.
        include_docker_volumes: Include the application's named Docker
            volumes.
        schemas: PostgreSQL schemas to dump instead of whole databases. Not
            supported inside a self-contained backup; see
            :meth:`~noust.managers.backup_manager.BackupManager.create`.
        redis_method: How to capture Redis, ``rdb`` or ``aof``.
        tags: Tags to store with the backup.
        job_context: Injected by the job manager.

    Returns:
        Identifier, path and size of the new backup.

    Raises:
        BackupError: When the backup manager fails.
    """
    from noust.managers.backup_manager import BackupManager

    context = _require_context(job_context)
    context.set_metadata("domain", domain)
    context.update("Creating backup", 20)

    metadata = BackupManager(verbose=False).create(
        domain=domain,
        description=description,
        include_env=include_env,
        include_node_modules=include_node_modules,
        include_build=include_build,
        include_databases=include_databases,
        include_docker_volumes=include_docker_volumes,
        schemas=schemas,
        redis_method=redis_method,
        tags=tags or [],
    )

    context.update("Backup complete", 100)
    return {
        "domain": domain,
        "status": "backup_created",
        "backup_id": metadata.id,
        "size": metadata.size_bytes,
    }


def restore_backup_job(
    backup_id: str,
    target_domain: str | None = None,
    restore_env: bool = True,
    verify: bool = True,
    schema_changed_ok: bool = False,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Restore an application from a backup.

    Args:
        backup_id: Identifier of the backup to restore.
        target_domain: Domain to restore into, defaulting to the backup's own.
        restore_env: Restore the ``.env`` files from the archive.
        verify: Check the archive against its recorded checksum before
            restoring.
        schema_changed_ok: The operator confirmed putting back only the files
            past deployments that changed the schema.
        job_context: Injected by the job manager.

    Returns:
        Summary of the restore.

    Raises:
        BackupError: When the backup is unknown or the restore fails.
    """
    from noust.managers.backup_manager import BackupManager

    context = _require_context(job_context)
    context.set_metadata("backup_id", backup_id)

    manager = BackupManager(verbose=False)
    backup = manager.get_backup(backup_id)
    if backup is None:
        raise BackupError(
            f"Backup not found: {backup_id}",
            details="List the available backups with 'noust backup list'.",
        )

    domain = target_domain or backup.domain
    context.set_metadata("domain", domain)
    context.update("Restoring backup", 30)

    if not manager.restore(
        backup_id=backup_id,
        target_domain=domain,
        restore_env=restore_env,
        verify_checksum=verify,
        schema_changed_ok=schema_changed_ok,
    ):
        raise BackupError(
            f"Restore failed for backup {backup_id}",
            details="Verify the archive with 'noust backup verify' and retry.",
        )

    context.update("Restore complete", 100)
    return {"domain": domain, "backup_id": backup_id, "status": "restored"}


def push_backup_job(
    backup_id: str,
    destination_name: str,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Upload a local backup to a remote destination.

    Args:
        backup_id: Identifier of the local backup to upload.
        destination_name: Destination to upload it to.
        job_context: Injected by the job manager.

    Returns:
        Summary of the upload: files sent and any remote retention applied.

    Raises:
        BackupError: When the backup or the destination is unknown, or the
            upload, its verification, or retention fails.
        DependencyError: When rclone is not installed.
    """
    from noust.managers.backup_destinations import BackupDestinationManager
    from noust.managers.backup_manager import BackupManager

    context = _require_context(job_context)
    context.set_metadata("backup_id", backup_id)
    context.set_metadata("destination", destination_name)

    manager = BackupManager(verbose=False)
    backup = manager.get_backup(backup_id)
    if backup is None:
        raise BackupError(
            f"Backup not found: {backup_id}",
            details="List the available backups with 'noust backup list'.",
        )
    context.set_metadata("domain", backup.domain)
    context.update(f"Uploading to {destination_name}", 20)

    summary = BackupDestinationManager().push(backup, destination_name, backup_manager=manager)

    context.update("Upload complete", 100)
    return {"domain": backup.domain, "backup_id": backup_id, **summary}


def restore_from_destination_job(
    destination_name: str,
    backup_id: str,
    app_name: str,
    target_domain: str | None = None,
    restore_env: bool = True,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Download a backup from a remote destination and restore it.

    Args:
        destination_name: Destination to download from.
        backup_id: Identifier of the backup to restore.
        app_name: Application the backup belongs to, to locate it on the
            destination.
        target_domain: Domain to restore into, defaulting to the one recorded
            in the downloaded backup's own metadata.
        restore_env: Restore the ``.env`` files from the archive.
        job_context: Injected by the job manager.

    Returns:
        Summary of the restore.

    Raises:
        BackupError: When the download, its checksum, or the restore fails.
        DependencyError: When rclone is not installed.
    """
    from noust.managers.backup_destinations import BackupDestinationManager

    context = _require_context(job_context)
    context.set_metadata("backup_id", backup_id)
    context.set_metadata("destination", destination_name)
    context.update("Downloading and restoring backup", 10)

    # The one remote restore: staged under the backup directory with a free
    # space check, the sidecar matched to its folder, cleaned up afterwards.
    domain = BackupDestinationManager().restore_remote(
        destination_name,
        backup_id,
        app_name,
        target_domain=target_domain,
        restore_env=restore_env,
    )
    context.set_metadata("domain", domain)

    context.update("Restore complete", 100)
    return {
        "domain": domain,
        "backup_id": backup_id,
        "destination": destination_name,
        "status": "restored",
    }


def rollback_app_job(
    domain: str,
    backup_id: str | None = None,
    job_context: JobContext | None = None,
    schema_changed_ok: bool = False,
) -> dict[str, Any]:
    """
    Roll an application back to a previous backup.

    Args:
        domain: Domain of the application.
        backup_id: Backup to roll back to, defaulting to the most recent one.
        job_context: Injected by the job manager.
        schema_changed_ok: The operator confirmed going back past
            deployments that changed the database's schema.

    Returns:
        Summary of the rollback.

    Raises:
        RollbackError: When the rollback fails.
        SchemaChangedError: Going back past a schema change was not confirmed.
    """
    from noust.managers.backup_manager import RollbackManager

    context = _require_context(job_context)
    context.set_metadata("domain", domain)
    context.set_metadata("backup_id", backup_id)
    context.update("Rolling back", 20)

    if not RollbackManager(verbose=False).rollback(
        domain=domain, backup_id=backup_id, trigger="panel", schema_changed_ok=schema_changed_ok
    ):
        raise RollbackError(
            f"Rollback failed for {domain}",
            details="Check that a backup exists with 'noust backup list'.",
        )

    context.update("Rollback complete", 100)
    return {"domain": domain, "backup_id": backup_id, "status": "rolled_back"}


def rollback_deployment_job(
    domain: str,
    deployment_id: int,
    job_context: JobContext | None = None,
    schema_changed_ok: bool = False,
) -> dict[str, Any]:
    """
    Put back what one deployment produced: its release, or its snapshot backup.

    Args:
        domain: Domain of the application.
        deployment_id: The deployment to go back to.
        job_context: Injected by the job manager.
        schema_changed_ok: The operator confirmed going back past deployments
            that changed the database's schema.

    Returns:
        Summary of the rollback.

    Raises:
        NoustError: The deployment cannot be gone back to, or going back failed.
    """
    from noust.deployers.lifecycle import rollback_to_deployment

    context = _require_context(job_context)
    context.set_metadata("domain", domain)
    context.set_metadata("deployment_id", deployment_id)
    context.update(f"Going back to deployment {deployment_id}", 20)

    outcome = rollback_to_deployment(
        domain,
        deployment_id,
        trigger=DeploymentTrigger.PANEL.value,
        schema_changed_ok=schema_changed_ok,
    )

    context.update("Rollback complete", 100)
    return {
        "domain": domain,
        "deployment_id": deployment_id,
        "release_id": outcome.release_id,
        "backup_id": outcome.backup_id,
        "status": "rolled_back",
    }


def database_engine_job(
    engine: str,
    action: str,
    purge: bool = False,
    flavour: str | None = None,
    version: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Install or uninstall a database engine.

    Both actions drive the distribution package manager, which downloads,
    unpacks and configures; that is minutes of work and it must not happen on
    a request.

    Args:
        engine: Engine name, as registered in the database registry, or a
            flavour's (``mariadb``, ``valkey``).
        action: Either ``install`` or ``uninstall``.
        purge: Also remove configuration and data when uninstalling.
        flavour: The flavour to install; None for the install of 3.2.
        version: The version to install; None for the distribution's.
        job_context: Injected by the job manager.

    Returns:
        Summary of the operation.

    Raises:
        DatabaseEngineError: When the engine is unknown or the package manager
            fails.
    """
    from noust.core.exceptions import DatabaseEngineError
    from noust.managers.database.service import DatabaseService

    context = _require_context(job_context)
    context.set_metadata("engine", engine)
    database_service = DatabaseService()
    manager = database_service.manager(engine)

    if action == "install":
        context.update(f"Installing {manager.DISPLAY_NAME}", 20)
        outcome = database_service.install_engine(engine, flavour=flavour, version=version)
        context.update("Complete", 100)
        return {
            "engine": engine,
            "action": action,
            "status": "completed",
            "installed": outcome.to_dict(),
        }
    if action == "uninstall":
        context.update(f"Uninstalling {manager.DISPLAY_NAME}", 20)
        manager.uninstall(purge=purge)
    else:
        raise DatabaseEngineError(
            f"Unsupported engine action: {action}",
            details="Use 'install' or 'uninstall'.",
        )

    context.update("Complete", 100)
    return {"engine": engine, "action": action, "status": "completed"}


def cert_create_job(
    domain: str,
    email: str | None = None,
    domains: list[str] | None = None,
    method: str | None = None,
    webroot: str | None = None,
    include_www: bool = False,
    expand: bool = False,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Obtain a certificate for a domain.

    Reaches :meth:`~noust.managers.cert_manager.CertManager.obtain` exactly the
    way ``noust cert create`` does: this used to call the narrower ``.create()``
    convenience wrapper, which has no ``standalone`` option and always forced
    a webserver plugin, so a panel-issued certificate could not use the
    webroot or standalone methods the CLI has always offered.

    Args:
        domain: Primary domain of the certificate.
        email: Registration email.
        domains: Extra domains (SANs) to cover, beyond ``domain`` and the
            ``www`` alias ``include_www`` may add.
        method: How to prove control of the domain: ``nginx``, ``apache``,
            ``webroot`` or ``standalone``. None lets Noust pick the method
            that suits the web server it finds running, the CLI's own
            default when none of its method flags are given.
        webroot: Webroot path. Used when ``method`` is ``webroot``, or
            defaulted to :data:`~noust.managers.cert_manager.DEFAULT_WEBROOT`
            when ``method`` is ``webroot`` and no path was given.
        include_www: Also cover the ``www`` subdomain.
        expand: Expand an existing certificate even when it already covers
            every requested domain.
        job_context: Injected by the job manager.

    Returns:
        Summary of the issuance.

    Raises:
        CertificateError: When certbot fails.
    """
    from noust.managers.cert_manager import DEFAULT_WEBROOT, CertManager

    context = _require_context(job_context)
    context.set_metadata("domain", domain)
    context.update("Requesting certificate", 20)

    webroot_path: Path | None
    if webroot:
        webroot_path = Path(webroot)
    elif method == "webroot":
        webroot_path = DEFAULT_WEBROOT
    else:
        webroot_path = None

    manager = CertManager(verbose=False)
    manager.obtain(
        domain,
        email=email,
        webroot=webroot_path,
        standalone=method == "standalone",
        nginx=method == "nginx",
        apache=method == "apache",
        additional_domains=list(domains) if domains else None,
        expand=expand,
        include_www=include_www,
    )

    covered = manager.certificate_domains(domain, domains, include_www)
    context.update("Certificate created", 100)
    return {"domain": domain, "domains": covered, "status": "certificate_created"}


def cert_renew_job(
    domain: str | None = None,
    force: bool = False,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Renew one certificate, or every certificate that is due.

    Args:
        domain: Certificate name to renew, or None for all of them.
        force: Renew even when the certificate is not due yet.
        job_context: Injected by the job manager.

    Returns:
        Summary of the renewal: ``renewed`` lists each certificate renewed
        with its domains and new expiry, empty when none was due, and is None
        when certbot's list could not be read to tell.

    Raises:
        CertificateError: When certbot fails.
    """
    from noust.managers.cert_manager import CertManager

    context = _require_context(job_context)
    context.set_metadata("domain", domain or "all")
    context.update("Renewing certificates", 20)

    renewed = CertManager(verbose=False).renew_and_report(domain=domain, force=force)

    if renewed is None:
        context.log(
            "certbot's certificate list could not be read, so which ones were renewed is unknown"
        )
    elif not renewed:
        context.log("No certificate was due for renewal")
    for certificate in renewed or []:
        context.log(
            f"Renewed {certificate.name} ({', '.join(certificate.domains)}), "
            f"valid until {certificate.expiry or 'an expiry certbot did not print'}"
        )
    context.update("Renewal complete", 100)
    return {
        "domain": domain,
        "status": "renewed",
        "renewed": None
        if renewed is None
        else [{"name": c.name, "domains": list(c.domains), "expiry": c.expiry} for c in renewed],
    }
