# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bulk actions over a central's nodes: one job, with a result per node.

``POST /api/fleet/actions`` and ``noust fleet run`` are both clients of this
module, so a renewal of every certificate, a backup of every application, an
update of Noust itself or of the operating system follow the same plan, the
same batches and the same rules whichever of them started it.

A central re-implements nothing a node does (rule 3). Every step here is a
call to the node's own API, through its tunnel, with the operator's identity,
scope, role and sudo mode exactly as the proxy forwards them
(:func:`noust.web.api.node_proxy.forwarded_identity`): the node applies its own
permissions, its fleet ceiling and its own sudo mode check. What this module
adds is only the fleet's part:

- **A plan first** (:func:`plan`): which servers, in which batches, and which
  ones are skipped and why - the node does not offer the action (a 3.0 node:
  its OpenAPI schema is the source), its ceiling does not allow it, or there is
  nothing to do. Whether sudo mode is needed is read from each node's schema
  (``x-noust-requires-elevation``), as the proxy reads it.
- **Batches** of ``serial`` servers at a time (``"25%"`` works), a ``canary``
  that goes alone first and stops everything if it fails, and ``max_failures``:
  once more servers than that have failed, the servers not started yet are
  skipped as ``aborted``.
- **A record per server** (the ``fleet_jobs`` and ``fleet_job_nodes`` tables):
  its state, the node's own job ids, a result per application where the action
  has one, and the node's words verbatim. The failed servers of a job can be
  retried as a new job (:func:`retry_request`), after a restart too.
- **One fleet job per server at a time**: a server another fleet job is
  working on is skipped as ``busy``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote

from noust.core.audit import record as record_audit
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import NodeRecord, NoustStore, get_store
from noust.fleet.aggregate import (
    VERSION_PATH,
    Asker,
    Failure,
    NodeSource,
    decode_answer,
    get_aggregator,
)

if TYPE_CHECKING:
    import httpx

    from noust.fleet.nodes import NodeManager
    from noust.web.jobs import Job, JobContext

logger = logging.getLogger(__name__)

#: Seconds between looks at a node's job.
POLL_SECONDS = 2.0

#: Seconds a node's job may run before the central stops waiting for it.
NODE_JOB_TIMEOUT = 3600.0

#: Seconds a node may take to come back on its new Noust.
NOUST_UPDATE_TIMEOUT = 900.0

#: Seconds each request to a node may take during an action.
REQUEST_TIMEOUT = 60.0

#: How long the operator's sudo mode, confirmed when the job was created,
#: covers its steps: a job does not start an elevated step on a node after
#: this, it skips it (``elevation_expired``). It is a ceiling, never a grant
#: beyond what the operator holds: the job ends where the queuing session's own
#: sudo window ends when that is sooner (:func:`elevation_deadline`), because a
#: batch must not act under a confirmation that has already lapsed. The central
#: checks sudo mode through ``ensure_elevated`` before queueing, which is also
#: what keeps the session's own window open.
ELEVATION_WINDOW_SECONDS = 30 * 60

#: Most of a node's words kept per server.
MAX_OUTPUT = 64 * 1024

#: Most of them in the job's published result, which every poll carries.
MAX_PUBLISHED_OUTPUT = 4000

#: What a server's state can be in a fleet job.
NODE_STATES = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "unreachable",
    "refused",
    "interrupted",
)

#: States that count against ``max_failures`` and are retried.
FAILED_STATES = frozenset({"failed", "unreachable", "refused"})

#: Skip reasons that may not hold next time, so the server is retried.
RETRIED_SKIPS = frozenset({"aborted", "busy", "elevation_expired"})

#: A job whose worker is gone ended with the server where it was.
INTERRUPTED_REASON = "The central restarted; the node's own jobs may still be running"


