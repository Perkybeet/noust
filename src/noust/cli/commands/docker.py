# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust docker``: what Docker has on this server, and changing it with a net.

``images`` says which images in use have a new version (asked of the registries only when you say so:
``check``) and what updating each does, and updates through the application's own update, behind its health
gate with the way back kept. ``stack`` starts, stops and restarts a stack as a whole, brings one that already
runs to Noust without touching it, and edits its Compose: judged by the model Compose resolves it to, with
what it would change said first, every saved text a version, and the document before put back when the stack
does not come up.

Every command is a client of the one implementation the console uses (rule 3): the gate
(:class:`~noust.core.docker.gate.DockerGate`), which resolves what Docker has to its owner and judges what is
done to it, :mod:`noust.managers.docker_updates`, :mod:`noust.managers.docker_bring` and
:mod:`noust.deployers.compose_stack_edit`. This module only parses and presents.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.docker.gate import DockerGate
from noust.core.docker.models import OwnerKind
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.core.store import DeploymentTrigger
from noust.deployers.compose_adopt import AdoptionRefusedError
from noust.deployers.compose_paste import MAX_DOCUMENT_BYTES
from noust.deployers.compose_stack_edit import EditCheck, StackEditor
from noust.managers.docker_bring import Bringing, StackBringer
from noust.managers.docker_image_versions import ImageVersions
from noust.managers.docker_updates import ImageUpdates, UpdatePlan


def _gate() -> DockerGate:
    """
    Returns:
        The gate, with Docker made sure of: a machine without it says what is missing.

    Raises:
        DockerUnavailableError: Docker is not there, or does not answer, or is below what Noust speaks.
    """
    gate = DockerGate()
    gate.require_usable()
    return gate


def _read_document(file: Path) -> str:
    """
    Args:
        file: A path, or ``-`` for standard input.

    Returns:
        The document.

    Raises:
        ValidationError: It is larger than a stack is.
    """
    text = (
        sys.stdin.read(MAX_DOCUMENT_BYTES + 1)
        if str(file) == "-"
        else file.read_text(encoding="utf-8")
    )
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValidationError(
            "The Compose document is larger than 256 KiB",
            details="A stack is a few KiB. Deploy a repository for anything bigger.",
            field="file",
        )
    return text


@click.group("docker", cls=NoustGroup)
def cli() -> None:
    """What Docker has on this server, and updating and editing it with a net."""


# -- images ---------------------------------------------------------------------------------------


@cli.group("images", cls=NoustGroup)
def images() -> None:
    """The images in use: which have a new version, and updating them with a net."""


def _show_plan(logger: Logger, plan: UpdatePlan) -> None:
    """
    Print what updating an image does.

    Args:
        logger: Where it is printed.
        plan: The plan.
    """
    logger.key_value("Image", plan.image)
    state = {
        "not_looked": "nobody looked: noust docker images check",
        "unknown": f"could not be told: {plan.detail}",
        "current": "current",
        "newer": "a new version" + (f" ({plan.change})" if plan.change else ""),
    }.get(plan.state, plan.state)
    logger.key_value("Registry", state)
    if plan.current_version or plan.latest_version:
        logger.key_value(
            "Version", f"{plan.current_version or '?'} -> {plan.latest_version or '?'}"
        )
    logger.key_value("Owner", f"{plan.owner.kind.value} {plan.owner.subject}".strip())
    if plan.mode == "with_net":
        logger.key_value("Updates", f"{plan.app} (through its update, behind its health gate)")
        logger.key_value("Moves", ", ".join(plan.images))
        logger.key_value("Steps", " -> ".join(plan.steps))
        if plan.backup is not None:
            logger.key_value(
                "Copy", f"{plan.backup.state} (last proven {plan.backup.last_ok_at or 'never'})"
            )
        if plan.needs == "verified_copy":
            logger.warning(
                "A database's major version waits for a copy proven to restore: "
                f"{plan.backup.via if plan.backup else ''}"
            )
        elif plan.needs == "accept_major":
            logger.warning("A major version is a decision: update with --accept-major.")
    elif plan.mode == "bring_first":
        logger.warning(f"Bring its stack to Noust first: {plan.via}")
    elif plan.mode == "loose":
        logger.warning(
            "A container Noust did not create: Noust does not know how it was created, so it is "
            "not updated blind."
        )
    elif plan.mode == "via_owner":
        logger.warning(f"It is updated through its owner: {plan.via}")
    elif plan.mode == "rollback":
        logger.info("A version an application keeps to go back to.")


