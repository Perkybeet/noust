# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sentences the server says for the console to translate.

The console speaks English and Spanish, and a sentence that arrives from the server as a bare
string can only be shown as it came: a Spanish console with an English paragraph in it, which
is what an operator saw in Domains > Tunnels and in the installation check. A sentence that a
person is meant to read therefore carries, beside its English text, a **code** and its
**parameters**. The console looks the code up in its own catalog (``serverText.<code>``) and
puts the parameters in; when it has no entry for a code it shows the English text, which is
always sent, so a server newer than its console still says something true.

This module is the one place the English sentences live (rule 3): the CLI prints
:attr:`ServerText.text`, the API sends it together with :meth:`ServerText.to_dict`, and a test
holds the catalog to the code that asks for it. A sentence is a whole sentence with
placeholders, never a fragment to glue to another one, because word order is not the same in
both languages.

**A sentence's fix** is the catalog entry named like the sentence plus :data:`HINT_SUFFIX`
(``tunnels.store_sealed`` and ``tunnels.store_sealed.hint``): it is said with the same values,
and a sentence without one has none. One code in a response therefore tells the console both
the problem and the way out.

**What is never a sentence here**: another program's own output (nginx's, systemd's,
certbot's), a command to run, a path, a version a program printed, a name somebody typed, a
control's id, a person. They are values, sent as parameters or as the text beside the code, and
the console shows them as they are (``docs/api.md``, "Sentences with a code"). A value named
``command``, or ending in ``_command``, is a command: :func:`is_command`.

**The console is held to this catalog.** :func:`typescript_codes` writes the table
``panel/src/api/serverTextCodes.gen.ts`` (``scripts/generate_server_text.py``), and a test of the
console fails for a code without an entry in English and in Spanish, with the same values. There
is no other way for a sentence to reach the console with a code: a module that says something to
a person in words of its own (a flag, a ``what``/``how`` pair, a dict of fields) has not been
given codes yet.
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: What a value may be: the console puts these in a sentence, nothing structured.
Param = str | int | float | bool

#: Appended to a sentence's code to name its fix.
HINT_SUFFIX = ".hint"

#: What a call that waits for a second person's decision is told to do next, said twice below.
_SEND_AGAIN = (
    "Once approved, send exactly the same call again with the header {header}: {request}, "
    "within {minutes} minutes."
)

