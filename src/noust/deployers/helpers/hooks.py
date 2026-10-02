# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deploy hooks: what a project runs before its new version serves, and after.

Two sources declare them, and the second wins whole when it exists (they are
never merged, so what runs is always one document someone wrote):

1. ``noust.yaml`` (or ``.noust.yaml``) at the root of the repository, read by
   :mod:`noust.deployers.helpers.project_file`;
2. the operator's own, stored per application (``app_hooks``, store v13) and
   set with ``noust app hooks set`` or ``PUT /api/apps/{d}/hooks``.

:func:`resolve_hooks` is the one answer to "which hooks does this deployment
run". :func:`run_hooks` runs one phase of them in order through a
:class:`HookExecutor` - the deployer's, which decides where a hook runs: a
Compose stack in a one-off container of the new image, any other application
in the release phase of the build sandbox (its own identity and ``.env``), in
the tree being deployed. Every hook is an argv, split with :mod:`shlex` and
never handed to a shell, and goes through :class:`~noust.core.runner.CommandRunner`.

The semantics the deployers share:

- ``pre_deploy`` runs before anything serves the new version; the first hook
  that fails (non-zero, or out of time) aborts the deployment with its output
  verbatim, and what served before keeps serving.
- ``post_deploy`` runs once the new version passed its health gate; a failure
  leaves the deployment deployed with warnings, because undoing what already
  serves is worse than saying so.
- A hook marked ``migrates`` that succeeded, or Prisma's automatic migration
  that applied one, marks the deployment ``schema_changed``: going back past it
  asks the operator first (:func:`noust.deployers.lifecycle.schema_changed_between`).
"""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.fs import is_rehearsal
from noust.core.runner import CommandResult

if TYPE_CHECKING:
    from noust.core.logger import Logger
    from noust.core.notifications.context import NotificationContext
    from noust.core.notifications.model import Notification
    from noust.core.store import App, NoustStore

#: The two moments a hook runs at.
HookPhase = Literal["pre_deploy", "post_deploy"]

#: Where the hooks that apply came from.
HookSource = Literal["repo", "operator", "none"]

#: The phases, in the order a deployment reaches them.
PHASES: tuple[HookPhase, ...] = ("pre_deploy", "post_deploy")

#: The first words of the error of a deployment that changed the database's
#: schema and then failed: going back put the previous code back, never the
#: database, and that is what the operator must read first.
SCHEMA_CHANGED_PREFIX = "The database schema was changed by this attempt and was not undone."

#: The type whose hooks run in a container of the stack, where ``service``
#: means something; every other type refuses a hook that names one.
COMPOSE_TYPE = "docker-compose"

#: The PHP type: served by a pool rather than a unit, but not a static site.
_PHP_TYPE = "php-fpm"

#: How much of a hook's output the deployment history keeps per hook. The
#: whole output is in the deployment's log; this is what the console shows
#: next to the hook.
_OUTPUT_KEPT = 4000

#: Prisma's own words for a migration it applied, and for having none to
#: apply (``prisma migrate deploy``, 4.x to 6.x).
_PRISMA_APPLIED = re.compile(r"^\s*Applying migration\b", re.MULTILINE)
_PRISMA_NOTHING = re.compile(r"No pending migrations to apply", re.IGNORECASE)


@dataclass(frozen=True)
class Hook:
    """
    One command a deployment runs at one of its moments.

    Attributes:
        run: The program and its arguments; never given to a shell.
        service: For a Compose stack, the service whose new image it runs in.
        workdir: Where it runs: relative to the tree being deployed, or, in
            a Compose service, a path inside the container.
        timeout: Seconds it may take, 1 to 3600.
        migrates: It changes the database's schema, so a deployment whose
            hook succeeded is one going back past needs a decision.
    """

    run: tuple[str, ...]
    service: str | None = None
    workdir: str | None = None
    timeout: int = 600
    migrates: bool = False

    @property
    def command(self) -> str:
        """The argv as one line, quoted the way a shell would need it."""
        return shlex.join(self.run)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and ``--json``.

        Returns:
            Its fields, ``run`` as a list.
        """
        return {
            "run": list(self.run),
            "service": self.service,
            "workdir": self.workdir,
            "timeout": self.timeout,
            "migrates": self.migrates,
        }


