# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one shape every notification has, whatever channel it leaves by.

Before 3.1 a notification was ``title + "\\n" + body``: the emitters decided
what to say and, by concatenating, how it looked, and every channel received
the same text in a different envelope. Now an emitter describes *facts* and a
renderer (one per channel, :mod:`noust.core.notifications.render`) lays them
out. Nothing here knows about Telegram or Slack.

A :class:`Notification` says, in this order: how it went (:attr:`state`, told
three ways - colour where a channel has one, a glyph, a word), what it is
about (:attr:`subject`), one sentence about it (:attr:`summary`), the facts
worth a glance (:attr:`facts`, the server first), the one command to run next
(:attr:`command`), the system's own words as a short verbatim
:class:`Excerpt`, and where to look (:attr:`links`).

Everything Noust says is translated (the composers build it from
:mod:`noust.core.messages`); everything another program said - a journal
line, a tool's stderr, a branch name - travels in :class:`Excerpt` lines and
:class:`Fact` values, verbatim. The line between the two is the structure, not
the text.

Text that someone else chose is normalised on the way in (:func:`clean_line`,
:func:`clean_inline`): terminal escapes, control characters and
bidirectional overrides are removed here, once, so no renderer has to
remember to.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from noust.core.messages import Locale

#: Event kinds an operator can switch off under ``notifications.events``.
#: ``DEFAULT_CONFIG["notifications"]["events"]`` spells out the same names;
#: config.py cannot import this package (this package reads its settings from
#: config.py), so the agreement is pinned by a test in tests/test_notifier.py.
#:
#: A kind is a *switch*; what actually happened is the finer
#: :attr:`Notification.code`. ``deploy_success`` carries ``deploy.succeeded``,
#: ``update.succeeded``, ``rollback.succeeded``, ``activate.succeeded`` and
#: ``migrate.succeeded``: an operator who mutes deploys mutes all of them,
#: and a consumer of the webhook can still tell them apart.
EVENT_KINDS: tuple[str, ...] = (
    "deploy_started",
    "deploy_success",
    "deploy_failed",
    "deploy_rolled_back",
    "deploy_hook_failed",
    "restore_success",
    "restore_failed",
    "cert_expiring",
    "unit_failed",
    "disk_threshold",
    "backup_failed",
    "backup_success",
    "node_unreachable",
    "node_recovered",
    "node_host_key_changed",
    "server_rebooted",
    "server_back",
    "approval_requested",
    "approval_decided",
)

#: Kinds that ship switched off: ``deploy_started`` fires once per attempt with
#: no outcome to report, and ``backup_success`` is a daily heartbeat only an
#: operator who wants one should receive.
OFF_BY_DEFAULT: tuple[str, ...] = ("deploy_started", "backup_success")

#: The kind the settings page's "send a test" button sends. Always accepted and
#: never filtered, so the button works before anything is enabled.
TEST_KIND = "test"

#: The kind of the monitor's process-observation report. It travels by email
#: only and has its own switch (``monitor.notify``), so it is not in
#: :data:`EVENT_KINDS`.
REPORT_KIND = "report"

#: Every kind a :class:`Notification` may carry.
ALL_KINDS: tuple[str, ...] = (*EVENT_KINDS, TEST_KIND, REPORT_KIND)


class State(str, Enum):
    """
    How it went, in the words the console uses for state.

    ``(str, Enum)`` rather than ``StrEnum``: the project supports Python 3.10.
    """

    OK = "ok"
    PROGRESS = "progress"
    WARNING = "warning"
    FAILED = "failed"
    INFO = "info"


#: The state's glyph. Geometric text characters, none of which has the Unicode
#: Emoji property: ``✔ ✖ ⚠ ❌ ✅`` render as coloured pictures in most clients
#: and would say "success" or "error" in a colour Noust did not choose. The
#: shapes are the console's: a dot, an arc, a triangle, a cross, a ring.
STATE_GLYPHS: dict[State, str] = {
    State.OK: "●",
    State.PROGRESS: "◐",
    State.WARNING: "▲",
    State.FAILED: "✕",
    State.INFO: "○",
}

