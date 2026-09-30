# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who did something: the one shape every audit event names its actor in.

ENS op.exp.8.1 asks for the identifier of the user behind every event, and
art. 24.3 for the person, not the credential. Before 3.1 an event carried a
free-form label (``master``, ``token:ci``, twelve characters of a session id,
``cli:root``); :class:`Actor` keeps that label as its :attr:`~Actor.label`,
so the console's Activity filter keeps working, and adds what the label
cannot say: which kind of principal it is, its role, the channel it came
through and where from.

:func:`cli_actor` is the identity of whoever runs a command at this terminal.
``/proc/self/loginuid`` comes first because the kernel keeps it through
``sudo`` and ``su``: the operator who typed ``sudo noust app delete`` is on
record, not ``root``.
"""

from __future__ import annotations

import getpass
import os
import pwd
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

#: The kinds of principal an event can name. ``anonymous`` is a caller that
#: has not proven who it is yet: a refused sign-in, a refused handshake.
ActorKind = Literal["user", "token", "master", "fleet", "cli", "system", "anonymous"]

ACTOR_KINDS: frozenset[str] = frozenset(
    {"user", "token", "master", "fleet", "cli", "system", "anonymous"}
)

#: Where the kernel keeps the login user of this process.
LOGINUID_PATH = Path("/proc/self/loginuid")

#: ``(uid_t) -1``: no login user was ever assigned (a daemon, a container).
UNSET_LOGINUID = 4294967295


@dataclass(frozen=True)
class Actor:
    """
    The principal behind an audit event.

    Attributes:
        kind: What sort of principal it is.
        id: A stable identifier: an account id, a token name, a uid, a
            session prefix.
        name: The human-readable name: a login, a token's name.
        role: The role it acted with, when there are roles.
        via: The channel: ``web``, ``cli``, ``sudo``, ``webhook``, or the fleet
            token a central acted through.
        source: Where from: a client IP, a tty, an SSH client address.
    """

    kind: ActorKind
    id: str | None = None
    name: str | None = None
    role: str | None = None
    via: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        # The console's principals call a person an "account"; the trail
        # has one word for it.
        if self.kind == "account":
            object.__setattr__(self, "kind", "user")

    @property
    def label(self) -> str:
        """
        The short label the audit log and the Activity page have always shown.

        Returns:
            ``master``, ``token:<name>``, ``cli:<login>``, ``anonymous``,
            ``<fleet token> on behalf of <operator>``, ``system:<name>``, or an
            account's name.
        """
        if self.kind == "master":
            return "master"
        if self.kind == "anonymous":
            return "anonymous"
        if self.kind == "token":
            name = self.name or self.id or "unknown"
            return name if name.startswith("token:") else f"token:{name}"
        if self.kind == "cli":
            return f"cli:{self.name or self.id or 'unknown'}"
        if self.kind == "system":
            return f"system:{self.name}" if self.name else "system"
        if self.kind == "fleet":
            # The console spells the channel "fleet:<token name>"; the label
            # has always named the token alone.
            via = self.via.removeprefix("fleet:") if self.via else None
            if via and self.name:
                return f"{via} on behalf of {self.name}"
            return self.name or via or "fleet"
        return self.name or self.id or "user"

    def to_dict(self) -> dict[str, str]:
        """
        The actor as the audit line stores it.

        Returns:
            Every field that is set.
        """
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Actor:
        """
        Rebuild an actor from :meth:`to_dict`.

        Args:
            raw: A stored actor.

        Returns:
            The actor; an unknown kind reads as ``system`` rather than failing
            the read of an old line.
        """
        kind = str(raw.get("kind", "system"))
        return cls(
            kind=kind if kind in ACTOR_KINDS else "system",  # type: ignore[arg-type]
            id=_optional(raw.get("id")),
            name=_optional(raw.get("name")),
            role=_optional(raw.get("role")),
            via=_optional(raw.get("via")),
            source=_optional(raw.get("source")),
        )

    @classmethod
    def anonymous(cls, source: str | None = None) -> Actor:
        """
        A caller that has not proven who it is.

        Args:
            source: Its address.

        Returns:
            The actor.
        """
        return cls(kind="anonymous", source=source)

    @classmethod
    def system(cls, name: str | None = None) -> Actor:
        """
        Noust acting on its own: a timer, the monitor, a background job.

        Args:
            name: Which part of Noust.

        Returns:
            The actor.
        """
        return cls(kind="system", name=name)

    @classmethod
    def from_label(cls, label: str | None, source: str | None = None) -> Actor:
        """
        Read a label written before :class:`Actor` existed.

        Args:
            label: ``master``, ``token:<name>``, ``cli:<login>``, a session
                prefix, ``<token> on behalf of <operator>`` or ``anonymous``.
            source: The address the event came from.

        Returns:
            The actor the label names, whose :attr:`label` gives the same
            text back.
        """
        text = (label or "").strip()
        if not text or text in ("anonymous", "unknown"):
            return cls(kind="anonymous", source=source)
        if text == "master":
            return cls(kind="master", id="master", source=source)
        if " on behalf of " in text:
            via, _, behalf = text.partition(" on behalf of ")
            return cls(kind="fleet", id=via, name=behalf, via=via, source=source)
        prefix, colon, rest = text.partition(":")
        if colon and prefix == "token":
            return cls(kind="token", id=rest, name=rest, source=source)
        if colon and prefix == "cli":
            return cls(kind="cli", id=rest, name=rest, via="cli", source=source)
        if colon and prefix == "system":
            return cls(kind="system", name=rest, source=source)
        if text.startswith("fleet-"):
            return cls(kind="fleet", id=text, name=text, source=source)
        return cls(kind="user", id=text, name=text, source=source)


def _optional(value: Any) -> str | None:
    return None if value is None else str(value)


def _login_uid() -> int | None:
    """
    Read the kernel's login uid for this process.

    Returns:
        The uid, or None when the kernel has none (unset, or no ``/proc``).
    """
    try:
        text = LOGINUID_PATH.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if not text.isdigit() or int(text) == UNSET_LOGINUID:
        return None
    return int(text)


def _user_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _tty() -> str | None:
    for descriptor in (0, 1, 2):
        try:
            if os.isatty(descriptor):
                return os.ttyname(descriptor)
        except OSError:
            continue
    return None


def cli_actor(environ: dict[str, str] | None = None) -> Actor:
    """
    The operating system identity of whoever runs this process.

    Order: the kernel's login uid (kept through ``sudo`` and ``su``), then
    ``SUDO_USER``, then the process's own user. A process systemd started
    with no login user (a timer, the console's unit) is ``system``.

    Args:
        environ: The environment to read; the process's own by default.

    Returns:
        The actor, with the channel (``cli`` or ``sudo``) in ``via`` and the
        SSH client address or the terminal in ``source``.
    """
    env = dict(os.environ) if environ is None else environ
    login_uid = _login_uid()
    sudo_user = env.get("SUDO_USER") or None
    via = "sudo" if sudo_user else "cli"
    ssh = env.get("SSH_CONNECTION", "").split()
    source = ssh[0] if ssh else _tty()

    if login_uid is not None:
        return Actor(
            kind="cli", id=str(login_uid), name=_user_name(login_uid), via=via, source=source
        )
    if sudo_user:
        uid = env.get("SUDO_UID")
        return Actor(kind="cli", id=uid, name=sudo_user, via=via, source=source)
    if env.get("INVOCATION_ID") and source is None:
        return Actor(kind="system", id=env["INVOCATION_ID"][:12], name="systemd", via="systemd")
    try:
        name = getpass.getuser()
    except (KeyError, OSError):
        name = str(os.getuid())
    return Actor(kind="cli", id=str(os.getuid()), name=name, via=via, source=source)