@images.command("list", read_only=True)
@click.option("--owner", type=click.Choice([kind.value for kind in OwnerKind]), default=None)
@json_option("Print the images and what updating each does as JSON.")
@pass_context
def images_list(ctx: Context, owner: str | None) -> None:
    """List the images, with what the last look at the registries found and what updating each does."""
    gate = _gate()
    page = gate.images(owner=OwnerKind(owner) if owner else None)
    plans = ImageUpdates(gate, versions=ImageVersions(gate)).plans(page.items)
    if ctx.json_output:
        click.echo(
            json.dumps(
                [{**image.to_dict(), "update": plans[image.id].to_dict()} for image in page.items]
            )
        )
        return
    for image in page.items:
        plan = plans[image.id]
        label = image.tags[0] if image.tags else image.id
        news = (
            f"new version ({plan.change or 'unknown change'})"
            if plan.state == "newer"
            else plan.state.replace("_", " ")
        )
        ctx.logger.key_value(label, f"{image.owner.kind.value:8} {news}  [{plan.mode}]")


@images.command("check")
@click.option(
    "--pull",
    is_flag=True,
    help="Download what moved to compare: what a Docker without buildx needs. Without it nothing "
    "is downloaded.",
)
@json_option("Print what was found as JSON.")
@pass_context
def images_check(ctx: Context, pull: bool) -> None:
    """
    Ask the registries whether the images in use have a new version.

    The registry of each image is asked what the tag points at now (the question 'docker pull' asks, put
    without taking the layers), with the account Noust keeps for that registry. Nothing is asked unless
    you run this: the pages of the console say what was found.
    """
    gate = _gate()
    found = ImageVersions(gate).check(
        pull=pull,
        on_progress=None
        if ctx.json_output
        else lambda done, total, ref: ctx.logger.substep(f"{done}/{total} {ref}"),
    )
    if ctx.json_output:
        click.echo(json.dumps(found.to_dict()))
        return
    for record in found.records:
        said = {
            "newer": f"new version ({record.change or 'unknown change'})",
            "current": "current",
            "unknown": f"could not be told: {record.detail}",
        }[record.state]
        ctx.logger.key_value(record.reference, said)
    for skipped in found.skipped:
        why = (
            "built here, no registry to ask" if skipped.why == "built_here" else "pinned by digest"
        )
        ctx.logger.key_value(skipped.reference, why)
    newer = sum(1 for record in found.records if record.state == "newer")
    ctx.logger.success(f"{newer} of {len(found.records)} images have a new version.")


@images.command("plan", read_only=True)
@click.argument("image")
@json_option("Print the plan as JSON.")
@pass_context
def images_plan(ctx: Context, image: str) -> None:
    """Say what updating an image would do, without doing it. IMAGE is a name or an id."""
    gate = _gate()
    plan = ImageUpdates(gate, versions=ImageVersions(gate)).plan_for(image)
    if ctx.json_output:
        click.echo(json.dumps(plan.to_dict()))
        return
    _show_plan(ctx.logger, plan)


@images.command("update")
@click.argument("image")
@click.option("--accept-major", is_flag=True, help="Take a new major version.")
@click.option("--yes", "-y", is_flag=True, help="Update without asking.")
@json_option("Print what was done as JSON.")
@pass_context
def images_update(ctx: Context, image: str, accept_major: bool, yes: bool) -> None:
    """
    Update an image through its application, behind its health gate, with the way back kept.

    What an application owns is updated by its update: every image of it that has a new version moves with
    it, and this says which before it starts. A major version needs --accept-major, and a database's also a
    copy of the application's databases that was proven to restore within the last day. What nobody of Noust
    made is not updated here, and the refusal says why and what to do.
    """
    from noust.deployers.lifecycle import update_app

    gate = _gate()
    prepared = ImageUpdates(gate, versions=ImageVersions(gate)).prepare(
        image, accept_major=accept_major
    )
    if not ctx.json_output:
        _show_plan(ctx.logger, prepared.plan)
    if not (yes or ctx.json_output or ctx.dry_run):
        click.confirm(f"Update {prepared.domain} now?", default=False, abort=True)
    outcome = update_app(
        prepared.domain,
        trigger=DeploymentTrigger.CLI.value,
        on_phase=ctx.logger.step,
        on_step=ctx.logger.substep,
        logger=ctx.logger,
        verbose=ctx.verbose,
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": prepared.domain,
                    "images": prepared.plan.images,
                    "deployment_id": outcome.deployment_id,
                    "warnings": outcome.warnings,
                }
            )
        )
        return
    ctx.logger.success(f"{prepared.domain} runs the new version.")