#: Which states make a sound. What went right, what is in progress and what is
#: only information arrive silently; a warning or a failure is the one thing a
#: phone should ring for.
LOUD_STATES: frozenset[State] = frozenset({State.WARNING, State.FAILED})


@dataclass(frozen=True)
class StateColors:
    """
    A state's colours, the console's own tokens.

    Attributes:
        light: Text colour on a light ground.
        dark: Text colour on a dark ground, and the bar or strip colour every
            channel that paints one uses.
        strip: The colour a channel's side strip takes (Slack, Discord).
    """

    light: str
    dark: str
    strip: str


#: ``panel/src/styles/tokens.css``: running, deploying, warning, failed and
#: stopped. Colour only ever encodes state.
STATE_COLORS: dict[State, StateColors] = {
    State.OK: StateColors("#16784a", "#4dc47e", "#4dc47e"),
    State.PROGRESS: StateColors("#8c5800", "#e3a73c", "#e3a73c"),
    State.WARNING: StateColors("#8c5800", "#e3a73c", "#e3a73c"),
    State.FAILED: StateColors("#c12c24", "#ff736a", "#cf3a30"),
    State.INFO: StateColors("#636363", "#9e9e9e", "#9e9e9e"),
}

# ESC sequences a terminal understands: CSI (colours, cursor), OSC (window
# titles, hyperlinks) and the two-character forms. A journal line or a build
# tool's output carries them whenever the tool believed it had a terminal.
_ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC
    r"|\x1b[@-Z\\-_]"  # two-character
)
# C0 (bar tab), DEL and C1. Tab is turned into spaces by the callers.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Bidirectional embeddings, overrides and isolates, the marks and the Arabic
# letter mark: text that reads backwards, or makes what follows read backwards,
# in a client that honours it. ``gnp.exe`` written as ``exe.png``.
_BIDI = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
# Zero-width characters the renderers add themselves to defuse mentions and
# markup. Removing them on the way in makes that defusing the renderer's alone.
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def clean_line(text: str) -> str:
    """
    Clean one line of somebody else's text, keeping its indentation.

    Args:
        text: A line of output, a name, a message.

    Returns:
        The line without terminal escapes, control characters or
        bidirectional overrides, tabs as four spaces, and without trailing
        space. Newlines are the caller's to split on first.
    """
    text = _ANSI.sub("", text)
    text = text.replace("\t", "    ").replace("\r", "")
    text = _CONTROL.sub("", text)
    text = _BIDI.sub("", text)
    return _ZERO_WIDTH.sub("", text).rstrip()


def clean_inline(text: str) -> str:
    """
    Clean a value that must be one line: a fact, a subject, a title.

    Args:
        text: The value.

    Returns:
        :func:`clean_line`'s result with every run of whitespace, newlines
        included, as one space, trimmed.
    """
    text = " ".join(_ANSI.sub("", text).split())
    text = _CONTROL.sub("", text)
    text = _BIDI.sub("", text)
    text = _ZERO_WIDTH.sub("", text)
    return " ".join(text.split())


@dataclass(frozen=True)
class Fact:
    """
    One labelled value: who, what, how long.

    Attributes:
        key: Stable machine name (``commit``, ``duration``, ``server``). The
            webhook publishes it; consumers key on it, never on the label.
        label: The label, translated.
        value: The value. Verbatim when it came from somewhere else.
        mono: Set it in monospace: an identifier, a command, a path.
    """

    key: str
    label: str
    value: str
    mono: bool = False

    def __post_init__(self) -> None:
        """Clean what someone else may have chosen."""
        object.__setattr__(self, "label", clean_inline(self.label))
        object.__setattr__(self, "value", clean_inline(self.value))


@dataclass(frozen=True)
class Link:
    """
    A place to go.

    Attributes:
        rel: What it is (``console``).
        label: Its text, translated.
        url: An absolute http(s) URL.
    """

    rel: str
    label: str
    url: str