#: Every sentence, by code. Grouped by the area that says it. ``tests/test_server_text.py`` holds
#: it to the code: a code asked for and absent fails, and a sentence nobody says fails.
CATALOG: dict[str, str] = {
    # -- Tunnels (Domains > Tunnels): what is missing before one can be made --------------
    "tunnels.cloudflared_missing": "cloudflared is not installed",
    "tunnels.cloudflared_missing.hint": (
        "Noust installs it from Cloudflare's own package repository, {url}, with the key Cloudflare "
        "signs it with: run 'noust tunnel install-cloudflared', then this command again."
    ),
    "tunnels.store_sealed": "The secret store is sealed",
    "tunnels.store_sealed.hint": (
        "A tunnel's connector reads its token at boot, with nobody to type a passphrase. "
        "Run tunnels on a server, not on a sealed central."
    ),
    "tunnels.no_account": "There is no Cloudflare account to manage a tunnel with",
    "tunnels.no_account.hint": (
        "Add one with Account > Cloudflare Tunnel:Edit: "
        "noust dns provider add NAME --kind cloudflare --token-stdin"
    ),
    # -- What an operator may call an object (noust.validators.object_names) ----------------
    "names.rule.words": (
        "Use lower-case letters, digits, '-' and '_', starting with a letter, "
        "at most {max} characters."
    ),
    "names.rule.dashes": (
        "Use lower-case letters, digits and single dashes, starting with a letter, "
        "at most {max} characters."
    ),
    "names.rule.underscores": (
        "Use lower-case letters, digits and single underscores, starting with a letter, "
        "at most {max} characters."
    ),
    # -- Mail an account is sent (an invitation, a password reset) -------------------------
    "mail.no_transport": "There is no way to send mail from this server",
    "mail.no_transport.hint": (
        "Set monitor.smtp.* or monitor.email_api.* (Settings > Channels), then ask again."
    ),
    "mail.no_public_url": "The console has no public address to put in the link",
    "mail.no_public_url.hint": "Set web.public_url to the address people open the console at.",
    "invitation.mail_failed": "The invitation could not be mailed: {reason}",
    "invitation.mail_failed.hint": "The invitation exists: hand its link over yourself.",
    # -- The installation check (Settings > About, noust setup doctor) ---------------------
    # Sections: the heading's text is its own key in the console; only the sentence that
    # explains one is here.
    "doctor.platform.supported": "{label}: supported",
    "doctor.platform.pip_only": "{label}: installed with pip, there is no package for it",
    "doctor.platform.best_effort": "{label}: best effort, not a tested platform",
    "doctor.platform.unsupported": "{label}: not a platform Noust declares support for",
    "doctor.platform.eol": "{release} stops receiving security updates on {date}",
    "doctor.platform.explain": "The platforms Noust installs on are listed in docs/PLATFORMS.md",
    "doctor.memory.unreadable": "Memory: could not be read",
    "doctor.memory.below_floor": "Memory: {amount}, under the {floor} Noust needs to run",
    "doctor.memory.below_floor_swap": (
        "Memory: {amount}, swap {swap}, under the {floor} Noust needs to run"
    ),
    "doctor.memory.below_minimum": "Memory: {amount}, under the {minimum} a server is sized for",
    "doctor.memory.below_minimum_swap": (
        "Memory: {amount}, swap {swap}, under the {minimum} a server is sized for"
    ),
    "doctor.memory.ok": "Memory: {amount} (minimum {minimum})",
    "doctor.memory.ok_swap": "Memory: {amount}, swap {swap} (minimum {minimum})",
    "doctor.memory.note": (
        "Builds are what need memory; a swap file lets one that needs more than the server has "
        "finish slowly instead of being killed."
    ),
    "doctor.cpu.unreadable": "Processors: could not be read",
    "doctor.cpu.below_floor": "Processors: {amount} (Noust needs at least {floor} to run)",
    "doctor.cpu.below_minimum": "Processors: {amount} (a server is sized for at least {minimum})",
    "doctor.cpu.ok": "Processors: {amount} (minimum {minimum})",
    "doctor.cpu.note": "Noust and a build need at least one.",
    "doctor.disk.unreadable": "Free disk: could not be read",
    "doctor.disk.below_floor": (
        "Free disk: {free} free of {total} at {path}, under the {floor} Noust needs to run"
    ),
    "doctor.disk.below_minimum": (
        "Free disk: {free} free of {total} at {path}, under the {minimum} a server is sized for"
    ),
    "doctor.disk.ok": "Free disk: {free} free of {total} at {path} (minimum {minimum})",
    "doctor.disk.note": (
        "'noust server cleanup' removes what Noust can prove is unused; every release on disk "
        "keeps its code and dependencies."
    ),
    "doctor.port.free": "Port {port}: free (the web server is not listening on it yet)",
    "doctor.port.web_server": "Port {port}: {holders}",
    "doctor.port.container": "Port {port}: published by a container ({holders})",
    "doctor.port.container_note": (
        "Another platform's proxy or a Compose stack holds it, so nginx cannot listen on it "
        "until that stops."
    ),
    "doctor.port.held": "Port {port}: held by {holders}, not by the web server",
    "doctor.port.held_note": (
        "A certificate is asked for on port 80 and sites are served on 443: the web server "
        "cannot start while another program holds them."
    ),
    "doctor.program.version": "{command}: {version}",
    "doctor.program.installed": "{command}: installed",
    "doctor.program.missing": "{command}: not installed",
    "doctor.program.optional_missing": "{command}: not installed (optional)",
    "doctor.web.none": "Web server: not installed",
    "doctor.web.running": "  {unit} service: running",
    "doctor.web.stopped": "  {unit} service: not running",
    "doctor.node.npm_missing": "npm: not installed, although it ships with Node.js",
    "doctor.node.not_needed": "node: not installed (only Node.js applications need it)",
    "doctor.python.missing": "python3: not installed, needed for Python apps",
    "doctor.config.apps_dir_ok": "Apps directory: {path}",
    "doctor.config.apps_dir_missing": "Apps directory: {path} not found",
    "doctor.config.log_dir_ok": "Log directory: {path}",
    "doctor.config.log_dir_missing": "Log directory: {path} not found",
    "doctor.config.file_ok": "Config file: {path}",
    "doctor.config.file_missing": "Config file: {path} not found",
    "doctor.ssh.no_key": "SSH key: not found",
    "doctor.ssh.key_ok": "SSH key: {path}",
    "doctor.ssh.github_connected": "GitHub SSH: connected",
    "doctor.ssh.github_refused": "GitHub SSH: not configured",
    "doctor.ssh.github_note": "Add your key to GitHub: https://github.com/settings/keys",
    "doctor.ssh.github_untested": "GitHub SSH: not tested here",
    "doctor.relays.left": "{domain}: {what}",
    "doctor.relays.unread": "Not checked: {reason}",
    "doctor.names.explain": (
        "Nothing here stops working: Noust accepts the old names while they are met. "
        "'noust setup rename' finishes what can be finished without restarting anything."
    ),
    # -- The terminal: what keeps it from opening, and its limits ----------------------------
    # A command is a value ({command}): the console shows it as it is, and a test runs it.
    "terminal.server_off": "The terminal on the server itself is off (terminal.server.enabled)",
    "terminal.server_off.hint": (
        "It is a root shell, so it stays off until an administrator turns it on: {command}"
    ),
    "terminal.server_control": (
        "The control {control} is set to refused on this server, so the terminal on the server "
        "itself stays off"
    ),
    "terminal.server_control.hint": (
        "No framework asks for it: it was set in Settings > Controls or with the command line. "
        "To allow it: {command}"
    ),
    "terminal.server_framework": (
        "The compliance framework in force ({frameworks}) keeps the terminal on the server "
        "itself off (the control {control} is refused)"
    ),
    "terminal.server_framework.hint": (
        "A root shell in the browser is the console at its most root-equivalent. To allow it "
        "anyway, which is put on record: {command}. "
        "Or turn the framework off: {turn_off_command}"
    ),
    "terminal.disabled": "Terminals are turned off on this server",
    "terminal.disabled.hint": "An administrator turns them on with: {command}",
    "terminal.limit_total": "{open} terminals are open, the most this server allows",
    "terminal.limit_total.hint": "Close one, or raise terminal.max_sessions.",
    "terminal.limit_user": "You already have {open} terminals open, the most one person may",
    "terminal.limit_user.hint": "Close one, or raise terminal.max_sessions_per_user.",
    # -- The settings a central manages on every server (noust.fleet.settings) ---------------
    "fleet_settings.unknown_kind": "{kind} is not something the fleet manages",
    "fleet_settings.unknown_kind.hint": "The fleet manages: {kinds}.",
    "fleet_settings.kind_not_yet": "The central cannot manage {kind} for the fleet yet",
    "fleet_settings.kind_not_yet.hint": "For now, set it on each server, in its own settings.",
    "fleet_settings.value_not_object": "The value of a {kind} must be an object of fields",
    "fleet_settings.value_not_object.hint": (
        "Send the fields that a server's own settings take for a {kind}."
    ),
    "fleet_settings.unknown_field": "A {kind} has no field called {field}",
    "fleet_settings.unknown_field.hint": "A {kind} takes: {fields}.",
    "fleet_settings.missing_field": "A {kind} needs {field}",
    "fleet_settings.missing_field.hint": "A server cannot make one without it.",
    "fleet_settings.value_too_large": "This {kind} is larger than the {limit} bytes a setting may be",
    "fleet_settings.secret_missing": "{field} is hidden and nothing is stored for it yet",
    "fleet_settings.secret_missing.hint": (
        "Type the secret again: it is only shown as {redacted} when one is stored."
    ),
    "fleet_settings.bad_scope": "{scope} is not a place a setting of the fleet can be set",
    "fleet_settings.bad_scope.hint": "Use 'fleet' for every server, or 'node:<server>' for one.",
    "fleet_settings.permission_missing": "Setting a {kind} for the fleet needs the permission {permission}",
    "fleet_settings.permission_missing.hint": (
        "It is the permission that setting one on a single server asks: ask an administrator "
        "for a role that has it."
    ),
    "fleet_settings.unknown_node": "There is no server called {node} in this fleet",
    "fleet_settings.unknown_node.hint": "List the servers with: {command}",
    "fleet_settings.managed_missing": "This server holds no {kind} called {name}",
    "fleet_settings.managed_missing.hint": (
        "A setting is recorded as the fleet's after the central has written it: write it first."
    ),
    "fleet_settings.not_managed": "No central manages the {kind} {name} on this server",
    "fleet_settings.not_managed.hint": "Only a setting a central pushed can be asked back.",
    "fleet_settings.sync_ceiling": (
        "This server lets its central do at most '{level}', and applying the fleet's settings "
        "needs 'admin'"
    ),
    "fleet_settings.sync_ceiling.hint": "Raise it on this server, as root:\n  {command}",
    "fleet_settings.sync_route": "A push of the fleet's settings cannot {method} {path}",
    "fleet_settings.sync_route.hint": (
        "It may only write the settings the fleet manages: channels, alert rules, backup "
        "destinations, registry logins and shared variables."
    ),
    "fleet_settings.sync_required": (
        "Only the central that manages this server can record a setting as the fleet's"
    ),
    "fleet_settings.sync_required.hint": (
        "The central does it when it applies a setting; nobody records one by hand."
    ),
    "fleet_settings.backend_changed": (
        "The backup destination {name} is {have} on this server and the fleet wants it {want}"
    ),
    "fleet_settings.backend_changed.hint": (
        "No route changes the backend of a destination: remove it on that server, or name the "
        "fleet's one differently."
    ),
    "fleet_settings.item_conflict": (
        "The {kind} {name} already exists on this server and is not the fleet's"
    ),
    "fleet_settings.item_conflict.hint": (
        "Apply again letting the fleet take it over, or give the fleet's setting another name."
    ),
    "fleet_settings.item_unsupported": (
        "This server cannot take a {kind} from the fleet: it does not offer {operation}"
    ),
    "fleet_settings.item_unsupported.hint": "It runs an older Noust: update it first.",
    "fleet_settings.no_such_setting": "The fleet has no {kind} called {name}",
    "fleet_settings.no_such_setting.hint": "List what it has with: GET /api/fleet/settings",
    "fleet_settings.bad_mode": "{mode} is not a way a server can follow the fleet's settings",
    "fleet_settings.bad_mode.hint": "Use 'managed' to follow them, or 'independent' to be left out.",
    # -- Which control asks something of a person, and how to stop it asking -----------------
    # (noust.core.compliance.explain decides which one; the words are here.)
    "controls.asked_by_setting": (
        "The control {control} asks for it, and it was turned on in Settings > Controls (or "
        "with the command line). It applies to everybody, whatever their role."
    ),
    "controls.asked_by_setting.hint": "To turn it off there, or:\n  {command}",
    "controls.asked_by_framework": (
        "The control {control} asks for it, and the framework in force ({frameworks}) asks for "
        "that control, so it stays on while the framework is on and cannot be turned off by "
        "itself."
    ),
    "controls.asked_by_framework.hint": (
        "To turn the framework off (Settings > Controls):\n  {command}"
    ),
    # -- A call that needs a second person's approval, or a reason ---------------------------
    "approvals.asked_waiting": (
        "This needs a second person's approval, whoever you are. Request {request} is waiting "
        "for an account with one of these roles to decide it: {approvers}."
    ),
    "approvals.asked_waiting.hint": _SEND_AGAIN,
    "approvals.asked_approved": (
        "This needs a second person's approval, whoever you are. Request {request} is "
        "approved: send the call again with it now."
    ),
    "approvals.asked_approved.hint": _SEND_AGAIN,
    "approvals.reason_required": (
        "Say why: this call needs a second person's approval, and a reason."
    ),
    "approvals.reason_required.hint": "Send the reason in the {header} header (percent-encoded).",
    "approvals.central_missing": "This needs a second person's approval on the central.",
    "approvals.central_missing.hint": (
        "Ask for it on the central and retry through it once it is approved."
    ),
}