@dataclass(frozen=True)
class HookSet:
    """
    The hooks one deployment runs, from one source.

    Attributes:
        pre_deploy: Run in order before the new version serves.
        post_deploy: Run in order once it passed its health gate.
        source: ``repo`` (``noust.yaml``), ``operator`` (the store) or
            ``none``.
    """

    pre_deploy: tuple[Hook, ...] = ()
    post_deploy: tuple[Hook, ...] = ()
    source: HookSource = "none"

    @property
    def declared(self) -> bool:
        """Whether any hook is declared; an empty document declares none."""
        return bool(self.pre_deploy or self.post_deploy)

    def phase(self, phase: HookPhase) -> tuple[Hook, ...]:
        """
        Args:
            phase: ``pre_deploy`` or ``post_deploy``.

        Returns:
            The hooks of that phase, in order.
        """
        return self.pre_deploy if phase == "pre_deploy" else self.post_deploy

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and ``--json``.

        Returns:
            The source and each phase's hooks.
        """
        return {
            "source": self.source,
            "pre_deploy": [hook.to_dict() for hook in self.pre_deploy],
            "post_deploy": [hook.to_dict() for hook in self.post_deploy],
        }


class HookExecutor(Protocol):
    """Runs one hook where the deployer decides it runs."""

    def execute(self, hook: Hook) -> CommandResult:
        """
        Run a hook to completion.

        Args:
            hook: The hook.

        Returns:
            Its outcome; a non-zero exit or a timeout is a failure the caller
            judges, not an exception.
        """
        ...


@dataclass
class HookOutcome:
    """
    What a phase of hooks did.

    Attributes:
        ran: One entry per hook that ran, in order: the phase, the command,
            the service, how it ended and the end of its output.
        schema_changed: A hook marked ``migrates`` succeeded.
    """

    ran: list[dict[str, Any]] = field(default_factory=list)
    schema_changed: bool = False


class HookFailedError(DeploymentError):
    """
    A hook failed: non-zero, out of time, or refused before it ran.

    Attributes:
        phase: The phase it belonged to.
        hook: The hook.
        outcome: What the phase had run until then, this hook included, so
            the history can say whether the schema already changed.
    """

    def __init__(
        self,
        message: str,
        *,
        details: str,
        output: str,
        phase: HookPhase,
        hook: Hook,
        outcome: HookOutcome,
    ) -> None:
        """
        Args:
            message: What failed.
            details: How to fix it.
            output: The hook's own output, verbatim.
            phase: The phase it belonged to.
            hook: The hook.
            outcome: What the phase had run until then.
        """
        super().__init__(message, details=details, output=output)
        self.phase = phase
        self.hook = hook
        self.outcome = outcome


def resolve_hooks(app: App | None, root: Path, store: NoustStore) -> HookSet:
    """
    Name the hooks a deployment of an application runs.

    The operator's, when the store holds a document for the application,
    whole; otherwise the repository's ``noust.yaml``; otherwise none.

    Args:
        app: The application's row; None for one not registered yet, which
            can only have the repository's.
        root: The tree being deployed, where ``noust.yaml`` is read.
        store: The store holding the operator's document.

    Returns:
        The hooks, with where they came from.

    Raises:
        ValidationError: The document that applies is not valid; the error
            names the field.
    """
    # Imported here: project_file builds the dataclasses this module defines.
    from noust.deployers.helpers.project_file import load_project_file, parse_hooks_document

    if app is not None and app.domain:
        document = store.get_app_hooks(app.domain)
        if document is not None:
            return parse_hooks_document(document, source="operator")
    return load_project_file(root).hooks


def hooks_unsupported_reason(app_type: str, *, serves: bool) -> str | None:
    """
    Say why an application cannot have hooks, when it cannot.

    Args:
        app_type: The application's type.
        serves: Whether it runs a process (a start command) of its own.

    Returns:
        None when it can; otherwise why not, to show the operator.
    """
    if serves or app_type in (COMPOSE_TYPE, _PHP_TYPE, "monorepo"):
        return None
    return (
        f"A {app_type} site is files the web server serves: nothing of it runs to give a "
        "hook a database or an identity to run with"
    )


def refuse_unsupported(hooks: HookSet, app_type: str, *, serves: bool) -> None:
    """
    Refuse hooks declared for an application that cannot run them.

    Args:
        hooks: What applies.
        app_type: The application's type.
        serves: Whether it runs a process of its own.

    Raises:
        ValidationError: Hooks are declared and the type has none.
    """
    if not hooks.declared:
        return
    reason = hooks_unsupported_reason(app_type, serves=serves)
    if reason is None:
        return
    where = "noust.yaml" if hooks.source == "repo" else "the operator's hooks"
    raise ValidationError(
        f"Deploy hooks are declared in {where}, and {app_type} applications have none",
        details=f"{reason}. Remove them"
        + (" from noust.yaml" if hooks.source == "repo" else " with: noust app hooks clear DOMAIN")
        + ", or deploy the project as the type that runs it.",
        field="hooks",
    )


def run_hooks(
    phase: HookPhase, hooks: HookSet, executor: HookExecutor, logger: Logger
) -> HookOutcome:
    """
    Run one phase of hooks in order, stopping at the first that fails.

    Args:
        phase: ``pre_deploy`` or ``post_deploy``.
        hooks: What applies.
        executor: Where each hook runs.
        logger: Where each hook is announced; its output goes to the log
            through the runner, as every command's does.

    Returns:
        What ran, and whether a hook marked ``migrates`` succeeded.

    Raises:
        HookFailedError: A hook exited non-zero or ran out of time, or the
            executor refused it; with its output verbatim and what the phase
            had run until then.
    """
    outcome = HookOutcome()
    selected = hooks.phase(phase)
    if selected:
        origin = "noust.yaml" if hooks.source == "repo" else "the operator's hooks"
        logger.substep(f"Running {len(selected)} {phase} hook(s) from {origin}")
    for index, hook in enumerate(selected):
        where = f" in {hook.service}" if hook.service else ""
        logger.substep(f"{phase}[{index}]{where}: {hook.command}")
        try:
            result = executor.execute(hook)
        except NoustError as exc:
            entry = _entry(phase, hook, exit_code=None, ok=False, output=str(exc))
            outcome.ran.append(entry)
            raise HookFailedError(
                f"The {phase} hook '{hook.command}' could not run: {exc.message}",
                details=_with_output(exc.details or _fix(phase), exc.output or ""),
                output=exc.output or "",
                phase=phase,
                hook=hook,
                outcome=outcome,
            ) from exc
        output = _combined(result)
        ok = result.success and not result.timed_out
        outcome.ran.append(
            _entry(
                phase,
                hook,
                exit_code=result.exit_code,
                ok=ok,
                output=output,
                duration=result.duration,
                timed_out=result.timed_out,
            )
        )
        if ok:
            if hook.migrates:
                outcome.schema_changed = True
            continue
        how = (
            f"ran out of its {hook.timeout} seconds"
            if result.timed_out
            else f"exited with {result.exit_code}"
        )
        raise HookFailedError(
            f"The {phase} hook '{hook.command}' {how}",
            details=_with_output(_fix(phase), output),
            output=output,
            phase=phase,
            hook=hook,
            outcome=outcome,
        )
    return outcome


def _fix(phase: HookPhase) -> str:
    """
    Args:
        phase: The phase that failed.

    Returns:
        What failing it means and how to go on.
    """
    if phase == "pre_deploy":
        return (
            "Nothing was switched over: what served before is still serving. Fix the hook "
            "or what it checks, and deploy again; its output is above and in the deployment's log."
        )
    return (
        "The new version is serving; only the hook failed. Run it again by hand, or fix it "
        "for the next deployment; its output is above and in the deployment's log."
    )


def _with_output(fix: str, output: str) -> str:
    """
    Args:
        fix: How to go on.
        output: The hook's own output.

    Returns:
        Both, the output verbatim after the fix: the details are what the
        deployment history keeps as the error, and what the hook said is
        the part an operator needs.
    """
    return f"{fix}\n\n{output}" if output else fix


def _combined(result: CommandResult) -> str:
    """
    Args:
        result: A hook's outcome.

    Returns:
        Its standard output and error, as it printed them, trimmed.
    """
    return "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)


def _entry(
    phase: HookPhase,
    hook: Hook,
    *,
    exit_code: int | None,
    ok: bool,
    output: str,
    duration: float | None = None,
    timed_out: bool = False,
) -> dict[str, Any]:
    """
    Describe one hook that ran, for the deployment's history.

    Args:
        phase: Its phase.
        hook: The hook.
        exit_code: How it exited; None when it never ran.
        ok: Whether it succeeded.
        output: Its output.
        duration: Seconds it took, when it ran.
        timed_out: Whether it ran out of time.

    Returns:
        The entry; the output is the end of it, the log keeps all.
    """
    return {
        "phase": phase,
        "run": hook.command,
        "service": hook.service,
        "migrates": hook.migrates,
        "exit_code": exit_code,
        "ok": ok,
        "timed_out": timed_out,
        "duration_s": round(duration, 2) if duration is not None else None,
        "output": output[-_OUTPUT_KEPT:],
    }


def prisma_applied_migrations(output: str) -> bool:
    """
    Tell from ``prisma migrate deploy``'s own output whether it applied anything.

    Args:
        output: What it printed, standard output and error.

    Returns:
        True when it applied at least one migration. "No pending migrations"
        is False; output that says neither (an older or a wrapped Prisma) is
        True, because a schema that may have changed must be treated as one
        that did.
    """
    if _PRISMA_APPLIED.search(output):
        return True
    return not _PRISMA_NOTHING.search(output)


def prisma_applied_any(output: str) -> bool:
    """
    Tell from a failed ``prisma migrate``'s output whether it applied some first.

    Args:
        output: What it printed before failing.

    Returns:
        True when it says it applied a migration before the one that failed.
    """
    return bool(_PRISMA_APPLIED.search(output))


def prisma_entry(output: str, *, applied: bool, ok: bool, command: str) -> dict[str, Any]:
    """
    Describe Prisma's automatic migration the way a hook is described.

    Args:
        output: What it printed.
        applied: Whether it applied a migration.
        ok: Whether it succeeded.
        command: What ran.

    Returns:
        A history entry, marked as automatic.
    """
    return {
        "phase": "pre_deploy",
        "run": command,
        "service": None,
        "migrates": applied,
        "exit_code": 0 if ok else 1,
        "ok": ok,
        "timed_out": False,
        "duration_s": None,
        "output": output[-_OUTPUT_KEPT:],
        "automatic": "prisma",
    }


def hooks_json(entries: list[dict[str, Any]]) -> str | None:
    """
    Args:
        entries: What ran, in order.

    Returns:
        The JSON the deployment history keeps, or None when nothing ran.
    """
    return json.dumps(entries) if entries else None


@dataclass(frozen=True)
class CommandHookExecutor:
    """
    Run hooks as commands in the tree being deployed.

    What every type but a Compose stack uses: the deployer hands its own
    chokepoint (``_run`` in the release phase, as the application with its
    ``.env``), and this resolves each hook's working directory inside the
    tree, refusing one that leaves it, and a hook that names a service.

    Attributes:
        run: Runs an argv: ``run(argv, cwd=..., timeout=...)``.
        root: The tree being deployed.
        app_type: The application's type, for the refusal of ``service``.
    """

    run: Callable[..., CommandResult]
    root: Path
    app_type: str

    def execute(self, hook: Hook) -> CommandResult:
        """
        Run a hook in the tree.

        Args:
            hook: The hook.

        Returns:
            Its outcome.

        Raises:
            ValidationError: It names a service, or its working directory is
                not a directory inside the tree.
        """
        if hook.service is not None:
            raise ValidationError(
                f"The hook names the service {hook.service!r}, and a {self.app_type} "
                "application has no services",
                details="'service' is for Docker Compose stacks. Remove it to run the hook "
                "in the application's own tree.",
                field="service",
            )
        cwd = self.root
        if hook.workdir:
            cwd = (self.root / hook.workdir).resolve()
            root = self.root.resolve()
            # A link the repository committed must not take a hook, which
            # runs with the application's secrets, outside its tree.
            if not cwd.is_relative_to(root) or not cwd.is_dir():
                raise ValidationError(
                    f"The hook's workdir {hook.workdir!r} is not a directory inside the "
                    "application",
                    details="Name a directory of the repository, relative to its root.",
                    field="workdir",
                )
        return self.run(list(hook.run), cwd=cwd, timeout=hook.timeout)


@dataclass
class DeploymentHooks:
    """
    What one deployment's hooks are, and what they did: the state every deployer shares.

    A deployer keeps one per deployment and asks it at its own moments
    (:meth:`run_pre` before anything serves, :meth:`run_post` once the gate
    passed); this is where the rules live, so a type cannot apply them
    differently (rule 3).

    Attributes:
        hooks: What applies, once read.
        runs: What ran, hooks and Prisma's automatic migration, in order.
        schema_changed: A hook marked ``migrates`` or Prisma changed the schema.
        warnings: Why the deployment carries warnings (failed ``post_deploy``).
        failure: The ``post_deploy`` hook that failed, for its notification.
    """

    hooks: HookSet | None = None
    runs: list[dict[str, Any]] = field(default_factory=list)
    schema_changed: bool = False
    warnings: list[str] = field(default_factory=list)
    failure: HookFailedError | None = None

    def resolve(
        self, app: App | None, root: Path, store: NoustStore, *, app_type: str, serves: bool
    ) -> HookSet:
        """
        Read the hooks that apply, once per deployment.

        Args:
            app: The application's row, None for one not registered yet.
            root: The tree being deployed.
            store: The store holding the operator's hooks.
            app_type: The application's type.
            serves: Whether it runs a process of its own.

        Returns:
            The hooks.

        Raises:
            ValidationError: Not valid, or declared for a type that has none.
        """
        if self.hooks is None:
            hooks = resolve_hooks(app, root, store)
            refuse_unsupported(hooks, app_type, serves=serves)
            self.hooks = hooks
        return self.hooks

    def note(self, outcome: HookOutcome) -> None:
        """
        Keep what a phase ran.

        Args:
            outcome: What it ran.
        """
        self.runs.extend(outcome.ran)
        self.schema_changed = self.schema_changed or outcome.schema_changed

    def note_prisma(self, output: str, *, applied: bool, ok: bool, command: str) -> None:
        """
        Keep what Prisma's automatic migration did.

        Args:
            output: What it printed.
            applied: Whether it applied a migration.
            ok: Whether it succeeded.
            command: What ran.
        """
        self.runs.append(prisma_entry(output, applied=applied, ok=ok, command=command))
        self.schema_changed = self.schema_changed or applied

    def run_pre(self, executor: HookExecutor, logger: Logger) -> None:
        """
        Run the ``pre_deploy`` hooks.

        Args:
            executor: Where they run.
            logger: Where they are announced.

        Raises:
            HookFailedError: One failed; nothing may be switched over.
        """
        hooks = self.hooks or HookSet()
        try:
            self.note(run_hooks("pre_deploy", hooks, executor, logger))
        except HookFailedError as exc:
            self.note(exc.outcome)
            raise

    def run_post(self, executor: HookExecutor, logger: Logger) -> None:
        """
        Run the ``post_deploy`` hooks; a failure is a warning, never an error.

        Args:
            executor: Where they run.
            logger: Where they and a failure are reported, the output verbatim.
        """
        hooks = self.hooks or HookSet()
        try:
            self.note(run_hooks("post_deploy", hooks, executor, logger))
        except HookFailedError as exc:
            self.note(exc.outcome)
            self.warnings.append(exc.message)
            self.failure = exc
            logger.warning(f"Deployed with warnings: {exc.message}")
            if exc.output:
                logger.info(exc.output)

    @property
    def warnings_text(self) -> str | None:
        """The warnings as the history keeps them, or None without any."""
        return "\n".join(self.warnings) or None

    def say_schema_changed(self, error: NoustError) -> None:
        """
        Put "the schema changed" first in the error of a deployment that changed it.

        Going back after a failed gate puts the previous code back, never the
        database: the operator has to read that first.

        Args:
            error: The deployment's failure, changed in place so its class
                (a rollback or a plain failure) is kept.
        """
        if not self.schema_changed or error.message.startswith(SCHEMA_CHANGED_PREFIX):
            return
        error.message = f"{SCHEMA_CHANGED_PREFIX} {error.message}"
        error.args = (error.message,)

    def record(
        self, store: NoustStore, deployment_id: int | None, logger: Logger, *, domain: str = ""
    ) -> None:
        """
        Write what the hooks did into the deployment's history row, and announce a warning.

        A ``post_deploy`` hook that failed is announced as ``deploy_hook_failed``
        once the row says so, so the notification links to it.

        Args:
            store: The store.
            deployment_id: The row; nothing is written without one (a
                rehearsal, or recording failed).
            logger: Where a failure to write is reported; it never fails the
                deployment it describes.
            domain: The application, for the notification.
        """
        if self.failure is not None and domain and not is_rehearsal():
            announce_hook_failure(domain, self.failure, deployment_id=deployment_id)
        if deployment_id is None or not (self.runs or self.schema_changed or self.warnings):
            return
        try:
            store.record_deployment_hooks(
                deployment_id,
                hooks=hooks_json(self.runs),
                schema_changed=self.schema_changed,
                warnings=self.warnings_text,
            )
        except (NoustError, sqlite3.Error) as exc:
            logger.warning(f"Could not record what the deploy hooks did: {exc}")


def announce_hook_failure(
    domain: str, failure: HookFailedError, *, deployment_id: int | None
) -> None:
    """
    Tell the operator a deployment is serving with a failed ``post_deploy`` hook.

    Composed and delivered on the notification worker, never on the
    deploying thread.

    Args:
        domain: The application.
        failure: The hook that failed.
        deployment_id: The deployment, for the link.
    """
    from noust.core.notifier import notify_composed

    notify_composed(
        lambda ctx: compose_deploy_hook_failed(
            domain,
            ctx,
            command=failure.hook.command,
            output=failure.output or "",
            deployment_id=deployment_id,
        )
    )


def compose_deploy_hook_failed(
    domain: str,
    ctx: NotificationContext,
    *,
    command: str,
    output: str,
    deployment_id: int | None = None,
) -> Notification:
    """
    Compose the notification for a deployment serving with a failed ``post_deploy`` hook.

    Under its own switch, ``deploy_hook_failed``, as a warning: the new
    version serves, only the hook failed.

    Args:
        domain: The application.
        ctx: The context.
        command: The hook that failed, as one line.
        output: Its own output, verbatim.
        deployment_id: The deployment, for the link.

    Returns:
        The notification.
    """
    from noust.core.messages import message
    from noust.core.notifications.composers import build
    from noust.core.notifications.excerpt import make_excerpt
    from noust.core.notifications.model import Fact, State

    code = "deploy.hook_failed"
    return build(
        ctx,
        kind="deploy_hook_failed",
        code=code,
        state=State.WARNING,
        subject=domain,
        summary=message(f"summary.{code}", ctx.locale),
        facts=[Fact("command", message("fact.command", ctx.locale), command, True)],
        command=Fact(
            "inspect", message("fact.inspect", ctx.locale), f"noust app hooks show {domain}", True
        ),
        excerpt=make_excerpt(output, label=message("excerpt.output", ctx.locale), pin_error=True)
        if output
        else None,
        path=f"/apps/{domain}/deployments/{deployment_id}"
        if deployment_id is not None
        else f"/apps/{domain}",
        domain=domain,
    )


@dataclass(frozen=True)
class AppHooks:
    """
    The hooks an application's next deployment would run, and where they come from.

    Attributes:
        domain: The application.
        hooks: What applies: the operator's whole, else the running code's
            ``noust.yaml``.
        document: The operator's document as written, when there is one.
        repository_error: Why the running code's ``noust.yaml`` cannot be
            used, when it cannot (the next deployment would fail with this).
    """

    domain: str
    hooks: HookSet
    document: str | None = None
    repository_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and ``--json``.

        Returns:
            The hooks, the source and the operator's document.
        """
        return {
            "domain": self.domain,
            **self.hooks.to_dict(),
            "document": self.document,
            "repository_error": self.repository_error,
        }