def _now() -> str:
    """Returns: Now, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


class ActionFailed(NoustError):
    """A step of an action failed on a node; ``output`` carries its words."""


class NotNeeded(Exception):
    """There is nothing for the action to do on this node."""


# ------------------------------------------------------------ one node


@dataclass
class NodeRun:
    """
    What an action's step function works with on one node.

    Attributes:
        source: The node, asked as the operator.
        options: The action's checked options.
        log: Writes a line to the job's log, prefixed with the node's name.
        step: Says what is happening on the node now.
        node_jobs: The node's own job ids, as they are queued.
        items: One result per application, where the action has them.
        poll: Seconds between looks at a node's job.
        sleep: Waits (tests pass one that does not).
        clock: Monotonic clock.
    """

    source: NodeSource
    options: dict[str, Any]
    log: Callable[[str], None]
    step: Callable[[str], None]
    node_jobs: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    poll: float = POLL_SECONDS
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    @property
    def name(self) -> str:
        """The node's name."""
        return self.source.name

    def send(
        self, method: str, path: str, *, body: Any = None, params: Mapping[str, Any] | None = None
    ) -> httpx.Response:
        """
        Send one request to the node, as the operator.

        Args:
            method: HTTP method.
            path: Path on the node.
            body: JSON body.
            params: Query parameters.

        Returns:
            The answer, whatever its status except 401 and 403.

        Raises:
            Failure: The node could not be reached, or refused.
        """
        return self.source.request(
            method, path, params=params, json_body=body, timeout=REQUEST_TIMEOUT
        )

    def call(
        self, method: str, path: str, *, body: Any = None, params: Mapping[str, Any] | None = None
    ) -> Any:
        """
        Send one request and read its JSON.

        Args:
            method: HTTP method.
            path: Path on the node.
            body: JSON body.
            params: Query parameters.

        Returns:
            The decoded answer.

        Raises:
            Failure: The node could not be reached, refused, does not offer
                it, or answered an error (its words in ``output``).
        """
        response = self.send(method, path, body=body, params=params)
        return decode_answer(self.name, method, path, response)

    def follow(
        self, job_id: str, *, timeout: float = NODE_JOB_TIMEOUT, tolerate_restart: bool = False
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Wait for one of the node's jobs, relaying its log to this job's.

        Args:
            job_id: The node's job.
            timeout: Seconds to wait.
            tolerate_restart: Keep asking while the node does not answer (its
                console restarting under an update), until the timeout.

        Returns:
            The node's job as it ended, and its log's last lines.

        Raises:
            ActionFailed: The job did not end in time.
            Failure: The node could not be asked, and a restart is not expected.
        """
        seen = 0
        lines: list[str] = []
        deadline = self.clock() + timeout
        while True:
            try:
                job = self.call("GET", f"/api/jobs/{quote(job_id, safe='')}")
                log = self.call(
                    "GET", f"/api/jobs/{quote(job_id, safe='')}/log", params={"tail": 2000}
                )
            except Failure as exc:
                if not tolerate_restart or exc.kind in ("forbidden", "unsupported"):
                    raise
                job = None
            else:
                content = log.get("content") if isinstance(log, dict) else None
                lines = str(content or "").splitlines()
                for line in lines[seen:] if len(lines) >= seen else lines:
                    self.log(line)
                seen = len(lines)
                if isinstance(job, dict) and job.get("status") in (
                    "completed",
                    "failed",
                    "cancelled",
                ):
                    return job, lines
            if self.clock() > deadline:
                raise ActionFailed(
                    f"{self.name}'s job {job_id} did not finish within {timeout / 60:.0f} minutes",
                    details=f"It keeps running on the node: /n/{self.name}/activity.",
                    output="\n".join(lines[-100:]) or None,
                )
            self.sleep(self.poll)

    def run_job(self, answer: Any, what: str, **follow: Any) -> dict[str, Any]:
        """
        Follow the job a node queued, and require it to succeed.

        Args:
            answer: The node's ``202`` answer (``job_id``, or ``job.id``).
            what: What the job does, for the messages.
            **follow: Passed to :meth:`follow`.

        Returns:
            The node's job as it ended.

        Raises:
            ActionFailed: No job was queued, or it did not succeed; its error
                and the end of its log are the output.
        """
        job_id = None
        if isinstance(answer, dict):
            job_id = answer.get("job_id") or (answer.get("job") or {}).get("id")
        if not isinstance(job_id, str) or not job_id:
            raise ActionFailed(
                f"{self.name} did not queue a job for {what}",
                output=json.dumps(answer)[:MAX_PUBLISHED_OUTPUT],
            )
        self.node_jobs.append(job_id)
        job, lines = self.follow(job_id, **follow)
        if job.get("status") != "completed":
            error = str(job.get("error") or f"{what} ended {job.get('status')}")
            raise ActionFailed(
                f"{what} failed on {self.name}: {error.splitlines()[0] if error else ''}".rstrip(
                    ": "
                ),
                output="\n".join([error, *lines[-100:]]).strip() or None,
            )
        return job


def _dicts(value: Any, key: str) -> list[dict[str, Any]]:
    """
    Read a list of objects out of a node's answer.

    Args:
        value: The answer.
        key: The list's key.

    Returns:
        The objects; any other shape reads as none.
    """
    items = value.get(key) if isinstance(value, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _domains(run: NodeRun) -> list[str]:
    """
    The node's applications the action is for.

    Args:
        run: The node.

    Returns:
        Their domains: every one, or those named in the ``domains`` option.
    """
    wanted = run.options.get("domains")
    domains = [str(app.get("domain")) for app in _dicts(run.call("GET", "/api/apps"), "apps")]
    return [domain for domain in domains if not wanted or domain in wanted]


def _per_app(
    run: NodeRun, domains: Sequence[str], what: str, step: Callable[[str, dict[str, Any]], None]
) -> None:
    """
    Run a step for each application, with a result each, and fail if any failed.

    A failure on one application does not stop the others: each one is its
    own unit of work, and the operator wants to know about all of them.

    Args:
        run: The node.
        domains: The applications.
        what: What is done, for the messages (``Backing up``).
        step: Does it for one; raises on failure, or :class:`NotNeeded`.

    Raises:
        ActionFailed: Any application failed; the output names each one.
        Failure: The node itself stopped answering.
    """
    if not domains:
        raise NotNeeded(f"{run.name} has no application to act on")
    for domain in domains:
        item: dict[str, Any] = {"domain": domain, "state": "running"}
        run.items.append(item)
        run.step(f"{what} {domain}")
        try:
            step(domain, item)
        except NotNeeded as exc:
            item.update(state="skipped", reason="not_needed", message=str(exc))
        except ActionFailed as exc:
            item.update(state="failed", message=exc.message, output=exc.output)
        except Failure as exc:
            if exc.kind in ("unreachable", "refused"):
                item.update(state="failed", message=exc.message)
                raise
            item.update(state="failed", message=exc.message, output=exc.output)
        else:
            if item["state"] == "running":
                item["state"] = "succeeded"
    failed = [item for item in run.items if item["state"] == "failed"]
    if failed:
        raise ActionFailed(
            f"{len(failed)} of {len(run.items)} applications failed on {run.name}",
            output="\n\n".join(
                f"{item['domain']}: {item.get('message')}\n{item.get('output') or ''}".strip()
                for item in failed
            ),
        )


# ------------------------------------------------------------ the actions


def _certs_renew(run: NodeRun) -> None:
    """Renew every certificate that is due on the node (``POST /api/certs/renew-all``)."""
    run.step("Renewing certificates")
    answer = run.call("POST", "/api/certs/renew-all", body={"force": run.options["force"]})
    run.run_job(answer, "Certificate renewal")
    run.step("Certificates renewed")


def _backups_run(run: NodeRun) -> None:
    """Back up each application now, then verify the archive (``POST /api/backups``)."""

    def backup(domain: str, item: dict[str, Any]) -> None:
        answer = run.call("POST", "/api/backups", body={"domain": domain})
        job = run.run_job(answer, f"The backup of {domain}")
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        backup_id = result.get("backup_id") if result else None
        item["backup_id"] = backup_id
        if run.options["verify"] and isinstance(backup_id, str):
            _verify(run, backup_id, item)

    _per_app(run, _domains(run), "Backing up", backup)
    run.step(f"{len(run.items)} applications backed up")


def _verify(run: NodeRun, backup_id: str, item: dict[str, Any]) -> None:
    """
    Verify one backup on the node, and fail the item if it does not hold.

    Args:
        run: The node.
        backup_id: The backup.
        item: The application's result.

    Raises:
        ActionFailed: The archive is not what it should be; the node's
            errors are the output.
    """
    verdict = run.call("POST", f"/api/backups/{quote(backup_id, safe='')}/verify")
    valid = bool(verdict.get("valid")) if isinstance(verdict, dict) else False
    item["verified"] = valid
    if not valid:
        errors = verdict.get("errors") if isinstance(verdict, dict) else None
        raise ActionFailed(
            f"Backup {backup_id} did not verify",
            output="\n".join(str(error) for error in errors or []) or None,
        )


def _backups_verify(run: NodeRun) -> None:
    """Verify the newest backup of each application (``POST /api/backups/{id}/verify``)."""
    wanted = run.options.get("domains")
    newest: dict[str, dict[str, Any]] = {}
    for backup in _dicts(run.call("GET", "/api/backups", params={"limit": 1000}), "backups"):
        domain = str(backup.get("domain"))
        if wanted and domain not in wanted:
            continue
        if domain not in newest or str(backup.get("timestamp")) > str(
            newest[domain].get("timestamp")
        ):
            newest[domain] = backup

    def verify(domain: str, item: dict[str, Any]) -> None:
        backup_id = str(newest[domain].get("backup_id"))
        item["backup_id"] = backup_id
        _verify(run, backup_id, item)

    _per_app(run, sorted(newest), "Verifying the backup of", verify)
    run.step(f"{len(run.items)} backups verified")


def _error_code(response: httpx.Response) -> tuple[str | None, str | None]:
    """
    Read the error code and sentence out of a node's error answer.

    Args:
        response: The answer.

    Returns:
        ``error`` and ``detail``, each None when absent.
    """
    try:
        body = response.json()
    except ValueError:
        return None, None
    if isinstance(body, dict) and isinstance(body.get("detail"), dict):
        body = body["detail"]
    if not isinstance(body, dict):
        return None, None
    code, detail = body.get("error"), body.get("detail")
    return (
        code if isinstance(code, str) else None,
        detail if isinstance(detail, str) else None,
    )


def _apps_update(run: NodeRun) -> None:
    """Update each application from its branch (``POST /api/jobs/update``)."""

    def update(domain: str, item: dict[str, Any]) -> None:
        response = run.send(
            "POST", "/api/jobs/update", body={"domain": domain, "force": run.options["force"]}
        )
        if response.status_code == 409:
            code, message = _error_code(response)
            if code == "nothing_new":
                raise NotNeeded(message or f"{domain} has nothing new")
        answer = decode_answer(run.name, "POST", "/api/jobs/update", response)
        job = run.run_job(answer, f"The update of {domain}")
        result = job.get("result") if isinstance(job.get("result"), dict) else None
        if result and result.get("deployment_id") is not None:
            item["deployment_id"] = result.get("deployment_id")

    _per_app(run, _domains(run), "Updating", update)
    run.step(f"{len(run.items)} applications updated")


def _apps_restart(run: NodeRun) -> None:
    """Restart each application (``POST /api/apps/{domain}/restart``)."""

    def restart(domain: str, item: dict[str, Any]) -> None:
        answer = run.call("POST", f"/api/apps/{quote(domain, safe='')}/restart")
        if not (isinstance(answer, dict) and answer.get("success")):
            message = answer.get("message") if isinstance(answer, dict) else None
            raise ActionFailed(str(message or f"{domain} did not restart"))

    _per_app(run, _domains(run), "Restarting", restart)
    run.step(f"{len(run.items)} applications restarted")


def _noust_update(run: NodeRun) -> None:
    """
    Update the node's Noust and wait until it answers on the new version.

    The node's console restarts under its own update, so its job is not the
    account of how it ended: the update's record (``GET /api/system/update``),
    settled by the console that comes back, is.
    """
    before = run.call("GET", VERSION_PATH).get("current_version")
    answer = run.call("POST", "/api/system/update")
    job_id = answer.get("job_id") if isinstance(answer, dict) else None
    if not isinstance(job_id, str):
        raise ActionFailed(f"{run.name} did not queue its update", output=json.dumps(answer))
    run.node_jobs.append(job_id)
    run.step(f"Installing (Noust {before})")
    deadline = run.clock() + NOUST_UPDATE_TIMEOUT
    last: dict[str, Any] = {}
    while True:
        try:
            status = run.call("GET", "/api/system/update")
            record = status.get("last_run") if isinstance(status, dict) else None
            if isinstance(record, dict) and record.get("job_id") == job_id:
                last = record
                if record.get("status") == "succeeded":
                    break
                if record.get("status") == "failed":
                    raise ActionFailed(
                        str(record.get("error") or f"The update failed on {run.name}"),
                        output="\n".join(record.get("tail") or []) or None,
                    )
            elif not last:
                job = run.call("GET", f"/api/jobs/{quote(job_id, safe='')}")
                if isinstance(job, dict) and job.get("status") == "failed":
                    raise ActionFailed(
                        f"The update did not start on {run.name}",
                        output=str(job.get("error") or "") or None,
                    )
        except Failure as exc:
            # The console restarting under its update does not answer for a
            # moment; anything else is an answer.
            if exc.kind not in ("unreachable", "error", "timeout"):
                raise
        if run.clock() > deadline:
            raise ActionFailed(
                f"{run.name} did not come back on a new Noust within "
                f"{NOUST_UPDATE_TIMEOUT / 60:.0f} minutes",
                details=f"Look at the node: noust node test {run.name}",
                output="\n".join(last.get("tail") or []) or None,
            )
        run.sleep(run.poll)
    after = run.call("GET", VERSION_PATH).get("current_version")
    if after == before:
        raise ActionFailed(
            f"{run.name} still runs Noust {before}",
            output="\n".join(last.get("tail") or []) or None,
        )
    run.items.append({"from_version": before, "to_version": after, "state": "succeeded"})
    run.step(f"Noust {after} confirmed")


def _noust_update_precheck(run: NodeRun) -> None:
    """
    Skip a node that already runs the newest Noust its source offers.

    Args:
        run: The node.

    Raises:
        NotNeeded: It is up to date.
    """
    version = run.call("GET", VERSION_PATH)
    if isinstance(version, dict) and version.get("update_state") in ("up_to_date", "on_the_way"):
        raise NotNeeded(
            f"{run.name} runs Noust {version.get('current_version')}, the newest its "
            "package source offers"
        )


def _os_updates(run: NodeRun) -> None:
    """
    Apply the operating system's updates, never rebooting.

    The node runs them in its own transient unit. A ``noust`` package among
    them restarts the node's console in the middle: the central keeps asking
    while the node does not answer (``tolerate_restart``), and the console that
    comes back finishes the job from the unit, so the job is the account. A
    node older than 3.2 marks that job interrupted instead; its update record
    is then the account, waited for until it says how the update ended.
    """
    scope = run.options["scope"]
    run.step(f"Applying {scope} updates")
    answer = run.call("POST", "/api/server/updates/apply", body={"scope": scope})
    job_id = answer.get("job_id") if isinstance(answer, dict) else None
    try:
        run.run_job(answer, "The operating system update", tolerate_restart=True)
        record = None
    except ActionFailed:
        record = _settled_run(run, job_id)
        if record is None or record.get("status") != "completed":
            raise
    record = record or _update_run(run, job_id)
    reboot = bool(record and record.get("reboot_required"))
    run.items.append(
        {
            "scope": scope,
            "packages": len((record or {}).get("packages") or []),
            "reboot_required": reboot,
            "state": "succeeded",
        }
    )
    run.step("Updated; a reboot is due (not done)" if reboot else "Updated")


def _settled_run(run: NodeRun, job_id: Any) -> dict[str, Any] | None:
    """
    Wait for the node's record of an update to say how it ended.

    The job a restart marked interrupted ends before the update does: the
    record says ``running`` until the unit writes its result, seconds later.

    Args:
        run: The node.
        job_id: The node's job.

    Returns:
        The record once it is no longer running, or as it was at the deadline;
        None when there is none.
    """
    deadline = run.clock() + NOUST_UPDATE_TIMEOUT
    while True:
        record = _update_run(run, job_id)
        if record is None or record.get("status") != "running" or run.clock() > deadline:
            return record
        run.step("Waiting for the update to record how it ended")
        run.sleep(run.poll)


def _update_run(run: NodeRun, job_id: Any) -> dict[str, Any] | None:
    """
    Find the node's record of an operating system update started by one of its jobs.

    Args:
        run: The node.
        job_id: The node's job.

    Returns:
        The record, or None when there is none.
    """
    try:
        runs = run.call("GET", "/api/server/updates/runs")
    except Failure:
        return None
    for record in runs if isinstance(runs, list) else []:
        if isinstance(record, dict) and record.get("job_id") == job_id:
            return record
    return None


#: What a server's step says in the plan when its update includes Noust.
NOUST_PENDING_STEP = "Also updates Noust: the node's console will restart"

#: The package names that are Noust, the transitional ones included.
_NOUST_PACKAGES = frozenset({"noust", "wasm", "wasm-cli"})


def _os_updates_precheck(run: NodeRun) -> None:
    """
    Skip a node with nothing pending in the chosen scope.

    Args:
        run: The node.

    Raises:
        NotNeeded: Nothing is pending (as far as the node's last look knows).
    """
    try:
        summary = run.call("GET", "/api/server/summary")
    except Failure as exc:
        if exc.kind == "unsupported":
            return
        raise
    updates = summary.get("updates") if isinstance(summary, dict) else None
    if not isinstance(updates, dict):
        return
    count = updates.get("security" if run.options["scope"] == "security" else "pending")
    if count == 0:
        raise NotNeeded(f"{run.name} has no {run.options['scope']} updates pending")
    if _updates_noust(run):
        run.step(NOUST_PENDING_STEP)


def _updates_noust(run: NodeRun) -> bool:
    """
    Tell whether the node's pending updates in the chosen scope include Noust.

    Args:
        run: The node.

    Returns:
        True when a ``noust`` package is pending; False when not, or when the
        node cannot say (an older node, a listing not computed yet).
    """
    try:
        listing = run.call("GET", "/api/server/updates")
    except Failure:
        return False
    packages = listing.get("packages") if isinstance(listing, dict) else None
    security_only = run.options["scope"] == "security"
    return any(
        isinstance(package, dict)
        and package.get("name") in _NOUST_PACKAGES
        and (package.get("security") or not security_only)
        for package in packages or []
    )


def _no_options(options: Mapping[str, Any]) -> dict[str, Any]:
    """
    Check an action that takes no options.

    Args:
        options: What was given.

    Returns:
        Nothing.

    Raises:
        ValidationError: Any option was given.
    """
    if options:
        raise ValidationError(
            f"This action takes no options, not {', '.join(sorted(options))}", field="options"
        )
    return {}


def _options(
    allowed: Mapping[str, tuple[type, Any]],
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """
    Build an options checker.

    Args:
        allowed: Option name to its type and default.

    Returns:
        A function that checks and completes an action's options.
    """

    def check(options: Mapping[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(options) - set(allowed))
        if unknown:
            raise ValidationError(
                f"Unknown option(s): {', '.join(unknown)}",
                details=f"This action takes: {', '.join(sorted(allowed)) or 'nothing'}.",
                field="options",
            )
        checked: dict[str, Any] = {}
        for name, (kind, default) in allowed.items():
            value = options.get(name, default)
            if name == "domains":
                if value is not None and (
                    not isinstance(value, list) or not all(isinstance(d, str) for d in value)
                ):
                    raise ValidationError("domains is a list of domains", field="options")
            elif name == "scope":
                if value not in ("security", "all"):
                    raise ValidationError("scope is security or all", field="options")
            elif not isinstance(value, kind):
                raise ValidationError(f"{name} must be {kind.__name__}", field="options")
            checked[name] = value
        return checked

    return check


@dataclass(frozen=True)
class ActionSpec:
    """
    One bulk action.

    Attributes:
        name: Its name, as the API and the CLI take it.
        title: What it does, as the job is named.
        operations: The node's operations it uses, ``(METHOD, path template)``:
            what makes a node ``unsupported`` (its schema lacks one), what needs
            sudo mode (its schema marks one) and what the node's ceiling must
            allow.
        serial: Servers at a time by default.
        max_failures: Failed servers tolerated by default before the rest are
            skipped; None never stops.
        run: The step function for one node.
        check_options: Checks and completes the options.
        precheck: Raises :class:`NotNeeded` for a node with nothing to do.
    """

    name: str
    title: str
    operations: tuple[tuple[str, str], ...]
    serial: int
    max_failures: int | None
    run: Callable[[NodeRun], None]
    check_options: Callable[[Mapping[str, Any]], dict[str, Any]] = _no_options
    precheck: Callable[[NodeRun], None] | None = None

    def describe(self) -> dict[str, Any]:
        """Returns: The action, for ``GET /api/fleet/actions``."""
        return {
            "name": self.name,
            "title": self.title,
            "operations": [f"{method} {path}" for method, path in self.operations],
            "serial": self.serial,
            "max_failures": self.max_failures,
        }


#: The ``domains`` option: a list of applications, or None for every one.
_DOMAINS = (list, None)

#: Every bulk action, by name.
ACTIONS: dict[str, ActionSpec] = {
    spec.name: spec
    for spec in (
        ActionSpec(
            "certs_renew",
            "Renew certificates",
            (("POST", "/api/certs/renew-all"),),
            serial=4,
            max_failures=None,
            run=_certs_renew,
            check_options=_options({"force": (bool, False)}),
        ),
        ActionSpec(
            "backups_run",
            "Back up applications now",
            (("GET", "/api/apps"), ("POST", "/api/backups")),
            serial=4,
            max_failures=None,
            run=_backups_run,
            check_options=_options({"domains": _DOMAINS, "verify": (bool, True)}),
        ),
        ActionSpec(
            "backups_verify",
            "Verify the newest backups",
            (("GET", "/api/backups"), ("POST", "/api/backups/{backup_id}/verify")),
            serial=4,
            max_failures=None,
            run=_backups_verify,
            check_options=_options({"domains": _DOMAINS}),
        ),
        ActionSpec(
            "apps_update",
            "Update applications",
            (("GET", "/api/apps"), ("POST", "/api/jobs/update")),
            serial=2,
            max_failures=0,
            run=_apps_update,
            check_options=_options({"domains": _DOMAINS, "force": (bool, False)}),
        ),
        ActionSpec(
            "apps_restart",
            "Restart applications",
            (("GET", "/api/apps"), ("POST", "/api/apps/{domain}/restart")),
            serial=4,
            max_failures=None,
            run=_apps_restart,
            check_options=_options({"domains": _DOMAINS}),
        ),
        ActionSpec(
            "noust_update",
            "Update Noust",
            (("GET", "/api/system/update"), ("POST", "/api/system/update")),
            serial=1,
            max_failures=0,
            run=_noust_update,
            precheck=_noust_update_precheck,
        ),
        ActionSpec(
            "os_updates",
            "Apply operating system updates",
            (("POST", "/api/server/updates/apply"),),
            serial=1,
            max_failures=0,
            run=_os_updates,
            check_options=_options({"scope": (str, "security")}),
            precheck=_os_updates_precheck,
        ),
    )
}


# --------------------------------------------------------------- request


@dataclass
class FleetRequest:
    """
    What a bulk action is asked to do.

    Attributes:
        action: A key of :data:`ACTIONS`.
        nodes: Servers named by hand.
        labels: A label selector: servers carrying every pair are added.
        serial: Servers at a time: a number, or a percentage such as ``"25%"``;
            the action's default when None.
        max_failures: Failed servers tolerated before the rest are skipped;
            the action's default when None, ``-1`` for "never stop".
        canary: A server that goes alone first; if it fails, nothing else runs.
        options: The action's options.
        retry_of: The job whose failed servers this retries.
    """

    action: str
    nodes: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    serial: int | str | None = None
    max_failures: int | None = None
    canary: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    retry_of: str | None = None

    @property
    def spec(self) -> ActionSpec:
        """
        The action.

        Raises:
            ValidationError: For an action that does not exist.
        """
        spec = ACTIONS.get(self.action)
        if spec is None:
            raise ValidationError(
                f"Unknown fleet action: {self.action!r}",
                details=f"Use one of: {', '.join(ACTIONS)}.",
                field="action",
            )
        return spec

    def to_dict(self) -> dict[str, Any]:
        """Returns: The request as JSON-ready data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FleetRequest:
        """
        Read a request back.

        Args:
            data: What :meth:`to_dict` wrote.

        Returns:
            The request.
        """
        known = {key: data[key] for key in cls.__dataclass_fields__ if key in data}
        return cls(**known)


def batch_size(serial: int | str | None, total: int, default: int) -> int:
    """
    Read how many servers go at a time.

    Args:
        serial: A number, a percentage (``"25%"``) or None for the default.
        total: How many servers there are.
        default: The action's default.

    Returns:
        At least 1.

    Raises:
        ValidationError: For anything else.
    """
    if serial is None:
        return max(1, default)
    if isinstance(serial, str) and serial.endswith("%"):
        try:
            percent = float(serial[:-1])
        except ValueError:
            percent = -1.0
        if not 0 < percent <= 100:
            raise ValidationError("serial as a percentage is between 1% and 100%", field="serial")
        return max(1, math.ceil(total * percent / 100))
    try:
        value = int(serial)
    except (TypeError, ValueError):
        value = 0
    if value < 1:
        raise ValidationError(
            "serial is how many servers go at a time: 1 or more, or a percentage",
            field="serial",
        )
    return value


# ------------------------------------------------------------- the plan


@dataclass
class NodeState:
    """
    One server in a fleet job.

    Attributes:
        node: Its name.
        position: Its order in the plan.
        batch: Its batch; 0 is the canary's, when there is one.
        state: One of :data:`NODE_STATES`.
        reason: Why it was skipped: ``policy``, ``unsupported``,
            ``not_needed``, ``unreachable``, ``aborted``, ``busy``,
            ``elevation_expired``.
        step: What is happening, or what happened last.
        node_jobs: The node's own job ids.
        items: One result per application, where the action has them.
        error: ``code``, ``message`` and ``hint`` when it failed or was skipped.
        output: The node's words (or ssh's), verbatim.
        started_at: When it started.
        ended_at: When it ended.
        requires_elevation: The node's schema marks the action's operations as
            needing sudo mode.
    """

    node: str
    position: int
    batch: int
    state: str = "queued"
    reason: str | None = None
    step: str | None = None
    node_jobs: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    output: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    requires_elevation: bool = False

    def publishable(self) -> dict[str, Any]:
        """
        Describe the server for the job's result and the API.

        Returns:
            Every attribute, the output cut to its end, and ``href``: the
            node's activity page through the central.
        """
        data = asdict(self)
        if self.output and len(self.output) > MAX_PUBLISHED_OUTPUT:
            data["output"] = self.output[-MAX_PUBLISHED_OUTPUT:]
        data["href"] = f"/n/{quote(self.node, safe='')}/activity"
        return data


@dataclass
class Plan:
    """
    What a bulk action will do: on which servers, in which batches, and what is skipped.

    Attributes:
        request: The request.
        options: Its checked options.
        nodes: Every server, in order.
        serial: Servers at a time.
        max_failures: Failures tolerated; None never stops.
        requires_elevation: Some server's schema marks the action as needing
            sudo mode: the operator confirms it once, for the whole job.
        notes: Sentences for the operator (the central updates itself apart).
    """

    request: FleetRequest
    options: dict[str, Any]
    nodes: list[NodeState]
    serial: int
    max_failures: int | None
    requires_elevation: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def spec(self) -> ActionSpec:
        """The action."""
        return self.request.spec

    def batches(self) -> list[list[NodeState]]:
        """Returns: The servers that run, batch by batch."""
        grouped: dict[int, list[NodeState]] = {}
        for node in self.nodes:
            if node.state == "queued":
                grouped.setdefault(node.batch, []).append(node)
        return [grouped[key] for key in sorted(grouped)]

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the plan, as ``POST /api/fleet/actions?plan`` answers it.

        Returns:
            ``action``, ``title``, ``strategy``, ``options``, ``nodes``,
            ``batches`` (names), ``summary`` (``run``, ``skipped``),
            ``requires_elevation`` and ``notes``.
        """
        return {
            "action": self.spec.name,
            "title": self.spec.title,
            "strategy": {
                "serial": self.serial,
                "max_failures": self.max_failures,
                "canary": self.request.canary,
            },
            "options": self.options,
            "nodes": [node.publishable() for node in self.nodes],
            "batches": [[node.node for node in batch] for batch in self.batches()],
            "summary": {
                "run": sum(1 for node in self.nodes if node.state == "queued"),
                "skipped": sum(1 for node in self.nodes if node.state != "queued"),
            },
            "requires_elevation": self.requires_elevation,
            "notes": list(self.notes),
        }


def _template_pattern(template: str) -> str:
    """
    Compile a path template the way the proxy compiles a node's schema.

    Args:
        template: Such as ``/api/backups/{backup_id}/verify``.

    Returns:
        The pattern's source, comparable with a compiled schema's.
    """
    pieces = re.split(r"\{[^/{}]+\}", template)
    return "[^/]+".join(re.escape(piece) for piece in pieces)


def schema_of(source: NodeSource) -> Any:
    """
    Read a node's elevation map from its OpenAPI schema, as the proxy does.

    Args:
        source: The node.

    Returns:
        The compiled :class:`~noust.web.api.node_proxy.NodeSchema`.

    Raises:
        Failure: The node could not be asked, or has no schema.
    """
    from noust.web.api.node_proxy import compile_schema, node_schemas

    cached = node_schemas.cached(source.record)
    if cached is not None:
        return cached
    document = source.get("/api/openapi.json")
    if not isinstance(document, dict):
        raise Failure("error", f"{source.name} served an API schema that is not an object")
    return compile_schema(document, source.record.version)


def offers(schema: Any, method: str, template: str) -> tuple[bool, bool]:
    """
    Report whether a node's schema has an operation, and whether it needs sudo mode.

    Matched by template, exactly: a node that only has a parametrised route
    beside it does not offer a literal one.

    Args:
        schema: The node's compiled schema.
        method: The method.
        template: The path template.

    Returns:
        ``(offered, requires_elevation)``.
    """
    wanted = _template_pattern(template)
    for pattern, methods in schema.operations:
        if pattern.pattern == wanted and method.upper() in methods:
            return True, bool(methods[method.upper()])
    return False, False


def _ceiling_refuses(record: NodeRecord, spec: ActionSpec) -> str | None:
    """
    Say why a node's published ceiling does not let this central run an action.

    Args:
        record: The node, with the ceiling it last published.
        spec: The action.

    Returns:
        The sentence, or None when the ceiling allows it (or is unknown: the
        node decides anyway).
    """
    if record.access_level is None:
        return None
    from noust.fleet.policy import FleetAccess, permits, validate_access_level
    from noust.web.permissions.registry import permission_for_route

    access = FleetAccess(
        level=validate_access_level(record.access_level), host_access=bool(record.host_access)
    )
    for method, template in spec.operations:
        permission = permission_for_route(method, template)
        if permission is not None and not permits(access, permission):
            return (
                f"{record.name} lets this central do at most '{access.describe()}', "
                f"and {method} {template} needs {permission}"
            )
    return None


def preflight(
    state: NodeState, source: NodeSource, spec: ActionSpec, options: dict[str, Any]
) -> None:
    """
    Decide whether a server takes part, and whether it needs sudo mode.

    Fills ``state``: ``queued``, or ``skipped``/``unreachable``/``refused`` with
    the reason and the node's words.

    Args:
        state: The server's state.
        source: The node.
        spec: The action.
        options: The action's checked options.
    """
    refused = _ceiling_refuses(source.record, spec)
    if refused is not None:
        _skip(state, "policy", refused, "Raise it on the node with 'noust fleet access'.")
        return
    try:
        schema = schema_of(source)
        needs = False
        for method, template in spec.operations:
            offered, elevated = offers(schema, method, template)
            if not offered:
                _skip(
                    state,
                    "unsupported",
                    f"{source.name} does not offer {method} {template}",
                    "It runs an older Noust: update it first.",
                )
                return
            needs = needs or elevated
        state.requires_elevation = needs
        if spec.precheck is not None:
            # What the precheck says about the server (an update that restarts
            # its console) is its step in the plan.
            spec.precheck(
                NodeRun(
                    source=source,
                    options=options,
                    log=lambda line: None,
                    step=lambda text: setattr(state, "step", text),
                )
            )
    except NotNeeded as exc:
        _skip(state, "not_needed", str(exc), None)
    except Failure as exc:
        _failed(state, exc)


def _skip(state: NodeState, reason: str, message: str, hint: str | None) -> None:
    """
    Mark a server skipped.

    Args:
        state: The server.
        reason: Why, for a machine.
        message: Why, in a sentence.
        hint: What to do about it.
    """
    state.state = "skipped"
    state.reason = reason
    state.step = message
    state.error = {"code": reason, "message": message, "hint": hint}


def _failed(state: NodeState, exc: Failure | NoustError) -> None:
    """
    Mark a server failed, unreachable or refused, with the node's words.

    Args:
        state: The server.
        exc: What went wrong.
    """
    if isinstance(exc, Failure):
        state.state = {"unreachable": "unreachable", "timeout": "unreachable"}.get(
            exc.kind, "refused" if exc.kind in ("refused", "forbidden") else "failed"
        )
        if exc.kind == "unsupported":
            state.state = "skipped"
            state.reason = "unsupported"
        state.error = {"code": exc.code, "message": exc.message, "hint": exc.hint}
        state.output = (exc.output or None) and exc.output[:MAX_OUTPUT]
    else:
        state.state = "failed"
        state.error = {
            "code": type(exc).__name__.lower(),
            "message": exc.message,
            "hint": exc.details or None,
        }
        state.output = (exc.output or None) and exc.output[:MAX_OUTPUT]
    state.step = state.error["message"]


def resolve_targets(request: FleetRequest, manager: NodeManager) -> list[NodeRecord]:
    """
    Turn names and labels into the servers an action runs on.

    Args:
        request: The request.
        manager: The node registry.

    Returns:
        The nodes, in the registry's order, each once.

    Raises:
        ValidationError: A name is not registered, or nothing was selected.
    """
    from noust.fleet.labels import NodeLabels, parse_selector

    records = manager.list()
    names = {record.name for record in records}
    unknown = sorted(set(request.nodes) - names)
    if unknown:
        raise ValidationError(
            f"No node named {', '.join(unknown)} is registered on this central",
            details="List them with 'noust node list'.",
            field="targets",
        )
    selector = request.labels or {}
    if selector:
        selector = parse_selector(f"{key}={value}" for key, value in selector.items())
    chosen = set(request.nodes) | set(
        NodeLabels(manager.store).select(selector, [record.name for record in records])
    )
    targets = [record for record in records if record.name in chosen]
    if not targets:
        raise ValidationError(
            "No server is selected",
            details="Name servers, or give a label that some server carries (env=prod).",
            field="targets",
        )
    if request.canary is not None and request.canary not in chosen:
        raise ValidationError(
            f"The canary {request.canary} is not one of the selected servers",
            field="canary",
        )
    return targets


def plan(request: FleetRequest, *, manager: NodeManager, asker: Asker, probe: bool = True) -> Plan:
    """
    Work out what a bulk action will do, asking each server what it offers.

    Args:
        request: The request.
        manager: The node registry.
        asker: Who asks: each node's schema and prechecks are read as them.
        probe: Ask the servers (the plan shown to the operator). A job about
            to run does not: each server's preflight runs again right before
            it starts.

    Returns:
        The plan.

    Raises:
        ValidationError: An unknown action, option, server or strategy.
    """
    spec = request.spec
    options = spec.check_options(request.options or {})
    targets = resolve_targets(request, manager)
    serial = batch_size(request.serial, len(targets), spec.serial)
    if request.max_failures is None:
        max_failures = spec.max_failures
    elif request.max_failures < 0:
        max_failures = None
    else:
        max_failures = request.max_failures
    ordered = sorted(targets, key=lambda record: record.name != request.canary)
    states: list[NodeState] = []
    first_batch = 1 if request.canary else 0
    for position, record in enumerate(ordered):
        if request.canary is not None and record.name == request.canary:
            batch = 0
        else:
            batch = first_batch + (position - first_batch) // serial
        states.append(NodeState(node=record.name, position=position, batch=batch))
    if probe:
        sources = {
            record.name: NodeSource(manager, record, asker, REQUEST_TIMEOUT) for record in ordered
        }
        with ThreadPoolExecutor(max_workers=min(16, len(states))) as pool:
            list(
                pool.map(lambda state: preflight(state, sources[state.node], spec, options), states)
            )
        for state in states:
            # A server that could not be checked is shown so, not as failed:
            # nothing ran on it yet.
            if state.state == "failed":
                state.state = "skipped"
                state.reason = "error"
    notes = []
    if spec.name == "noust_update":
        notes.append(
            "This central is not updated by this job: update it last, on its own, "
            "so it can watch every server come back."
        )
    restarting = [
        state.node
        for state in states
        if state.state == "queued" and state.step == NOUST_PENDING_STEP
    ]
    if spec.name == "os_updates" and restarting:
        notes.append(
            f"Also updates Noust on {', '.join(restarting)}: each node's console will restart. "
            "Noust is installed last, and this job waits for the console to come back and "
            "reads how the update ended."
        )
    return Plan(
        request=request,
        options=options,
        nodes=states,
        serial=serial,
        max_failures=max_failures,
        requires_elevation=any(state.requires_elevation for state in states),
        notes=notes,
    )


# ------------------------------------------------------------ the record


def _pid_alive(pid: Any) -> bool:
    """
    Report whether a process still exists.

    Args:
        pid: Its id.

    Returns:
        True when it does (or cannot be told apart from one that does).
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class FleetJobs:
    """
    The record of every fleet job: its request, its status and its servers.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def create(self, job_id: str, plan: Plan, *, created_by: str | None) -> None:
        """
        Record a job and its planned servers; a second call changes nothing.

        Args:
            job_id: The job.
            plan: Its plan.
            created_by: Who asked.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO fleet_jobs (job_id, action, request, status, created_at, "
                "created_by, owner_pid, retry_of) VALUES (?, ?, ?, 'running', ?, ?, ?, ?)",
                (
                    job_id,
                    plan.spec.name,
                    json.dumps(
                        {
                            **plan.request.to_dict(),
                            "serial": plan.serial,
                            "max_failures": plan.max_failures,
                            "options": plan.options,
                        }
                    ),
                    _now(),
                    created_by,
                    os.getpid(),
                    plan.request.retry_of,
                ),
            )
            cursor.executemany(
                "INSERT OR IGNORE INTO fleet_job_nodes (job_id, node, position, batch, state, "
                "reason, step, error, output) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        job_id,
                        node.node,
                        node.position,
                        node.batch,
                        node.state,
                        node.reason,
                        node.step,
                        json.dumps(node.error) if node.error else None,
                        node.output,
                    )
                    for node in plan.nodes
                ],
            )

    def save(self, job_id: str, node: NodeState) -> None:
        """
        Record a server's state.

        Args:
            job_id: The job.
            node: The server.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE fleet_job_nodes SET state = ?, reason = ?, step = ?, node_jobs = ?, "
                "items = ?, error = ?, output = ?, started_at = ?, ended_at = ? "
                "WHERE job_id = ? AND node = ?",
                (
                    node.state,
                    node.reason,
                    node.step,
                    json.dumps(node.node_jobs),
                    json.dumps(node.items),
                    json.dumps(node.error) if node.error else None,
                    node.output,
                    node.started_at,
                    node.ended_at,
                    job_id,
                    node.node,
                ),
            )

    def finish(self, job_id: str, status: str) -> None:
        """
        Record how a job ended.

        Args:
            job_id: The job.
            status: ``succeeded``, ``failed`` or ``aborted``.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE fleet_jobs SET status = ?, finished_at = ? WHERE job_id = ?",
                (status, _now(), job_id),
            )

    def _nodes(self, job_id: str, interrupted: bool) -> list[NodeState]:
        """
        Read a job's servers.

        Args:
            job_id: The job.
            interrupted: The job's worker is gone: a server still queued or
                running reads as ``interrupted``.

        Returns:
            The servers, in order.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM fleet_job_nodes WHERE job_id = ? ORDER BY position", (job_id,))
            .fetchall()
        )
        nodes = []
        for row in rows:
            node = NodeState(
                node=row["node"],
                position=row["position"],
                batch=row["batch"],
                state=row["state"],
                reason=row["reason"],
                step=row["step"],
                node_jobs=json.loads(row["node_jobs"] or "[]"),
                items=json.loads(row["items"] or "[]"),
                error=json.loads(row["error"]) if row["error"] else None,
                output=row["output"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
            )
            if interrupted and node.state in ("queued", "running"):
                node.state = "interrupted"
                node.step = INTERRUPTED_REASON
            nodes.append(node)
        return nodes

    def get(self, job_id: str) -> dict[str, Any] | None:
        """
        Read one job, with its servers.

        Args:
            job_id: The job.

        Returns:
            ``job_id``, ``action``, ``request``, ``status``, ``created_at``,
            ``created_by``, ``finished_at``, ``retry_of``, ``summary`` and
            ``nodes``; None when there is no such job.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM fleet_jobs WHERE job_id = ?", (job_id,))
            .fetchone()
        )
        if row is None:
            return None
        status = row["status"]
        interrupted = status == "running" and not _pid_alive(row["owner_pid"])
        if interrupted:
            status = "interrupted"
        nodes = self._nodes(job_id, interrupted)
        spec = ACTIONS.get(row["action"])
        return {
            "job_id": row["job_id"],
            "action": row["action"],
            "title": spec.title if spec else row["action"],
            "request": json.loads(row["request"]),
            "status": status,
            "created_at": row["created_at"],
            "created_by": row["created_by"],
            "finished_at": row["finished_at"],
            "retry_of": row["retry_of"],
            "summary": summarize(nodes),
            "nodes": [node.publishable() for node in nodes],
        }

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        List the most recent jobs, newest first, without their servers' details.

        Args:
            limit: How many.

        Returns:
            Each job as :meth:`get` describes it, ``nodes`` left out.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT job_id FROM fleet_jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            )
            .fetchall()
        )
        jobs = []
        for row in rows:
            job = self.get(row["job_id"])
            if job is not None:
                job.pop("nodes")
                jobs.append(job)
        return jobs