@dataclass(frozen=True)
class Excerpt:
    """
    A short piece of another program's output, whole lines and verbatim.

    Attributes:
        label: What it is (``Last lines of the journal of shop``), translated.
        lines: The lines, oldest first.
        omitted: How many earlier lines were left out, for the marker every
            renderer draws in the reader's language.
        pinned: How many of the first lines stay whatever a channel's budget
            is, because they carry the output's first error, brought up from
            above the lines that follow it (the failing step's own message,
            when there is one, counts among them). Zero when nothing was
            brought up; the lines after the pinned ones are the end of the
            output, as it came. Counted in :attr:`lines` as given, so each
            entry must be one line.
    """

    label: str
    lines: tuple[str, ...]
    omitted: int = 0
    pinned: int = 0

    def __post_init__(self) -> None:
        """Split multi-line entries and clean every line."""
        pieces: list[str] = []
        for line in self.lines:
            pieces.extend(clean_line(part) for part in line.replace("\r\n", "\n").split("\n"))
        object.__setattr__(self, "label", clean_inline(self.label))
        object.__setattr__(self, "lines", tuple(pieces))


@dataclass(frozen=True)
class Section:
    """
    A titled block of facts: one entry of a report.

    The monitor's process-observation report is a list of them (one per
    process); no other notification has any.

    Attributes:
        heading: The block's title.
        rows: Its facts.
        state: How serious it is, when that varies between blocks.
    """

    heading: str
    rows: tuple[Fact, ...] = ()
    state: State | None = None

    def __post_init__(self) -> None:
        """Clean the heading."""
        object.__setattr__(self, "heading", clean_inline(self.heading))


@dataclass(frozen=True)
class Notification:
    """
    One thing worth telling the operator, structured.

    Attributes:
        kind: The switch it is filtered by, one of :data:`EVENT_KINDS`, or
            :data:`TEST_KIND` or :data:`REPORT_KIND`.
        code: What actually happened, stable and fine-grained
            (``deploy.rolled_back``, ``cert.expired``). The webhook publishes
            it; a consumer must ignore a code it does not know.
        state: How it went.
        locale: The language of every word Noust wrote here.
        title: The state as a phrase (``Rolled back``), translated. Never
            carries the subject or a fact.
        subject: What it is about: an application, a unit, a mount point.
        summary: One sentence, translated, that repeats neither the title nor
            a fact.
        server: The name of the server that raised it.
        facts: The labelled values, the server first.
        command: The one command to run next.
        excerpt: The system's own words, short and verbatim.
        links: Where to look; the console first.
        sections: Blocks of facts, for a report.
        domain: The application's domain when it is about one (the webhook's
            legacy ``domain`` key).
        ts: When it happened, UTC.
        id: One per delivery: the webhook's ``id`` and the email's
            ``Message-ID``.
    """

    kind: str
    code: str
    state: State
    locale: Locale
    title: str
    subject: str
    summary: str
    server: str
    facts: tuple[Fact, ...] = ()
    command: Fact | None = None
    excerpt: Excerpt | None = None
    links: tuple[Link, ...] = ()
    sections: tuple[Section, ...] = ()
    domain: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        """
        Refuse a kind nothing can filter, and clean the text fields.

        A typo'd kind would default to "send" and silently bypass the
        operator's ``notifications.events`` switches.

        Raises:
            ValueError: When ``kind`` is not a known kind.
        """
        if self.kind not in ALL_KINDS:
            known = ", ".join(ALL_KINDS)
            raise ValueError(f"Unknown notification kind {self.kind!r}; expected one of: {known}")
        for name in ("title", "subject", "summary", "server"):
            object.__setattr__(self, name, clean_inline(getattr(self, name)))

    @property
    def headline(self) -> str:
        """The state and what it is about: ``Rolled back · shop.example.com``."""
        return f"{self.title} · {self.subject}" if self.subject else self.title

    @property
    def glyph(self) -> str:
        """The state's glyph."""
        return STATE_GLYPHS[self.state]

    @property
    def loud(self) -> bool:
        """Whether a phone should ring for it."""
        return self.state in LOUD_STATES

    @property
    def console_link(self) -> Link | None:
        """The link to the console, when there is one."""
        return next((link for link in self.links if link.rel == "console"), None)