def describe_hooks(domain: str, *, store: NoustStore | None = None) -> AppHooks:
    """
    Say which hooks an application's next deployment runs: the one read CLI and API share.

    The repository's are read from the code that runs now; the next deploy
    reads them from what it fetches, which may differ.

    Args:
        domain: A validated domain.
        store: The store; the process-wide one when None.

    Returns:
        What applies and where it comes from.

    Raises:
        NoustError: The application is unknown.
    """
    from noust.core.store import get_store
    from noust.deployers.helpers.layout import code_path_for
    from noust.deployers.helpers.project_file import load_project_file, parse_hooks_document

    target = store or get_store()
    app = target.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    document = target.get_app_hooks(domain)
    if document is not None:
        return AppHooks(
            domain=domain,
            hooks=parse_hooks_document(document, source="operator"),
            document=document,
        )
    try:
        hooks = load_project_file(code_path_for(app)).hooks
    except ValidationError as exc:
        return AppHooks(domain=domain, hooks=HookSet(), repository_error=str(exc))
    return AppHooks(domain=domain, hooks=hooks)


def operator_hooks(domain: str, *, store: NoustStore | None = None) -> str | None:
    """
    Read the hooks document the operator stored for an application.

    Args:
        domain: A validated domain.
        store: The store; the process-wide one when None.

    Returns:
        The document as written, or None.
    """
    from noust.core.store import get_store

    return (store or get_store()).get_app_hooks(domain)