# -- stacks ---------------------------------------------------------------------------------------


@cli.group("stack", cls=NoustGroup)
def stack() -> None:
    """The Compose stacks on this server: start, stop, restart, bring to Noust and edit."""


@stack.command("list", read_only=True)
@json_option("Print the stacks as JSON.")
@pass_context
def stack_list(ctx: Context) -> None:
    """List the Compose stacks, whose each is and what may be done to it."""
    gate = _gate()
    page = gate.projects()
    if ctx.json_output:
        click.echo(
            json.dumps([{**item.to_dict(), "can": gate.can(item).to_dict()} for item in page.items])
        )
        return
    for item in page.items:
        owner = f"{item.owner.kind.value} {item.owner.subject}".strip()
        ctx.logger.key_value(item.name, f"{item.state:8} {item.running}/{item.workers}  {owner}")


def _act(ctx: Context, name: str, verb: str) -> None:
    """
    Start, stop or restart a stack.

    Args:
        ctx: The CLI context.
        name: The Compose project.
        verb: ``start``, ``stop`` or ``restart``.
    """
    done = getattr(_gate(), f"{verb}_stack")(name)
    if ctx.json_output:
        click.echo(json.dumps({"kind": "stack", "name": done.name, "action": verb}))
        return
    ctx.logger.success(f"{done.name}: {verb} done.")


@stack.command("start")
@click.argument("name")
@json_option("Print what was done as JSON.")
@pass_context
def stack_start(ctx: Context, name: str) -> None:
    """Start every container of a stack nobody of Noust owns, in the order Compose gives."""
    _act(ctx, name, "start")


@stack.command("stop")
@click.argument("name")
@json_option("Print what was done as JSON.")
@pass_context
def stack_stop(ctx: Context, name: str) -> None:
    """Stop every container of a stack nobody of Noust owns, giving each 30 seconds."""
    _act(ctx, name, "stop")


@stack.command("restart")
@click.argument("name")
@json_option("Print what was done as JSON.")
@pass_context
def stack_restart(ctx: Context, name: str) -> None:
    """Restart every container of a stack nobody of Noust owns."""
    _act(ctx, name, "restart")


def _show_bringing(logger: Logger, found: Bringing) -> None:
    """
    Print what bringing a stack to Noust registers.

    Args:
        logger: Where it is printed.
        found: What it registers.
    """
    logger.key_value("Stack", found.stack)
    logger.key_value("Application", f"{found.name} (unit {found.plan.app_name})")
    logger.key_value("Folder", f"{found.folder} (stays where it is)")
    logger.key_value("Compose file", found.compose_file)
    counts = found.counts
    logger.key_value(
        "Has",
        f"{counts['containers']} containers ({counts['jobs']} jobs that ended), "
        f"{counts['volumes']} volumes, {counts['networks']} networks",
    )
    logger.key_value("Rehearsed up", "changes nothing" if not found.recreates else "would change:")
    for line in found.recreates:
        logger.substep(line)
    for item in found.restart_loss:
        logger.warning(
            f"{item.container} has the policy '{item.policy}': it would not come back after a restart"
        )
    for warning in found.warnings:
        logger.warning(warning)
    logger.info(
        "Nothing is recreated, restarted or changed, and its compose file is not rewritten."
    )


