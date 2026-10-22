# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust compliance`` command group: frameworks, controls and how they combine.

A front end over :mod:`noust.core.compliance`, the same code ``/api/compliance``
serves the console's Settings > Compliance from. Nothing is on by
default; ``enable`` turns on a framework, which is a preset over the controls, and
says first which controls it will change and the clause each one serves. Several
frameworks may be on, and the strictest value of each control wins.

- ``status``, ``frameworks``, ``show`` and ``controls`` read.
- ``enable``, ``disable`` and ``set`` change; loosening (``disable``, a control set
  looser) is what the console asks sudo mode for.
- ``check`` reports per framework, with the clause of each finding, and exits 0
  (ok), 1 (warnings) or 2 (failures).

A framework turns on technical controls that help with a standard. Nothing here
says a server *is* compliant: ``show`` lists what stays organisational.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core import audit
from noust.core.compliance import check as compliance_check
from noust.core.compliance import controls, frameworks
from noust.core.compliance.docs import show as show_value
from noust.core.compliance.manager import ComplianceManager, Plan
from noust.core.exceptions import ValidationError

_STATUS_OUTCOME = {"ok": "ok", "warning": "warning", "fail": "error", "n/a": "skip"}
_EXIT = {"ok": 0, "n/a": 0, "warning": 1, "fail": 2}


@click.group("compliance", cls=NoustGroup)
def cli() -> None:
    """Choose compliance frameworks (ENS, ISO 27001, NIS2, CIS, SOC 2, GDPR) and their controls."""


class FrameworkName(click.ParamType):
    """A compliance framework, by id, in any case."""

    name = "framework"

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> str:
        """
        Args:
            value: What was typed.
            param: The parameter.
            ctx: The context.

        Returns:
            The framework's id.
        """
        name = str(value).strip().lower()
        if not frameworks.exists(name):
            self.fail(
                f"{value!r} is not a framework; the frameworks are: "
                f"{', '.join(frameworks.FRAMEWORK_IDS)}",
                param,
                ctx,
            )
        return name


FRAMEWORK = FrameworkName()


def _source_words(entry: dict[str, Any]) -> str:
    """
    Args:
        entry: A framework as ``describe`` gives it.

    Returns:
        Why it is on, in a word or two.
    """
    if not entry["enabled"]:
        return "off"
    return "on (security.profile)" if entry["source"] == "profile" else "on"


def _effect_lines(ctx: Context, plan: Plan) -> None:
    """
    Print what a plan would change, control by control, with the clauses.

    Args:
        ctx: The CLI context.
        plan: The plan.
    """
    if not plan.effects:
        ctx.logger.info("No control changes value.")
        return
    rows = []
    for effect in plan.effects:
        control = controls.get(effect.control)
        clauses = []
        for name in effect.forced_by:
            clauses.append(f"{name}: {', '.join(effect.clauses.get(name, []))}")
        rows.append(
            [
                effect.control,
                show_value(control, effect.before),
                show_value(control, effect.after),
                "; ".join(clauses) or "-",
            ]
        )
    ctx.logger.table(["Control", "Now", "Then", "Because"], rows)


