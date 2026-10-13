# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Making a server match its manifest (J-15).

Applying a plan is nothing but the API calls a person would make in the console,
in the order that makes them valid, as the token that applies: a project before
the application in it, an application before its domains. Every one is audited
by the server as that token. A call that fails stops the run - what comes after
it may depend on it - and the run says what was done, what failed and what was
left, so running it again, after fixing the cause, continues from where it
stopped: a plan taken again shows only what remains.

Nothing here is destructive. It creates, sets and moves; it never deletes an
application, a domain, a database, a variable or a schedule.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from noust.client.errors import RemoteError
from noust.client.jobs import DEFAULT_WAIT, follow
from noust.manifest.plan import Action, Plan, Transport
from noust.manifest.schema import AppSpec


@dataclass
class Outcome:
    """
    How one action ended.

    Attributes:
        action: What was asked.
        status: ``applied``, ``unchanged`` (it was already so), ``failed`` or
            ``skipped`` (an earlier action failed).
        message: One sentence on how it went, with the server's words on a failure.
        restart_required: A variable changed, so the application needs a restart to use it.
    """

    action: Action
    status: str
    message: str = ""
    restart_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The outcome as ``--json`` shows it.
        """
        return {
            **self.action.to_dict(),
            "status": self.status,
            "message": self.message,
            "restart_required": self.restart_required,
        }


@dataclass
class ApplyResult:
    """
    What applying a plan did.

    Attributes:
        outcomes: One per action, in order.
    """

    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether nothing failed."""
        return not any(o.status == "failed" for o in self.outcomes)

    @property
    def restart(self) -> list[str]:
        """The applications whose new variables wait for a restart."""
        return sorted({o.action.target for o in self.outcomes if o.restart_required})

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result as ``--json`` shows it.
        """
        return {
            "ok": self.ok,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "restart": self.restart,
        }


def _create_body(spec: AppSpec, env: dict[str, str], database: Any) -> dict[str, Any]:
    """
    Args:
        spec: The application.
        env: Its variables, every value in hand.
        database: The first database it should have, made with it, or None.

    Returns:
        The request ``POST /api/apps`` takes.
    """
    body: dict[str, Any] = {"domain": spec.domain, "app_type": spec.app_type, "ssl": spec.ssl}
    if spec.recipe:
        body["recipe"] = spec.recipe
    if spec.source:
        body["source"] = spec.source
    for key in ("branch", "port", "team", "project", "environment"):
        if getattr(spec, key) is not None:
            body[key] = getattr(spec, key)
    if env:
        body["env_vars"] = env
    if spec.limits:
        body.update({k: v for k, v in spec.limits.items() if v is not None})
    if spec.health:
        body.update({f"health_{k}": v for k, v in spec.health.items() if v is not None})
    if database is not None:
        body["database"] = {
            key: value
            for key, value in {
                "engine": database.engine,
                "name": database.name,
                "env_var": database.env_var,
            }.items()
            if value is not None
        }
    return body


class Applier:
    """
    Carry out a plan against a server.

    Args:
        transport: The server's routes.
        wait: Wait for the jobs an action queues (a deployment, a database).
        timeout: Seconds to wait for each.
        on_line: Called with each line of a waited job's log.
        sleep: Replaces waiting, for tests.
        clock: Replaces the monotonic clock, for tests.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        wait: bool = True,
        timeout: float = DEFAULT_WAIT,
        on_line: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.transport = transport
        self.wait = wait
        self.timeout = timeout
        self.on_line = on_line
        self.sleep = sleep
        self.clock = clock

    def apply(self, plan: Plan, *, on_start: Callable[[Action], None] | None = None) -> ApplyResult:
        """
        Run a plan.

        Args:
            plan: What to do.
            on_start: Called with each action just before it runs.

        Returns:
            What happened. The first failure ends the run; the actions after
            it are ``skipped``.
        """
        result = ApplyResult()
        failed = False
        for action in plan.actions:
            if failed:
                result.outcomes.append(Outcome(action, "skipped", "an earlier step failed"))
                continue
            if on_start is not None:
                on_start(action)
            try:
                outcome = self._run(action)
            except RemoteError as exc:
                text = exc.message + (f" ({exc.details})" if exc.details else "")
                outcome = Outcome(action, "failed", text)
            result.outcomes.append(outcome)
            failed = outcome.status == "failed"
        return result

    # ------------------------------------------------------------------- steps

    def _job(self, answer: dict[str, Any], what: str, action: Action) -> Outcome:
        """
        Wait for the job an action queued, if asked to.

        Args:
            answer: What the route answered, carrying the job.
            what: What the job does, for the message.
            action: The action.

        Returns:
            How the job ended.
        """
        job = answer.get("job") or {}
        job_id = answer.get("job_id") or job.get("id")
        if not self.wait or not job_id:
            return Outcome(action, "applied", f"{what} queued as job {job_id}")
        final = follow(
            _Getter(self.transport),
            str(job_id),
            on_line=self.on_line,
            timeout=self.timeout,
            sleep=self.sleep,
            clock=self.clock,
        )
        state = final.get("state")
        if state == "succeeded":
            return Outcome(action, "applied", f"{what} finished (job {job_id})")
        if state in ("failed", "cancelled"):
            return Outcome(
                action,
                "failed",
                f"{what} {state}: {final.get('error') or 'see job ' + str(job_id)}",
            )
        return Outcome(
            action,
            "failed",
            f"{what} is still running (job {job_id}); wait for it with 'noust jobs wait {job_id}', then apply again",
        )

    def _run(self, action: Action) -> Outcome:
        """
        Args:
            action: One action of the plan.

        Returns:
            How it ended.

        Raises:
            RemoteError: When the server refuses the call.
        """
        data = action.data
        kind = action.kind
        t = self.transport
        if kind == "project.create":
            t.post(
                f"/teams/{data['team']}/projects", {"name": data["name"], "slug": data["project"]}
            )
            return Outcome(action, "applied", "created")
        if kind == "environment.create":
            t.post(
                f"/teams/{data['team']}/projects/{data['project']}/environments",
                {"name": data["environment"].title(), "slug": data["environment"]},
            )
            return Outcome(action, "applied", "created")
        if kind == "app.create":
            spec: AppSpec = data["spec"]
            if data.get("env") is None:
                return Outcome(
                    action,
                    "failed",
                    "its variables are read where the manifest is applied from, and could not be",
                )
            first = spec.databases[0] if spec.databases else None
            answer = t.post("/apps", _create_body(spec, data["env"], first))
            return self._job(answer, "the deployment", action)
        if kind == "app.move":
            t.post(
                f"/apps/{data['domain']}/move",
                {k: data[k] for k in ("team", "project", "environment") if data.get(k)},
            )
            return Outcome(action, "applied", "moved")
        if kind == "app.branch":
            t.patch(f"/apps/{data['domain']}/branch", {"branch": data["branch"]})
            return Outcome(action, "applied", "pinned")
        if kind == "app.limits":
            limits = data["limits"]
            t.patch(f"/apps/{data['domain']}/limits", {**limits, "restart": False})
            return Outcome(action, "applied", "set; they apply at the next restart")
        if kind == "app.health":
            t.patch(f"/apps/{data['domain']}/health", data["health"])
            return Outcome(action, "applied", "set")
        if kind == "app.domain":
            t.post(f"/apps/{data['domain']}/domains", {"domain": data["alias"], "kind": "alias"})
            return Outcome(action, "applied", "added")
        if kind == "app.database":
            database = data["database"]
            body = {
                k: v
                for k, v in {
                    "engine": database.engine,
                    "name": database.name,
                    "env_var": database.env_var,
                }.items()
                if v is not None
            }
            answer = t.post(f"/apps/{data['domain']}/databases", body)
            return self._job(answer, "the database", action)
        if kind == "app.env":
            if data.get("env") is None:
                return Outcome(
                    action,
                    "failed",
                    "its variables are read where the manifest is applied from, and could not be",
                )
            merged = t.patch(f"/apps/{data['domain']}/env", {"set": data["env"], "unset": []})
            if not merged.get("changed"):
                return Outcome(action, "unchanged", "already so")
            return Outcome(
                action,
                "applied",
                "set " + ", ".join(merged["changed"]) + "; restart it to use them",
                restart_required=True,
            )
        if kind == "app.backups":
            backups = data["backups"]
            body = {
                "domain": data["domain"],
                "schedule": backups.schedule,
                "retention_count": backups.retention_count,
                "retention_days": backups.retention_days,
                "include_databases": backups.include_databases,
            }
            if data.get("exists"):
                t.put(f"/backup-schedules/{data['domain']}", body)
            else:
                t.post("/backup-schedules", body)
            return Outcome(action, "applied", "scheduled")
        return Outcome(
            action, "failed", f"unknown action {kind}"
        )  # a defect: the plan names only those above


class _Getter:
    """Gives :func:`~noust.client.jobs.follow` the ``get`` it needs from any transport."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def get(self, path: str, **query: Any) -> Any:
        """
        Args:
            path: A route.
            **query: Query parameters.

        Returns:
            Its decoded body.
        """
        return self._transport.get(path, **query)


__all__ = ["Applier", "ApplyResult", "Outcome"]