def set_operator_hooks(
    domain: str, document: str | None, *, actor: str | None, store: NoustStore | None = None
) -> HookSet:
    """
    Store, or clear, an application's hooks: the one write CLI and API share.

    A hook of the operator's is code that runs with the application's
    identity and secrets; the change is recorded in the audit trail here, so
    neither caller can forget (rule 4).

    Args:
        domain: A validated domain.
        document: The YAML document (``hooks:`` with its phases), or None to
            clear it and let the repository's apply again.
        actor: Who changed it, for the store's record.
        store: The store; the process-wide one when None.

    Returns:
        The hooks the operator's document declares; an empty set with
        source ``none`` once cleared.

    Raises:
        NoustError: The application is unknown.
        ValidationError: The document is not valid (naming the field), or
            the application's type has no hooks.
    """
    from noust.core import audit
    from noust.core.store import get_store
    from noust.deployers.helpers.project_file import parse_hooks_document

    target = store or get_store()
    app = target.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    hooks = HookSet() if document is None else parse_hooks_document(document, source="operator")
    if document is not None:
        refuse_unsupported(hooks, app.app_type, serves=not app.is_static)
    target.set_app_hooks(domain, document, updated_by=actor)
    audit.record(
        "apps.hooks",
        target=f"app:{domain}",
        details={
            "action": "clear" if document is None else "set",
            "pre_deploy": [hook.command for hook in hooks.pre_deploy],
            "post_deploy": [hook.command for hook in hooks.post_deploy],
        },
    )
    return hooks


__all__ = [
    "COMPOSE_TYPE",
    "PHASES",
    "SCHEMA_CHANGED_PREFIX",
    "AppHooks",
    "CommandHookExecutor",
    "DeploymentHooks",
    "Hook",
    "HookExecutor",
    "HookFailedError",
    "HookOutcome",
    "HookPhase",
    "HookSet",
    "HookSource",
    "announce_hook_failure",
    "compose_deploy_hook_failed",
    "describe_hooks",
    "hooks_json",
    "hooks_unsupported_reason",
    "operator_hooks",
    "prisma_applied_any",
    "prisma_applied_migrations",
    "prisma_entry",
    "refuse_unsupported",
    "resolve_hooks",
    "run_hooks",
    "set_operator_hooks",
]
