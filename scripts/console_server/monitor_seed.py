# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Server page's monitor card, the activity log and the job history."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from noust.core.server_text import say

from .machine import Unit
from .sandbox import Sandbox

# ---------------------------------------------------------------------------
# Console: the Server page's monitor card needs an installed, running unit
# and a small findings history to show status, observations and the
# acknowledge flow; the Activity page needs at least one job with a captured
# log, since none of tests.support.panel_factory.seed_console_state's jobs record one.
# ---------------------------------------------------------------------------


def seed_monitor(
    sandbox: Sandbox,
    units: dict[str, Unit],
    *,
    showcase: bool = False,
    showcase_notice: bool = True,
) -> None:
    """
    Install the monitor's unit and give it a short findings history.

    Mirrors a machine where ``noust monitor install --enable`` has already run
    for a while: the unit active and enabled (answered by the fake runner,
    the same way an application's unit is), and a few observations already on
    record - a couple still open, one already acknowledged - for the
    console's monitor card, the acknowledge flow, and the overview's "Needs
    attention" (Task 3.1, already wired to the same endpoint).

    Args:
        sandbox: The sandbox.
        units: The modelled machine's units, mutated in place so the fake
            runner reports the monitor unit as installed, active and enabled.
        showcase: Seed at most one plausible, benign notice instead of the default
            machine's cryptominer and raw-listener findings (``--showcase``): a
            documentation screenshot of a small agency's server should not look actively
            compromised, and the failed application, the failed unit and the expiring
            certificate already carry "Needs attention" on their own.
        showcase_notice: With ``showcase``, whether to seed even that one open notice.
            False for a fleet node meant to read as calm and fully healthy (a second
            region, a staging server): every open finding, not only the alarming ones,
            counts towards "Needs attention".
    """
    from noust.monitor.models import (
        SEVERITY_NOTICE,
        SEVERITY_WARNING,
        SIGNAL_NAME_PATTERN,
        SIGNAL_RESOURCE_USAGE,
        ProcessInfo,
        ProcessObservation,
    )
    from noust.monitor.observation_store import ObservationStore
    from noust.monitor.process_monitor import ProcessMonitor

    unit_name = ProcessMonitor.SERVICE_NAME
    units[unit_name] = Unit(
        active="active", enabled=True, pid=2114, since=datetime.now() - timedelta(days=6)
    )
    (sandbox.systemd_dir / f"{unit_name}.service").write_text(
        "[Unit]\nDescription=Noust resource monitor\nAfter=network.target\n\n"
        "[Service]\nType=simple\nExecStart=/usr/bin/noust monitor run\nRestart=always\n\n"
        "[Install]\nWantedBy=multi-user.target\n",
        encoding="utf-8",
    )

    now = datetime.now()
    store = ObservationStore()
    open_observations: list[ProcessObservation] = (
        (
            [
                ProcessObservation(
                    process=ProcessInfo(
                        pid=2087,
                        name="tar",
                        user="www-data",
                        cpu_percent=76.0,
                        memory_percent=3.0,
                        command="tar czf backup.tar.gz .",
                    ),
                    signal=SIGNAL_RESOURCE_USAGE,
                    severity=SEVERITY_NOTICE,
                    detail="Short CPU spike during the nightly backup",
                    observed_at=now - timedelta(hours=3),
                ),
            ]
            if showcase_notice
            else []
        )
        if showcase
        else [
            ProcessObservation(
                process=ProcessInfo(
                    pid=41823,
                    name="node",
                    user="www-data",
                    cpu_percent=92.4,
                    memory_percent=18.2,
                    command="node server.js",
                ),
                signal=SIGNAL_RESOURCE_USAGE,
                severity=SEVERITY_WARNING,
                detail=say(
                    "monitor.observation.resource_usage",
                    cpu="92.4",
                    cpu_threshold="80.0",
                    memory="18.2",
                    memory_threshold="80.0",
                ).text,
                observed_at=now - timedelta(hours=2),
            ),
            ProcessObservation(
                process=ProcessInfo(
                    pid=9931,
                    name="xmrig",
                    user="www-data",
                    cpu_percent=100.0,
                    memory_percent=2.1,
                    command="xmrig -o pool.example.com:4444",
                ),
                signal=SIGNAL_NAME_PATTERN,
                severity=SEVERITY_WARNING,
                detail=say("monitor.observation.name_pattern", prefix="xmrig").text,
                observed_at=now - timedelta(hours=5),
            ),
            ProcessObservation(
                process=ProcessInfo(
                    pid=512,
                    name="nc",
                    user="root",
                    cpu_percent=0.1,
                    memory_percent=0.1,
                    command="nc -lvp 4444",
                ),
                signal=SIGNAL_NAME_PATTERN,
                severity=SEVERITY_NOTICE,
                detail="Listener started with a raw networking tool",
                observed_at=now - timedelta(days=2),
            ),
        ]
    )
    store.save_many(open_observations)
    # One already-dismissed row, so the observations list and "Needs
    # attention" show the difference between open and acknowledged findings.
    acknowledged_id = store.save(
        ProcessObservation(
            process=ProcessInfo(
                pid=7710,
                name="ffmpeg",
                user="www-data",
                cpu_percent=88.0,
                memory_percent=6.4,
                command="ffmpeg -i input.mp4 -f null -",
            ),
            signal=SIGNAL_RESOURCE_USAGE,
            severity=SEVERITY_NOTICE,
            detail="Short-lived CPU spike during a video conversion",
            observed_at=now - timedelta(days=3),
        )
    )
    store.acknowledge(acknowledged_id)


