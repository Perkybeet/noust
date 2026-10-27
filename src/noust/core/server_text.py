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
certbot's), a command to run, a path, a version a program printed, a name somebody typed. They
are values, sent as parameters or as the text beside the code, and the console shows them as
they are (``docs/api.md``, "Sentences with a code").
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
}


def values_of(code: str) -> set[str]:
    """
    Args:
        code: A sentence's code.

    Returns:
        The values it is said with: those of its text and of its fix.
    """
    names: set[str] = set()
    for key in (code, code + HINT_SUFFIX):
        template = CATALOG.get(key)
        if template is not None:
            names |= {name for _, name, _, _ in string.Formatter().parse(template) if name}
    return names


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
