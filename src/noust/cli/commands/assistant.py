# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust assistant`` command group: the built-in assistant, which is off until it is turned on
(item 171).

An AI assistant that investigates what is wrong with an application and proposes what to do, with a
model the operator chooses (a provider of their own, or a service that speaks the OpenAI-compatible
protocol). Nothing leaves the server until it is set up here or in the console, and what leaves is
what ``docs/assistant.md`` says. Handlers hold no logic: they are a client of
:mod:`noust.core.assistant`, like the console's routes.

**The key is never an argument.** It is read from one line of standard input (``--key-stdin``) or
from the ``NOUST_ASSISTANT_API_KEY`` variable, because everything on a command line is visible in
``ps`` to every local user and lands in shell history.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.team_common import record
from noust.core import paths

#: Where the key may come from besides standard input: ``NOUST_ASSISTANT_API_KEY``.
KEY_VARIABLE = "ASSISTANT_API_KEY"


@click.group("assistant", cls=NoustGroup)
def cli() -> None:
    """Set up and look after the built-in assistant (an AI, with a model of your choice)."""


def _actor() -> str:
    """
    Returns:
        Who runs the command, as the audit trail names them.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _key(from_stdin: bool) -> str | None:
    """
    Args:
        from_stdin: Read the key from one line of standard input.

    Returns:
        The key, from standard input or from the environment; None when neither gives one.

    Raises:
        click.UsageError: Standard input was asked for and is empty.
    """
    if from_stdin:
        line = sys.stdin.readline(4096).rstrip("\r\n")
        if not line:
            raise click.UsageError(
                "No key on standard input. Pipe it in, such as: "
                "printf '%s\\n' \"$KEY\" | noust assistant configure --key-stdin"
            )
        return line
    return paths.getenv(KEY_VARIABLE) or None


def _pairs(values: tuple[str, ...], what: str, example: str) -> list[tuple[str, str]]:
    """
    Args:
        values: ``NAME=VALUE`` texts.
        what: What they are, for the message.
        example: One, for the message.

    Returns:
        ``(name, value)`` for each.

    Raises:
        click.BadParameter: One has no ``=``.
    """
    pairs = []
    for text in values:
        name, separator, value = text.partition("=")
        if not separator or not name or not value:
            raise click.BadParameter(f"{what} are NAME=VALUE, such as {example}")
        pairs.append((name, value))
    return pairs


def _prices(values: tuple[str, ...]) -> dict[str, dict[str, str]] | None:
    """
    Args:
        values: ``MODEL=INPUT,OUTPUT`` texts, the price of a million tokens each way.

    Returns:
        The price table, or None when none was given.

    Raises:
        click.BadParameter: One is not of that shape.
    """
    if not values:
        return None
    table: dict[str, dict[str, str]] = {}
    for model, value in _pairs(values, "prices", "llama3=0.2,0.6"):
        given, comma, said = value.partition(",")
        if not comma:
            raise click.BadParameter("a price is MODEL=INPUT,OUTPUT, such as llama3=0.2,0.6")
        table[model] = {"input_per_million": given, "output_per_million": said}
    return table


def _options(command: Any) -> Any:
    """
    Args:
        command: A Click command function.

    Returns:
        The command with the options that choose a provider, its limits and its prices.
    """
    for option in reversed(
        [
            click.option(
                "--preset", default=None, help="A provider by name: see 'noust assistant status'."
            ),
            click.option(
                "--base-url",
                default=None,
                help="The provider's address, up to its version, such as https://api.example.org/v1.",
            ),
            click.option(
                "--model", default=None, help="The model's name, as the provider calls it."
            ),
            click.option(
                "--key-stdin",
                is_flag=True,
                help="Read the provider's key from one line of standard input. "
                f"NOUST_{KEY_VARIABLE} is read when this is not given. Never an argument.",
            ),
            click.option(
                "--location",
                type=click.Choice(["eu", "outside"]),
                default=None,
                help="Where a public provider keeps what it is sent, when you have a contract "
                "that says so. An address on this machine or your network says it itself.",
            ),
            click.option(
                "--flow-reason",
                default=None,
                help="Why a provider outside is an authorized flow (the contract, the data "
                "processing agreement). The ENS profile asks for it before the assistant is "
                "turned on with one.",
            ),
            click.option(
                "--allow-private-host",
                is_flag=True,
                help="Say that this server may call the provider's host though it is on this "
                "machine or your network: the host is added to security.allow_private_hosts.",
            ),
            click.option(
                "--personal-data/--no-personal-data",
                "mask_personal",
                default=None,
                help="Disguise emails and addresses with markers before they are sent (always on "
                "for a provider that is not on this machine or your network).",
            ),
            click.option(
                "--limit",
                "limits",
                multiple=True,
                metavar="NAME=VALUE",
                help="Change a limit: steps_per_turn, input_tokens_per_conversation, "
                "spend_per_conversation, spend_per_month, actions_per_hour. Never removed.",
            ),
            click.option(
                "--price",
                "prices",
                multiple=True,
                metavar="MODEL=INPUT,OUTPUT",
                help="What a model costs per million tokens, in the currency below. Replaces the "
                "price table. Without one there is no limit on spending, only on tokens.",
            ),
            click.option("--currency", default=None, help="What the prices are in, such as EUR."),
            click.option(
                "--no-probe",
                is_flag=True,
                help="Do not ask the provider what it can do afterwards.",
            ),
        ]
    ):
        command = option(command)
    return command


def _configure(
    ctx: Context,
    *,
    actor: str,
    key_stdin: bool,
    limits: tuple[str, ...],
    prices: tuple[str, ...],
    no_probe: bool,
    allow_private_host: bool,
    **given: Any,
) -> Any:
    """
    Save what was given, and probe the provider if it changed.

    Args:
        ctx: The command context.
        actor: Who it is for, for the record.
        key_stdin: Read the key from standard input.
        limits: ``NAME=VALUE`` limits.
        prices: ``MODEL=INPUT,OUTPUT`` prices.
        no_probe: Skip the probe.
        allow_private_host: The operator's consent to call a host of this machine or network.
        **given: The other options.

    Returns:
        The settings as they are now.
    """
    from noust.core.assistant.settings import AssistantSettings

    changes = {k: v for k, v in given.items() if v is not None}
    key = _key(key_stdin)
    if key is not None:
        changes["api_key"] = key
    if limits:
        changes["limits"] = dict(_pairs(limits, "limits", "steps_per_turn=10"))
    table = _prices(prices)
    if table is not None:
        changes["prices"] = table
    store = AssistantSettings()
    if changes or allow_private_host:
        store.update(actor=actor, allow_private_host=allow_private_host, **changes)
        record(
            "assistant.settings",
            "setting:assistant",
            {"changed": sorted(changes), "key_set": "api_key" in changes},
        )
        ctx.logger.success("Saved.")
    current = store.read()
    if (
        current.configured
        and not no_probe
        and {"preset", "base_url", "model", "api_key"} & set(changes)
    ):
        _probe(ctx, store)
    return store.read()


def _probe(ctx: Context, store: Any) -> Any:
    """
    Probe the chosen provider, keep what was found and say it.

    Args:
        ctx: The command context.
        store: The :class:`~noust.core.assistant.settings.AssistantSettings`.

    Returns:
        The probe's result.
    """
    from noust.core.assistant.probe import probe_configured

    result = probe_configured(store, store.read())
    record(
        "assistant.probe",
        "setting:assistant",
        {"reachable": result.reachable, "tools": result.tools},
    )
    if not ctx.json_output:
        for sentence in result.sentences:
            ctx.logger.info(sentence.text)
            if sentence.hint:
                ctx.logger.info(f"  {sentence.hint}")
    return result


def _status_document(settings: Any) -> dict[str, Any]:
    """
    Args:
        settings: The settings.

    Returns:
        What ``status --json`` prints: everything but the key.
    """
    from noust.core.assistant.presets import PRESETS

    document: dict[str, Any] = settings.to_public()
    document["presets"] = [p.id for p in PRESETS]
    return document


@cli.command("status", read_only=True)
@json_option("Print the settings as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Say whether the assistant is on, which provider it uses and what the limits are."""
    from noust.core.assistant.presets import PRESETS
    from noust.core.assistant.settings import AssistantSettings

    settings = AssistantSettings().read()
    if ctx.json_output:
        click.echo(json.dumps(_status_document(settings)))
        return
    ctx.logger.info("The assistant is " + ("on." if settings.enabled else "off."))
    if settings.configured:
        ctx.logger.info(f"Provider: {settings.base_url}, model {settings.model}")
        ctx.logger.info(
            f"Where what you say to it ends up: {settings.location}"
            + ("; emails and addresses are disguised." if settings.mask_personal_effective else ".")
        )
        ctx.logger.info("A key is kept." if settings.has_key else "No key is kept.")
        ctx.logger.info(
            "Its model calls tools, so it can investigate."
            if settings.calls_tools
            else "It only explains what it is shown (the probe has not found its model calling "
            "tools)."
        )
    else:
        ctx.logger.info("No provider is chosen: 'noust assistant enable --preset NAME --model M'.")
    ctx.logger.info(
        "Limits: " + ", ".join(f"{k} {v}" for k, v in settings.limits.to_dict().items())
    )
    ctx.logger.info("Providers by name: " + ", ".join(p.id for p in PRESETS) + ".")