def summarize(nodes: Iterable[NodeState]) -> dict[str, int]:
    """
    Count servers by state.

    Args:
        nodes: The servers.

    Returns:
        A count for every state in :data:`NODE_STATES`.
    """
    counts = dict.fromkeys(NODE_STATES, 0)
    for node in nodes:
        counts[node.state] = counts.get(node.state, 0) + 1
    return counts


def retry_request(job_id: str, jobs: FleetJobs | None = None) -> FleetRequest:
    """
    Build the request that retries a job's servers that did not get done.

    Failed, unreachable, refused and interrupted servers are retried, and those
    skipped for a reason that may not hold any more (aborted, busy, sudo mode
    expired); never those skipped by policy, as unsupported or with nothing to do.

    Args:
        job_id: The job.
        jobs: The record; the default one when None.

    Returns:
        The same action, options and strategy, on those servers.

    Raises:
        ValidationError: No such job, or nothing to retry.
    """
    job = (jobs or FleetJobs()).get(job_id)
    if job is None:
        raise ValidationError(f"No fleet job {job_id}", field="job_id")
    names = [
        node["node"]
        for node in job["nodes"]
        if node["state"] in FAILED_STATES | {"interrupted"}
        or (node["state"] == "skipped" and node["reason"] in RETRIED_SKIPS)
    ]
    if not names:
        raise ValidationError(
            f"Fleet job {job_id} has no server to retry",
            details="Every server succeeded, or was skipped for a reason a retry does not change.",
            field="job_id",
        )
    original = job["request"]
    canary = original.get("canary")
    return FleetRequest(
        action=job["action"],
        nodes=names,
        serial=original.get("serial"),
        max_failures=-1 if original.get("max_failures") is None else original["max_failures"],
        canary=canary if canary in names else None,
        options=dict(original.get("options") or {}),
        retry_of=job_id,
    )