@cli.command("status", read_only=True)
@json_option("Print the state as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Show which frameworks are on, which controls they force, and which you set yourself."""
    manager = ComplianceManager()
    state = manager.describe()
    if ctx.json_output:
        click.echo(json.dumps(state, indent=2, default=str))
        return
    logger = ctx.logger
    on = [entry for entry in state["frameworks"] if entry["enabled"]]
    if on:
        logger.key_value(
            "Frameworks on", ", ".join(f"{e['name']} ({_source_words(e)})" for e in on)
        )
    else:
        logger.key_value("Frameworks on", "none: every control is as permissive as the default")
    forced = [c for c in state["controls"] if c["locked"]]
    own = [c for c in state["controls"] if not c["locked"] and c["enabled"]]
    logger.key_value("Controls forced by a framework", str(len(forced)))
    logger.key_value("Controls on by their own setting", str(len(own)))
    for name in state["unknown"]:
        logger.warning(f"security.frameworks names {name!r}, which this version does not know.")
    if not on:
        logger.info("See them with: noust compliance frameworks")
    else:
        logger.info("A framework turns on technical controls. It does not make a server compliant.")


@cli.command("frameworks", read_only=True)
@json_option("Print the frameworks as JSON.")
@pass_context
def frameworks_command(ctx: Context) -> None:
    """List the frameworks, whether each is on, and how many controls it asks of."""
    state = ComplianceManager().describe()
    if ctx.json_output:
        click.echo(json.dumps(state["frameworks"], indent=2, default=str))
        return
    ctx.logger.table(
        ["Id", "Framework", "State", "Controls", "Reference"],
        [
            [
                entry["id"],
                entry["name"],
                _source_words(entry),
                str(entry["requirement_count"]),
                entry["reference"],
            ]
            for entry in state["frameworks"]
        ],
    )
    ctx.logger.info(
        "Show one in full: noust compliance show FRAMEWORK. Turn one on: noust compliance enable FRAMEWORK"
    )


@cli.command("show", read_only=True)
@click.argument("framework", type=FRAMEWORK)
@json_option("Print the framework and its whole mapping as JSON.")
@pass_context
def show_command(ctx: Context, framework: str) -> None:
    """Show one framework: what it asks of each control, the clause it serves, and what stays yours."""
    found = frameworks.get(framework)
    if ctx.json_output:
        click.echo(json.dumps(found.to_dict(), indent=2, default=str))
        return
    logger = ctx.logger
    logger.key_value("Framework", found.name)
    logger.key_value("Reference", found.reference)
    logger.info(found.summary)
    rows = []
    for control in controls.CONTROLS:
        required = found.requirement(control.id)
        if required is None:
            continue
        rows.append(
            [
                control.id,
                show_value(control, required.value),
                "; ".join(f"{clause.id} {clause.title}" for clause in required.clauses),
                required.note,
            ]
        )
    logger.table(["Control", "Value", "Clause", "Note"], rows)
    logger.info("What stays organisational (no setting does it for you):")
    for item in found.organisational:
        logger.info(f"  - {item}")
    if found.not_covered:
        logger.info("Technical, and not enforced by Noust:")
        for item in found.not_covered:
            logger.info(f"  - {item}")


@cli.command("controls", read_only=True)
@json_option("Print the controls as JSON.")
@pass_context
def controls_command(ctx: Context) -> None:
    """List every control: its value in force, its default, its setting, and who forces it."""
    state = ComplianceManager().describe()
    if ctx.json_output:
        click.echo(json.dumps(state["controls"], indent=2, default=str))
        return
    rows = []
    for item in state["controls"]:
        control = controls.get(item["id"])
        rows.append(
            [
                item["id"],
                show_value(control, item["value"]),
                show_value(control, item["standard"]),
                ", ".join(item["forced_by"]) or "-",
                item["key"] or "-",
            ]
        )
    ctx.logger.table(["Control", "Value", "Default", "Forced by", "Setting"], rows)


def _apply(
    ctx: Context, plan: Plan, *, yes: bool, acknowledge: bool, title: str, event_target: str
) -> None:
    """
    Show a plan, ask, apply it and record it.

    Args:
        ctx: The CLI context.
        plan: What was asked.
        yes: Do not ask.
        acknowledge: Go ahead past the warnings.
        title: The sentence for the confirmation.
        event_target: The audit target.

    Raises:
        click.Abort: When the person declines.
    """
    manager = ComplianceManager()
    if plan.empty:
        ctx.logger.info("Nothing changes.")
        return
    if ctx.json_output:
        applied = manager.apply(plan, acknowledge=acknowledge or yes)
        click.echo(
            json.dumps(
                {"plan": plan.to_dict(), "applied": True, "after": list(applied.enabled_after)}
            )
        )
    else:
        _effect_lines(ctx, plan)
        for warning in plan.warnings:
            count = f" ({warning.count})" if warning.count else ""
            ctx.logger.warning(f"{warning.id}{count}: {_WARNING_WORDS.get(warning.id, warning.id)}")
        if ctx.dry_run:
            ctx.logger.info("would apply the change")
            return
        if not yes:
            click.confirm(title, abort=True)
        applied = manager.apply(plan, acknowledge=True)
        ctx.logger.success(
            "Done. " + ("Frameworks on: " + (", ".join(applied.enabled_after) or "none") + ".")
        )
    actor = audit.cli_actor()
    if plan.enable or plan.disable:
        audit.record(
            "security.framework.change",
            actor=actor,
            target=event_target,
            details={"enabled": list(plan.enable), "disabled": list(plan.disable)},
        )
    for control_id, value in plan.settings.items():
        audit.record(
            "security.control.change",
            actor=actor,
            target=f"control:{control_id}",
            details={"value": value},
        )


#: Said under a warning, in the words of the terminal.
_WARNING_WORDS = {
    "no_security_officer": "no active account has the security role, and the administrator will "
    "stop governing accounts: create one first (noust user create NAME --role security)",
    "separation_conflicts": "people hold accounts that cannot go together; record an exception "
    "or split them (noust user list)",
}


@cli.command("enable")
@click.argument("frameworks_", metavar="FRAMEWORK...", nargs=-1, required=True, type=FRAMEWORK)
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print the plan as JSON.")
@pass_context
def enable_command(ctx: Context, frameworks_: tuple[str, ...], yes: bool) -> None:
    """
    Turn frameworks on, after showing which controls change and the clause each serves.

    The strictest value any enabled framework asks wins, and none can be loosened
    below it while it is on. A framework turns on technical controls; it does not
    make a server compliant (noust compliance show FRAMEWORK lists what stays yours).
    """
    manager = ComplianceManager()
    plan = manager.plan(enable=frameworks_)
    _apply(
        ctx,
        plan,
        yes=yes,
        acknowledge=yes,
        title=f"Turn on {', '.join(frameworks_)}?",
        event_target="frameworks:" + ",".join(plan.enable),
    )


@cli.command("disable")
@click.argument("frameworks_", metavar="FRAMEWORK...", nargs=-1, required=True, type=FRAMEWORK)
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print the plan as JSON.")
@pass_context
def disable_command(ctx: Context, frameworks_: tuple[str, ...], yes: bool) -> None:
    """
    Turn frameworks off. What they forced goes back to the default, or to a stricter
    framework or setting that still asks for it.
    """
    manager = ComplianceManager()
    plan = manager.plan(disable=frameworks_)
    _apply(
        ctx,
        plan,
        yes=yes,
        acknowledge=yes,
        title=f"Turn off {', '.join(frameworks_)}? Protection goes down.",
        event_target="frameworks:" + ",".join(plan.disable),
    )


class ControlName(click.ParamType):
    """A compliance control, by id."""

    name = "control"

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> str:
        """
        Args:
            value: What was typed.
            param: The parameter.
            ctx: The context.

        Returns:
            The control's id.
        """
        name = str(value).strip().lower().replace("-", "_")
        if not controls.exists(name):
            self.fail(
                f"{value!r} is not a control; see them with: noust compliance controls", param, ctx
            )
        return name


@cli.command("set")
@click.argument("control", type=ControlName())
@click.argument("value")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print the plan as JSON.")
@pass_context
def set_command(ctx: Context, control: str, value: str, yes: bool) -> None:
    """
    Set a control's own value (on or off, a number, a choice; "none" for no limit).

    It can be stricter than any enabled framework asks, never looser.
    """
    manager = ComplianceManager()
    try:
        plan = manager.plan(settings={control: value})
    except ValidationError as exc:
        raise click.UsageError(f"{exc.message} {exc.details}".strip()) from exc
    _apply(
        ctx,
        plan,
        yes=yes,
        acknowledge=yes,
        title=f"Set {control} to {value}?",
        event_target=f"control:{control}",
    )


@cli.command("check", read_only=True)
@click.option(
    "--framework",
    "names",
    multiple=True,
    type=FRAMEWORK,
    help="Judge this framework (repeat for more); the ones that are on by default.",
)
@click.option("--all", "show_all", is_flag=True, help="Also show the controls that pass.")
@click.option(
    "--settings-only", is_flag=True, help="Judge the settings without reading the server's state."
)
@json_option("Print the result as JSON.")
@pass_context
def check_command(
    ctx: Context, names: tuple[str, ...], show_all: bool, settings_only: bool
) -> None:
    """
    Judge this server framework by framework, with the clause of each finding.

    Exits 0 when every control is in force, 1 when some only warn and 2 when any
    fails. A pass means the technical control is in force, not that the server
    complies with the framework.
    """
    result = compliance_check.run(list(names) or None, gather_facts=not settings_only)
    audit.record("compliance.read", actor=audit.cli_actor(), target="compliance:check")
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        logger = ctx.logger
        if not result.results:
            logger.info(
                "No framework is on. Turn one on (noust compliance enable FRAMEWORK) or judge "
                "one anyway (noust compliance check --framework FRAMEWORK)."
            )
        for entry in result.results:
            counts = entry.counts
            logger.info(
                f"{entry.framework.name} ({'on' if entry.enabled else 'off'}): "
                f"{counts['ok']} ok, {counts['warning']} warning(s), {counts['fail']} failure(s), "
                f"{counts['n/a']} not applicable."
            )
            for finding in entry.findings:
                if finding.status in ("ok", "n/a") and not show_all:
                    continue
                logger.check(
                    f"{finding.control} ({'; '.join(finding.clauses)})",
                    finding.summary,
                    _STATUS_OUTCOME[finding.status],
                )
                for line in finding.evidence:
                    logger.info(f"    {line}")
                if finding.remediation and finding.status != "ok":
                    logger.info(f"    Fix: {finding.remediation}")
            if entry.framework.organisational:
                logger.info(
                    "  Stays organisational: "
                    + " ".join(entry.framework.organisational[:2])
                    + " ..."
                )
        for area, error in result.errors.items():
            logger.warning(f"Could not read the {area}: {error}")
        logger.info(compliance_check.DISCLAIMER)
    code = _EXIT[result.verdict]
    if code:
        raise click.exceptions.Exit(code)