@stack.command("bring")
@click.argument("name")
@click.option(
    "--name", "app_name", default=None, help="What it is called in the list of applications."
)
@click.option(
    "--accept-recreate",
    is_flag=True,
    help="Bring it even if starting the stack the way Noust would recreates something.",
)
@click.option("--preview", is_flag=True, help="Say what would be registered and register nothing.")
@click.option("--yes", "-y", is_flag=True, help="Bring it without asking.")
@json_option("Print what was found, or done, as JSON.")
@pass_context
def stack_bring(
    ctx: Context,
    name: str,
    app_name: str | None,
    accept_recreate: bool,
    preview: bool,
    yes: bool,
) -> None:
    """
    Bring a stack that already runs to Noust, without touching it.

    The folder and the compose file are what the stack's containers say; only the name it will have in the
    list of applications is yours. What is registered is said first (the rehearsed 'docker compose up
    --dry-run' must show nothing would change), and a stack that is not on this server's disk, or was started
    from several files, is said not to be brought, with why.
    """
    bringer = StackBringer(_gate())
    try:
        shown = bringer.preview(name, name=app_name, accept_recreate=accept_recreate)
    except AdoptionRefusedError as refused:
        ctx.logger.error(f"{refused.message}: {refused.details}")
        ctx.logger.info(refused.output or "")
        raise SystemExit(1) from refused
    if preview:
        click.echo(json.dumps(shown.to_dict())) if ctx.json_output else _show_bringing(
            ctx.logger, shown
        )
        return
    if not ctx.json_output:
        _show_bringing(ctx.logger, shown)
    if not (yes or ctx.json_output or ctx.dry_run):
        click.confirm(f"Bring {name} to Noust as shown?", default=False, abort=True)
    done = bringer.bring(
        name,
        name=app_name,
        accept_recreate=accept_recreate,
        trigger=DeploymentTrigger.CLI.value,
    )
    if ctx.json_output:
        click.echo(json.dumps(done.to_dict()))
        return
    ctx.logger.success(f"{done.stack} is the application {done.name}.")


# -- the Compose of a stack ------------------------------------------------------------------------


@stack.group("compose", cls=NoustGroup)
def compose() -> None:
    """Edit the Compose of a stack: judge, see what changes, apply behind the health gate, go back."""


def _show_check(logger: Logger, found: EditCheck) -> None:
    """
    Print what a document was judged to be.

    Args:
        logger: Where it is printed.
        found: The judgement.
    """
    for line in found.checks:
        (logger.success if line.ok else logger.error)(
            f"{line.name}: {line.detail or ('ok' if line.ok else 'failed')}"
        )
    for problem in found.problems:
        where = f" (line {problem.line})" if problem.line else ""
        logger.error(f"{problem.message}{where}")
        if problem.output:
            logger.info(problem.output)
    for warning in found.warnings:
        logger.warning(warning)
    if found.diff:
        click.echo(found.diff, nl=False)
    if found.effects is not None:
        if not found.effects.changes:
            logger.info("Starting the stack from it changes nothing.")
        for effect in found.effects.changes:
            who = effect.service or effect.name
            logger.info(f"{effect.action} {effect.kind} {who}")
        if found.effects.removes_volumes:
            logger.warning("A volume would be removed: that is data.")


@compose.command("show", read_only=True)
@click.argument("name")
@pass_context
def compose_show(ctx: Context, name: str) -> None:
    """
    Print the document of a stack, or say why it is not edited here.

    Reading it is a reveal (a document can hold a value typed into it) and is recorded.
    """
    view = StackEditor(_gate()).view(name, actor="cli")
    if ctx.json_output:
        click.echo(json.dumps(view.to_dict()))
        return
    if not view.editable:
        ctx.logger.warning(view.why)
        raise SystemExit(1)
    click.echo(view.text or "", nl=False)


@compose.command("check", read_only=True)
@click.argument("name")
@click.argument("file", type=click.Path(path_type=Path, dir_okay=False, allow_dash=True))
@json_option("Print the judgement as JSON.")
@pass_context
def compose_check(ctx: Context, name: str, file: Path) -> None:
    """
    Judge a document for a stack and write nothing. FILE is a document, or - for standard input.

    Compose resolves it (an include or an extends counts) and the guard reads what it resolves to; what
    would change is Compose's own rehearsal. Exits 1 when something stops it from being applied.
    """
    found = StackEditor(_gate()).check(name, _read_document(file))
    if ctx.json_output:
        click.echo(json.dumps(found.to_dict()))
    else:
        _show_check(ctx.logger, found)
    if not found.valid:
        raise SystemExit(1)