def seed_activity_audit_log(sandbox: Sandbox, domain: str) -> None:
    """
    Write a few audit entries beyond what a real session generates.

    Every E2E test that signs in produces one real ``auth.login`` entry
    through the actual login endpoint, but that alone is only ever one actor
    and one result. This adds the mix an operator actually sees on the
    Activity page - a refusal, and an API token acting instead of a browser
    session - written through :class:`noust.web.auth.AuditLogger`, the same
    writer the API server installs, at the path :class:`SecurityConfig`
    resolves. This runs before :func:`noust.web.server.create_app` replaces
    the global logger with its own instance over that same file, so these
    are simply older lines already in the log the server reads from - never
    a hand-written format.

    Args:
        sandbox: The sandbox.
        domain: An application named in the denied action's resource.
    """
    from noust.web.auth import AuditLogger, SecurityConfig

    audit = AuditLogger(SecurityConfig(state_dir=sandbox.state_dir).audit_log)
    # The real refusal a deploy-scoped token gets from require_scope() (auth.py) when it
    # reaches an admin-only endpoint - deleting an app needs "admin", not "deploy".
    audit.record(
        action="auth.scope",
        result="denied",
        client_ip="203.0.113.7",
        actor="token:ci-deploy",
        resource=f"/api/apps/{domain}",
        detail="scope 'deploy' below required 'admin'",
    )
    audit.record(
        action="config.update",
        result="success",
        client_ip="127.0.0.1",
        actor="master",
        resource="/api/config",
        detail="updated ssl.email",
    )


def seed_job_history(sandbox: Sandbox, store: Any, domain: str) -> None:
    """
    Add one job with a captured log, for the Activity page's log drawer.

    :func:`tests.support.panel_factory.seed_console_state` seeds a small job history
    already (a deploy, a backup, a cert renewal, a failed update), none of
    which record ``log_path``: nothing wrote a file for them to point at.
    This adds one more, the way the real job manager does - a text file next
    to the store, referenced by the record - so opening a job's log on the
    Activity page has something real to show instead of every row answering
    "no log captured".

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        domain: The application the job ran against.
    """
    from noust.core.store import JobRecord

    log_dir = sandbox.store_file.parent / "job-logs"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    unit = domain.replace(".", "-")
    log_path = log_dir / "seed-update-0001.log"
    log_path.write_text(
        f"==> Updating {domain}\n"
        "Fetching origin\n"
        "HEAD is now at 9f2c41a Update dependencies\n"
        "Installing dependencies\n"
        f"Restarting {unit}.service\n"
        f"Update of {domain} finished\n",
        encoding="utf-8",
    )
    started = datetime.now() - timedelta(minutes=45)
    store.create_job(
        JobRecord(
            # Job ids are validated as hexadecimal (JOB_ID_PATTERN in
            # web/api/jobs.py) wherever one names a single job, unlike
            # tests.support.panel_factory's own "seed0000" ids, which only ever
            # appear in the list.
            id="c0ffee01",
            type="update",
            name=f"Update {domain}",
            description=f"Updating the application at {domain}",
            actor="master",
            status="completed",
            progress=100,
            domain=domain,
            created_at=started.isoformat(),
            started_at=started.isoformat(),
            finished_at=(started + timedelta(seconds=38)).isoformat(),
            log_path=str(log_path),
        )
    )