@cli.command("configure")
@_options
@pass_context
def configure_command(ctx: Context, **options: Any) -> None:
    """
    Choose the provider, its limits and its prices, without turning the assistant on.

    \b
      printf '%s\\n' "$KEY" | noust assistant configure --preset mistral --model MODEL --key-stdin
      noust assistant configure --preset ollama --model llama3 --allow-private-host
    """
    from noust.core.assistant.settings import (
        AssistantSettings,  # noqa: F401 - fails early if absent
    )

    _configure(ctx, actor=_actor(), **options)


@cli.command("enable")
@_options
@pass_context
def enable_command(ctx: Context, **options: Any) -> None:
    """
    Turn the assistant on, choosing the provider first if it is given here.

    The provider is probed, and it is turned on only if it answered. On this server's own
    machine or network it needs --allow-private-host once: that is the consent that this server
    may call it.
    """
    from noust.core.assistant.settings import AssistantSettings

    _configure(ctx, actor=_actor(), **options)
    AssistantSettings().enable(actor=_actor())
    record("assistant.enable", "setting:assistant")
    ctx.logger.success(
        "The assistant is on. People whose role holds assistant.use (administrator and operator) "
        "are offered it in the console."
    )


@cli.command("disable")
@pass_context
def disable_command(ctx: Context) -> None:
    """Turn the assistant off and revoke every token it holds, at once."""
    from noust.cli.web_state import assistant_identity
    from noust.core.assistant.settings import AssistantSettings

    AssistantSettings().disable(actor=_actor())
    record("assistant.disable", "setting:assistant")
    revoked = assistant_identity().revoke_all()
    ctx.logger.success(f"The assistant is off. {revoked} token(s) it held were revoked.")