def _names_in(template: str | None) -> tuple[str, ...] | None:
    """
    Args:
        template: A catalog entry, or None.

    Returns:
        The values the entry is said with, sorted; None when there is no entry.
    """
    if template is None:
        return None
    return tuple(sorted({name for _, name, _, _ in string.Formatter().parse(template) if name}))


def values_of(code: str) -> set[str]:
    """
    Args:
        code: A sentence's code.

    Returns:
        The values it is said with: those of its text and of its fix.
    """
    names: set[str] = set()
    for key in (code, code + HINT_SUFFIX):
        names |= set(_names_in(CATALOG.get(key)) or ())
    return names


def is_command(name: str) -> bool:
    """
    Args:
        name: The name of a value a sentence takes.

    Returns:
        Whether the value is a command to run: ``command``, or a name that ends in ``_command``.
        The console shows such a value as it is, in mono, and never translates or breaks it.
    """
    return name == "command" or name.endswith("_command")


def commands_of(code: str) -> tuple[str, ...]:
    """
    Args:
        code: A sentence's code.

    Returns:
        The values of the sentence and of its fix that are commands, sorted.
    """
    return tuple(sorted(name for name in values_of(code) if is_command(name)))


def typescript_codes() -> str:
    """
    Returns:
        The table of sentences as the TypeScript module the console is held to
        (``panel/src/api/serverTextCodes.gen.ts``), byte for byte what the generator writes: for
        each sentence the values its text takes, the values its fix takes (null when it has
        none) and which of them are commands. The console's English and Spanish catalogs must
        have an entry for each, with the same values.
    """
    import json

    lines = []
    for code in sorted(CATALOG):
        if code.endswith(HINT_SUFFIX):
            continue
        text = _names_in(CATALOG[code])
        hint = _names_in(CATALOG.get(code + HINT_SUFFIX))
        lines.append(
            f"  {json.dumps(code)}: {{ "
            f"text: {json.dumps(list(text or ()))}, "
            f"hint: {json.dumps(None if hint is None else list(hint))}, "
            f"commands: {json.dumps(list(commands_of(code)))} }},"
        )
    body = "\n".join(lines)
    return (
        "// Generated by scripts/generate_server_text.py from src/noust/core/server_text.py.\n"
        "// Do not edit: change the sentence there and run the script.\n"
        "\n"
        "export interface ServerTextEntry {\n"
        "  /** The values the sentence is said with. */\n"
        "  readonly text: readonly string[];\n"
        "  /** The values its fix is said with; null when the sentence has no fix. */\n"
        "  readonly hint: readonly string[] | null;\n"
        "  /** The values that are commands: shown as they are, in mono. */\n"
        "  readonly commands: readonly string[];\n"
        "}\n"
        "\n"
        "/** Every sentence the server says with a code, with the values each takes. */\n"
        f"export const SERVER_TEXT = {{\n{body}\n}} as const satisfies Record<string, ServerTextEntry>;\n"
        "\n"
        "export type ServerTextCode = keyof typeof SERVER_TEXT;\n"
    )