# -------------------------------------------------------------- running


class Reporter(Protocol):
    """Where a running fleet job says what happens: the job manager, or a terminal."""

    def log(self, message: str, level: str = "info") -> None:
        """
        Write a line.

        Args:
            message: The line.
            level: ``info``, ``warning``, ``error`` or ``success``.
        """

    def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
        """
        Publish the job's state.

        Args:
            snapshot: The result so far.
            done: Servers finished.
            step: What just happened.
        """


#: Node name to the fleet job working on it, in this process.
_busy: dict[str, str] = {}
_busy_lock = threading.Lock()


def _claim(node: str, job_id: str) -> str | None:
    """
    Take a server for a job, unless another fleet job has it.

    Args:
        node: The server.
        job_id: The job.

    Returns:
        None when taken; the other job's id when busy.
    """
    with _busy_lock:
        holder = _busy.get(node)
        if holder is not None and holder != job_id:
            return holder
        _busy[node] = job_id
        return None


def _release(node: str, job_id: str) -> None:
    """
    Give a server back.

    Args:
        node: The server.
        job_id: The job that had it.
    """
    with _busy_lock:
        if _busy.get(node) == job_id:
            del _busy[node]


@dataclass
class Runner:
    """
    Runs a plan: batch after batch, each server of a batch in parallel.

    Attributes:
        plan: The plan.
        job_id: The job.
        manager: The node registry.
        asker: Who acts, with the sudo mode confirmed when the job was created.
        reporter: Where progress goes.
        elevated_until: Unix time after which no elevated step starts; None
            when sudo mode was not confirmed.
        jobs: The record.
        poll: Seconds between looks at a node's job.
        sleep: Waits.
    """

    plan: Plan
    job_id: str
    manager: NodeManager
    asker: Asker
    reporter: Reporter
    elevated_until: float | None = None
    jobs: FleetJobs = field(default_factory=FleetJobs)
    poll: float = POLL_SECONDS
    sleep: Callable[[float], None] = time.sleep
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self, status: str = "running") -> dict[str, Any]:
        """
        The job's state, as its result carries it.

        Args:
            status: The job's status.

        Returns:
            ``fleet_job``, ``action``, ``title``, ``status``, ``strategy``,
            ``options``, ``summary`` and ``nodes``.
        """
        described = self.plan.to_dict()
        return {
            "fleet_job": self.job_id,
            "action": described["action"],
            "title": described["title"],
            "status": status,
            "strategy": described["strategy"],
            "options": described["options"],
            "summary": summarize(self.plan.nodes),
            "nodes": described["nodes"],
        }

    def _changed(self, node: NodeState, step: str) -> None:
        """
        Record and publish a server's new state.

        Args:
            node: The server.
            step: What happened.
        """
        with self._lock:
            self.jobs.save(self.job_id, node)
            done = sum(1 for other in self.plan.nodes if other.state not in ("queued", "running"))
            self.reporter.publish(self.snapshot(), done, step)

    def _run_node(self, node: NodeState) -> None:
        """
        Run the action on one server, recording every transition.

        Args:
            node: The server.
        """
        spec = self.plan.spec
        name = node.node
        holder = _claim(name, self.job_id)
        if holder is not None:
            _skip(node, "busy", f"Fleet job {holder} is working on {name}", "Retry once it ends.")
            self._changed(node, f"{name}: busy")
            return
        try:
            record = self.manager.get(name)
            source = NodeSource(self.manager, record, self.asker, REQUEST_TIMEOUT)
            node.state = "queued"
            node.error = None
            node.reason = None
            preflight(node, source, spec, self.plan.options)
            if node.state != "queued":
                self._changed(node, f"{name}: {node.state} ({node.reason or ''})")
                return
            if node.requires_elevation and (
                self.elevated_until is None or time.time() > self.elevated_until
            ):
                _skip(
                    node,
                    "elevation_expired",
                    "The sudo mode confirmed for this job has expired",
                    "Retry the job and confirm it's you again.",
                )
                self._changed(node, f"{name}: skipped")
                return
            node.state = "running"
            node.started_at = _now()
            self._changed(node, f"{name}: started")
            run = NodeRun(
                source=source,
                options=self.plan.options,
                log=lambda line: self.reporter.log(f"[{name}] {line}"),
                step=lambda text: self._step(node, text),
                poll=self.poll,
                sleep=self.sleep,
            )
            try:
                spec.run(run)
            finally:
                node.node_jobs = list(run.node_jobs)
                node.items = [dict(item) for item in run.items]
            node.state = "succeeded"
        except NotNeeded as exc:
            _skip(node, "not_needed", str(exc), None)
        except Failure as exc:
            _failed(node, exc)
        except NoustError as exc:
            _failed(node, exc)
        finally:
            _release(name, self.job_id)
            get_aggregator().forget(name)
        node.ended_at = _now()
        level = (
            "success"
            if node.state == "succeeded"
            else ("error" if node.state in FAILED_STATES else "warning")
        )
        self.reporter.log(f"[{name}] {node.state}: {node.step or ''}".rstrip(": "), level)
        self._changed(node, f"{name}: {node.state}")

    def _step(self, node: NodeState, text: str) -> None:
        """
        Say what is happening on a server.

        Args:
            node: The server.
            text: What.
        """
        node.step = text
        self._changed(node, f"{node.node}: {text}")

    def run(self) -> dict[str, Any]:
        """
        Run every batch, stopping when the canary fails or failures pass the threshold.

        Returns:
            The final snapshot; its ``status`` is ``succeeded``, ``failed`` or
            ``aborted``.
        """
        spec = self.plan.spec
        targets = [node.node for node in self.plan.nodes]
        record_audit(
            "fleet.action",
            target=f"fleet-job:{self.job_id}",
            details={
                "action": spec.name,
                "stage": "started",
                "nodes": targets,
                "serial": self.plan.serial,
                "max_failures": self.plan.max_failures,
                "canary": self.plan.request.canary,
                "retry_of": self.plan.request.retry_of,
            },
        )
        aborted: str | None = None
        for batch in self.plan.batches():
            if aborted is not None:
                for node in batch:
                    _skip(node, "aborted", aborted, "Retry the failed servers once fixed.")
                    self._changed(node, f"{node.node}: skipped")
                continue
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                list(pool.map(self._run_node, batch))
            failures = sum(1 for node in self.plan.nodes if node.state in FAILED_STATES)
            canary = self.plan.request.canary
            if canary is not None and any(
                node.node == canary and node.state in FAILED_STATES for node in batch
            ):
                aborted = f"The canary {canary} failed; nothing else was started"
            elif self.plan.max_failures is not None and failures > self.plan.max_failures:
                aborted = (
                    f"{failures} server(s) failed, more than the {self.plan.max_failures} "
                    "this job tolerates; the rest were not started"
                )
        failed = any(node.state in FAILED_STATES for node in self.plan.nodes)
        status = "aborted" if aborted else ("failed" if failed else "succeeded")
        self.jobs.finish(self.job_id, status)
        snapshot = self.snapshot(status)
        record_audit(
            "fleet.action",
            target=f"fleet-job:{self.job_id}",
            outcome="ok" if status == "succeeded" else "failure",
            details={"action": spec.name, "stage": status, "summary": snapshot["summary"]},
        )
        self.reporter.publish(snapshot, len(self.plan.nodes), f"Fleet job {status}")
        return snapshot