@cli.command("probe")
@json_option("Print what was found as JSON.")
@pass_context
def probe_command(ctx: Context) -> None:
    """Ask the provider what it can do: whether it answers, has the model and calls tools."""
    from noust.core.assistant.settings import AssistantSettings
    from noust.core.exceptions import ValidationError

    store = AssistantSettings()
    if not store.read().configured:
        raise ValidationError(
            "Choose a provider and a model before probing it",
            details="noust assistant configure --preset NAME --model M",
        )
    result = _probe(ctx, store)
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))


@cli.command("usage", read_only=True)
@json_option("Print the usage as JSON.")
@pass_context
def usage_command(ctx: Context) -> None:
    """Show what the assistant cost this month and the limits it runs under."""
    from noust.core.assistant.limits import UsageMeter
    from noust.core.assistant.settings import AssistantSettings

    settings = AssistantSettings().read()
    report = UsageMeter(settings.limits, settings.prices, settings.currency).report()
    month = report.month
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "month_start": report.month_start,
                    "currency": report.currency,
                    "month": vars(month),
                    "limits": report.limits.to_dict(),
                    "priced_models": list(report.priced_models),
                }
            )
        )
        return
    ctx.logger.info(
        f"Since {report.month_start[:10]}: {month.steps} step(s), {month.prompt_tokens} tokens "
        f"sent, {month.completion_tokens} said, {month.spend:.2f} {report.currency} spent."
    )
    if month.unpriced_steps:
        ctx.logger.info(
            f"{month.unpriced_steps} step(s) were of a model with no price: spending is not "
            "limited for it, only tokens ('noust assistant configure --price MODEL=IN,OUT')."
        )
    if month.estimated_steps:
        ctx.logger.info(
            f"{month.estimated_steps} step(s) used an estimate: the provider sent no usage."
        )
    ctx.logger.info(
        "Limits: " + ", ".join(f"{k} {v}" for k, v in report.limits.to_dict().items()) + "."
    )