@compose.command("apply")
@click.argument("name")
@click.argument("file", type=click.Path(path_type=Path, dir_okay=False, allow_dash=True))
@click.option(
    "--reason", required=True, help="Why: kept with the version, with who saved it and when."
)
@click.option("--yes", "-y", is_flag=True, help="Apply without asking.")
@json_option("Print what was done as JSON.")
@pass_context
def compose_apply(ctx: Context, name: str, file: Path, reason: str, yes: bool) -> None:
    """
    Apply a document to a stack, behind the health gate.

    It is judged, kept as a version (who, when and why) and applied by the application's update; when the
    stack does not come up, the document before is the one that runs again, and the error says whether it
    answers.
    """
    editor = StackEditor(_gate())
    staged = editor.stage(name, _read_document(file), reason=reason, actor="cli")
    if not ctx.json_output:
        _show_check(ctx.logger, staged.check)
    if not (yes or ctx.json_output or ctx.dry_run):
        click.confirm(
            f"Apply version {staged.version} to {staged.domain}?", default=False, abort=True
        )
    done = editor.apply(
        name,
        staged.version,
        actor="cli",
        trigger=DeploymentTrigger.CLI.value,
        on_phase=ctx.logger.step,
        on_step=ctx.logger.substep,
        logger=ctx.logger,
        verbose=ctx.verbose,
    )
    if ctx.json_output:
        click.echo(json.dumps(done.to_dict()))
        return
    ctx.logger.success(f"{done.domain} runs version {done.version}.")


@compose.command("history", read_only=True)
@click.argument("name")
@json_option("Print the versions as JSON.")
@pass_context
def compose_history(ctx: Context, name: str) -> None:
    """List the versions of a stack's document, newest first: who saved each, when and why."""
    found = StackEditor(_gate()).versions(name)
    if ctx.json_output:
        click.echo(json.dumps([item.to_dict() for item in found]))
        return
    for item in found:
        ctx.logger.key_value(
            f"{item.version}",
            f"{item.created_at}  {item.source:8} {item.actor or 'noust':10} {item.reason}",
        )


@compose.command("diff", read_only=True)
@click.argument("name")
@click.argument("version", type=int)
@pass_context
def compose_diff(ctx: Context, name: str, version: int) -> None:
    """Show what putting a version back would change in what runs now."""
    shown = StackEditor(_gate()).version(name, version)
    if ctx.json_output:
        click.echo(json.dumps({"version": shown.version, "diff": shown.diff}))
        return
    click.echo(shown.diff or "It is what runs now.\n", nl=False)


@compose.command("restore")
@click.argument("name")
@click.argument("version", type=int)
@click.option("--reason", default=None, help="Why; the default says which version it is.")
@click.option("--yes", "-y", is_flag=True, help="Put it back without asking.")
@json_option("Print what was done as JSON.")
@pass_context
def compose_restore(ctx: Context, name: str, version: int, reason: str | None, yes: bool) -> None:
    """Put an earlier version back, by the same road as an edit: judged, kept, applied behind the gate."""
    editor = StackEditor(_gate())
    staged = editor.stage_restore(
        name, version, reason=reason or f"Put back version {version}", actor="cli"
    )
    if not ctx.json_output:
        _show_check(ctx.logger, staged.check)
    if not (yes or ctx.json_output or ctx.dry_run):
        click.confirm(f"Put version {version} back on {staged.domain}?", default=False, abort=True)
    done = editor.apply(
        name,
        staged.version,
        actor="cli",
        trigger=DeploymentTrigger.CLI.value,
        on_phase=ctx.logger.step,
        on_step=ctx.logger.substep,
        logger=ctx.logger,
        verbose=ctx.verbose,
    )
    if ctx.json_output:
        click.echo(json.dumps(done.to_dict()))
        return
    ctx.logger.success(f"{done.domain} runs the document of version {version} again.")