# ----------------------------------------------------------- the job


class _JobReporter:
    """A fleet job's progress, told to the console's job manager."""

    def __init__(self, context: JobContext) -> None:
        self._context = context

    def log(self, message: str, level: str = "info") -> None:
        """Write a line to the job's log."""
        self._context.log(message, level)

    def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
        """Publish the job's result and progress."""
        self._context.set_result(snapshot)
        self._context.update(step, done)


def fleet_action_job(
    request: dict[str, Any],
    asker: dict[str, Any],
    elevated_until: float | None = None,
    created_by: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Run a bulk action as a console job (``JobType.FLEET``).

    The plan is made again when the job starts: the servers' state may have
    changed since the operator saw it, and each server's own preflight runs
    again before it starts anyway.

    Args:
        request: The :class:`FleetRequest`, as a dict.
        asker: The :class:`~noust.fleet.aggregate.Asker`, as a dict.
        elevated_until: Unix time after which no elevated step starts.
        created_by: Who asked, for the record.
        job_context: Injected by the job manager.

    Returns:
        The final snapshot.

    Raises:
        NoustError: Any server failed, or the job was aborted; the snapshot
            stays the job's result.
    """
    from noust.fleet.nodes import NodeManager
    from noust.web.jobs import _require_context

    context = _require_context(job_context)
    manager = NodeManager()
    who = Asker(**asker)
    fleet_request = FleetRequest.from_dict(request)
    the_plan = plan(fleet_request, manager=manager, asker=who, probe=False)
    jobs = FleetJobs()
    jobs.create(context.job_id, the_plan, created_by=created_by)
    snapshot = Runner(
        plan=the_plan,
        job_id=context.job_id,
        manager=manager,
        asker=who,
        reporter=_JobReporter(context),
        elevated_until=elevated_until,
        jobs=jobs,
    ).run()
    if snapshot["status"] != "succeeded":
        summary = snapshot["summary"]
        failed = sum(summary[state] for state in FAILED_STATES)
        raise NoustError(
            f"{the_plan.spec.title}: {failed} of {len(the_plan.nodes)} servers failed"
            + (", and the rest were not started" if snapshot["status"] == "aborted" else ""),
            details="Each server's words are in the job's result; retry the failed ones.",
        )
    return snapshot


def elevation_deadline(window_end: float | None, *, now: float | None = None) -> float:
    """
    Work out until when a job queued under sudo mode may start elevated steps.

    Args:
        window_end: Where the queuing session's own sudo window ends, as a Unix
            time; None for a credential that has no window (the master token
            as a Bearer, an API token), which leaves only the job's ceiling.
        now: The moment of queueing; the clock when omitted.

    Returns:
        The earlier of :data:`ELEVATION_WINDOW_SECONDS` from ``now`` and the end
        of the session's window: a job never outlasts the confirmation that
        started it.
    """
    moment = time.time() if now is None else now
    ceiling = moment + ELEVATION_WINDOW_SECONDS
    return ceiling if window_end is None else min(ceiling, float(window_end))


def start_job(
    the_plan: Plan,
    *,
    asker: Asker,
    actor: str | None,
    elevated: bool,
    window_end: float | None = None,
) -> tuple[Job, dict[str, Any]]:
    """
    Queue a bulk action as a console job, and record it.

    Args:
        the_plan: The plan the operator saw.
        asker: Who acts.
        actor: Who asked, as the job records it.
        elevated: Whether the operator's sudo mode was confirmed; it covers the
            job's elevated steps for :data:`ELEVATION_WINDOW_SECONDS` at most.
        window_end: Where the queuing session's sudo window ends, which the
            job's cover never goes past (:func:`elevation_deadline`); None for
            a credential with no window.

    Returns:
        The job, and the plan as it was shown.
    """
    from noust.web.jobs import JobType, get_job_manager

    targets = [node.node for node in the_plan.nodes]
    job = get_job_manager().create_job(
        job_type=JobType.FLEET,
        name=f"{the_plan.spec.title} on {len(targets)} server{'s' if len(targets) != 1 else ''}",
        description=", ".join(targets),
        func=fleet_action_job,
        kwargs={
            "request": the_plan.request.to_dict(),
            "asker": asdict(asker),
            "elevated_until": elevation_deadline(window_end) if elevated else None,
            "created_by": actor,
        },
        metadata={"action": the_plan.spec.name, "nodes": targets},
        total_steps=max(1, len(targets)),
        actor=actor,
    )
    FleetJobs().create(job.id, the_plan, created_by=actor)
    return job, the_plan.to_dict()


def new_job_id() -> str:
    """Returns: An identifier for a fleet job run outside the console (the CLI)."""
    return uuid.uuid4().hex[:8]