def _call_line(proposal: dict[str, Any]) -> str:
    """
    Args:
        proposal: A proposal as the console draws it.

    Returns:
        The call that would carry it out, on one line.
    """
    call = proposal["call"]
    line = f"{call['method']} {call['path']}"
    if call.get("query"):
        line += "?" + "&".join(f"{k}={v}" for k, v in call["query"])
    if call.get("body") is not None:
        line += " " + json.dumps(call["body"], separators=(",", ":"), ensure_ascii=False)
    return line


@cli.command("ask")
@click.argument("question", nargs=-1, required=True)
@click.option(
    "--as",
    "as_user",
    required=True,
    help="The account the assistant acts for: it holds at most what that account holds.",
)
@click.option("--server", help="The server it is about: a name, or 'all' (a central's servers).")
@click.option(
    "--page",
    "page_text",
    help="The page the question is asked from: deployment:ID, diagnosis:DOMAIN, alert:RULE or page:/ROUTE.",
)
@json_option("Print the turn as JSON instead of the text.")
@pass_context
def ask_command(
    ctx: Context,
    question: tuple[str, ...],
    as_user: str,
    server: str | None,
    page_text: str | None,
) -> None:
    """
    Ask the assistant something and read its answer here.

    It explains and investigates, with the same tools and limits as in the console, as the account named
    by --as. What it proposes is listed with the call that would carry it out and is NOT carried out: a person
    does it in the console, in their own session.
    """
    from noust.web.assistant.ask import ask_as, parse_page

    text = " ".join(question).strip()
    if not text:
        raise click.UsageError("Say what to ask.")
    live = not ctx.json_output
    turn = ask_as(
        as_user,
        text,
        server="*" if server == "all" else server,
        page=parse_page(page_text),
        on_text=(lambda piece: click.echo(piece, nl=False)) if live else None,
    )
    if ctx.json_output:
        click.echo(json.dumps(turn))
        return
    click.echo("")
    for step in turn["steps"]:
        said = (step.get("what") or {}).get("text") or step["tool"]
        click.echo(f"  - {said} ({step['state']})", err=True)
    for refused in turn["not_offered"]:
        why = refused["why"] or {}
        click.echo(
            f"  Not offered: {(refused['title'] or {}).get('text')}. {why.get('text')}", err=True
        )
        if why.get("hint"):
            click.echo(f"    {why['hint']}", err=True)
    for proposal in turn["proposals"]:
        title = (proposal["title"] or {}).get("text") or proposal["tool"]
        if proposal["klass"] == "alone":
            click.echo(f"  Done as your policy lets it: {title} ({proposal['state']})", err=True)
            continue
        click.echo(f"  Proposed, NOT done ({proposal['klass']}): {title}", err=True)
        click.echo(f"    call: {_call_line(proposal)}", err=True)
        click.echo("    Carry it out in the console, with your own session.", err=True)
    if turn["stop"]:
        click.echo(f"  Stopped: {turn['stop']['text']}", err=True)
        if turn["stop"].get("hint"):
            click.echo(f"    {turn['stop']['hint']}", err=True)
    if turn["error"]:
        click.echo(f"  It could not answer: {turn['error'].get('text')}", err=True)
    model = turn.get("model")
    if model:
        click.echo(f"  (An AI: {model}; what you said went to: {turn['location']}.)", err=True)