@dataclass(frozen=True)
class ServerText:
    """
    One sentence of the server's, as a code and the values to put in it.

    It cannot be made for a code the catalog does not have, nor with values other than the ones
    the sentence takes: a wrong sentence is found by the test that builds it, not by an
    operator reading a blank.

    Attributes:
        code: The catalog key (``tunnels.store_sealed``), which the console translates.
        params: Values for the placeholders; text, numbers and flags only.
    """

    code: str
    params: Mapping[str, Param] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in CATALOG:
            raise KeyError(f"{self.code}: there is no such sentence in noust.core.server_text")
        wanted, given = values_of(self.code), set(self.params)
        if wanted - given:
            raise ValueError(f"{self.code} needs {', '.join(sorted(wanted - given))}")
        if given - wanted:
            raise ValueError(f"{self.code} takes no {', '.join(sorted(given - wanted))}")
        for name, value in self.params.items():
            if not isinstance(value, str | int | float | bool):
                raise TypeError(f"{self.code}: {name} is {type(value).__name__}, not a plain value")

    @property
    def text(self) -> str:
        """The sentence in English."""
        return CATALOG[self.code].format(**self.params)

    @property
    def hint(self) -> str | None:
        """How to fix it in English, when the sentence has a fix."""
        template = CATALOG.get(self.code + HINT_SUFFIX)
        return template.format(**self.params) if template is not None else None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The code and the values, which is all the console needs next to the text.
        """
        return {"code": self.code, "params": dict(self.params)}


def say(code: str, **params: Param) -> ServerText:
    """
    Make a sentence.

    Args:
        code: Its catalog key.
        **params: The values its text and its fix are said with.

    Returns:
        The sentence.

    Raises:
        KeyError: When the catalog has no such code.
        ValueError: When a value is missing or one is not taken.
        TypeError: When a value is not text, a number or a flag.
    """
    return ServerText(code, params)
