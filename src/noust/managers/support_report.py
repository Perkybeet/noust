# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The support report: what a maintainer would otherwise ask for, one question at a time.

``noust support report`` and ``GET /api/system/support-report`` build the same
document: the version and how Noust was installed, the machine and where it stands
against the platform table, the health report, every application's state and the
diagnosis of the ones that need attention, the Noust units and the last lines of
the console's and the monitor's journal. It is meant to be pasted into an issue, so
**nothing leaves it unmasked**:

1. every secret Noust knows (its own configuration, and each listed application's
   ``.env``) is replaced by :class:`~noust.core.redact.Scrubber`;
2. what only looks like one - a token, a key block, an ``Authorization`` header, a
   connection string's password, a ``name=value`` whose name says it is secret - is
   replaced by :func:`~noust.core.secret_detection.redact_secret_shapes`, which does
   not need to know the secret (it is the last line of defence for a journal line
   another program wrote);
3. with ``hide_domains`` every domain, application name and the host's name become
   ``app-1``, ``host`` and so on, consistently through the whole document.

A section that cannot be read says so and why instead of vanishing (rule: an
unexplained gap is how "the report was fine" turns into a second round of questions).
Every probe only reads; nothing here changes the machine.
"""

from __future__ import annotations

import logging
import platform
import re
import socket
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust import __version__
from noust.core import redact
from noust.core.app_state import AppState, resolve_states
from noust.core.exceptions import NoustError
from noust.core.runner import CommandRunner, get_runner
from noust.core.secret_detection import redact_secret_shapes
from noust.core.store import App, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.managers.diagnose import Diagnosis, diagnose
from noust.managers.health import HealthReport, collect_health_report
from noust.managers.self_update import SelfUpdate
from noust.managers.server import platforms
from noust.managers.service_manager import ServiceManager

logger = logging.getLogger(__name__)

#: Lines of each unit's journal in the report.
DEFAULT_JOURNAL_LINES = 60

#: How many applications that need attention are diagnosed (each probe costs requests).
DEFAULT_DIAGNOSE_LIMIT = 3

#: The most lines of one check's evidence kept; the rest is in ``noust diagnose``.
EVIDENCE_LINES = 12

#: The units whose journal is read: the console and the monitor.
JOURNAL_UNITS = ("noust-web.service", "noust-monitor.service")

#: Errors a collector may end in and still leave the others standing: a store or a file
#: that cannot be read, a probe that failed with Noust's own error.
COLLECT_ERRORS: tuple[type[Exception], ...] = (NoustError, OSError, sqlite3.Error, ValueError)


@dataclass(frozen=True)
class ReportSection:
    """
    One part of the report.

    Attributes:
        key: A stable identifier (``noust``, ``machine``, ``health``, ``applications``,
            ``units``, ``diagnosis``, ``journal``).
        title: The heading.
        body: The text, already masked.
        collected: False when it could not be read; ``body`` then says why.
    """

    key: str
    title: str
    body: str
    collected: bool = True


@dataclass(frozen=True)
class SupportReport:
    """
    The whole report.

    Attributes:
        version: The Noust version that made it.
        generated_at: When, ISO 8601 in UTC.
        hide_domains: Whether domains and the host's name were replaced.
        sections: The parts, in reading order.
    """

    version: str
    generated_at: str
    hide_domains: bool
    sections: tuple[ReportSection, ...] = field(default_factory=tuple)

    def to_markdown(self) -> str:
        """
        Render the report for an issue.

        Returns:
            Markdown: a heading, a line saying what was masked, and each section with its
            output fenced so the issue keeps it verbatim.
        """
        lines = [
            "# Noust support report",
            "",
            f"Made by Noust {self.version} on {self.generated_at}. "
            "Secrets Noust knows, and anything shaped like one, are masked"
            + (
                "; domains, application names and the host's name are replaced (`app-1`, `host`)."
                if self.hide_domains
                else ". Domains and the host's name are included: run it with "
                "`--hide-domains` to replace them."
            ),
            "Read it before you post it.",
            "",
        ]
        for section in self.sections:
            lines += [f"## {section.title}", ""]
            lines += _fenced(section.body)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the report as JSON-ready data.

        Returns:
            ``version``, ``generated_at``, ``hide_domains``, ``markdown`` (what
            :meth:`to_markdown` renders) and ``sections`` (key, title, body, collected).
        """
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "hide_domains": self.hide_domains,
            "markdown": self.to_markdown(),
            "sections": [
                {
                    "key": s.key,
                    "title": s.title,
                    "body": s.body,
                    "collected": s.collected,
                }
                for s in self.sections
            ],
        }


def _fenced(body: str) -> list[str]:
    """
    Fence a block of output so Markdown does not interpret it.

    Args:
        body: The text.

    Returns:
        The lines, fenced with more backticks than any run in the text.
    """
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence, *body.rstrip("\n").splitlines(), fence]


class SupportReporter:
    """
    Collect the support report.

    Args:
        runner: Runs the probes (systemctl, journalctl); the process-wide one by default.
        store: The store the applications are read from; the process-wide one by default.
        journal_lines: Lines of each unit's journal.
        journal_left_out: Why the journal is not read, or None to read it. A caller without
            the right to read it leaves it out and the report says why.
        hide_domains: Replace domains, application names and the host's name.
        diagnose_limit: How many applications that need attention are diagnosed.
        health: Produces the health report (replaced in tests).
        states: Resolves what each application is doing (replaced in tests; the real one
            probes ports).
        diagnosis: Diagnoses one domain (replaced in tests).
        now: The clock (replaced in tests).
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        store: NoustStore | None = None,
        journal_lines: int = DEFAULT_JOURNAL_LINES,
        journal_left_out: str | None = None,
        hide_domains: bool = False,
        diagnose_limit: int = DEFAULT_DIAGNOSE_LIMIT,
        health: Callable[[], HealthReport] | None = None,
        states: Callable[[list[App]], dict[str, AppState]] | None = None,
        diagnosis: Callable[[str], Diagnosis] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._runner = runner
        self._store = store
        self.journal_lines = journal_lines
        self.journal_left_out = journal_left_out
        self.hide_domains = hide_domains
        self.diagnose_limit = diagnose_limit
        self._health = health or collect_health_report
        self._resolve = states or self._resolve_states
        self._diagnosis = diagnosis or diagnose
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def store(self) -> NoustStore:
        """The store in force right now."""
        return self._store or get_store()

    # ----------------------------------------------------------------- build

    def build(self) -> SupportReport:
        """
        Collect every section, mask the result and return it.

        Returns:
            The report. A section that could not be read is present and says why.
        """
        apps = self._apps()
        states = self._states(apps)
        sections = [
            self._section("noust", "Noust", self._noust),
            self._section("machine", "Machine", self._machine),
            self._section("health", "Health", self._health_text),
            self._section("applications", "Applications", lambda: self._applications(apps, states)),
            self._section("units", "Units", self._units),
            self._section(
                "diagnosis", "Applications that need attention", lambda: self._diagnoses(states)
            ),
            self._journal_section(),
        ]
        mask = self._masker(apps)
        return SupportReport(
            version=__version__,
            generated_at=self._now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            hide_domains=self.hide_domains,
            sections=tuple(
                ReportSection(s.key, s.title, mask(s.body), s.collected) for s in sections
            ),
        )

    def _section(self, key: str, title: str, collect: Callable[[], str]) -> ReportSection:
        """
        Run one collector, turning a failure into a section that says why.

        Args:
            key: The section's key.
            title: Its heading.
            collect: Produces the body.

        Returns:
            The section.
        """
        try:
            return ReportSection(key, title, collect())
        except COLLECT_ERRORS as exc:
            logger.warning("Support report: %s could not be collected: %s", key, exc)
            # A NoustError's text already carries its details.
            return ReportSection(key, title, f"(not collected: {exc})", collected=False)

    def _masker(self, apps: list[App]) -> Callable[[str], str]:
        """
        Build the one function every section's text goes through before it leaves.

        Args:
            apps: The applications, whose secrets and names are masked.

        Returns:
            A function from text to masked text.
        """
        scrubber = redact.Scrubber(redact.known_credentials())
        for app in apps:
            scrubber.add(redact.app_secret_values(app.domain))
        aliases = self._aliases(apps) if self.hide_domains else []

        def mask(text: str) -> str:
            text = redact_secret_shapes(scrubber.scrub(text))
            for real, alias in aliases:
                text = text.replace(real, alias)
            return text

        return mask

    def _aliases(self, apps: list[App]) -> list[tuple[str, str]]:
        """
        Name what ``hide_domains`` replaces and with what, longest first.

        Args:
            apps: The applications.

        Returns:
            ``(real, alias)`` pairs: each domain and its unit-style name become ``app-N``,
            the host's name becomes ``host``. Longest first, so a domain is replaced whole
            before a name inside it.
        """
        pairs: list[tuple[str, str]] = []
        for number, app in enumerate(sorted(apps, key=lambda a: a.domain), start=1):
            alias = f"app-{number}"
            pairs += [(app.domain, alias), (domain_to_app_name(app.domain), alias)]
        host = socket.gethostname()
        if host:
            pairs.append((host, "host"))
        return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)

    # ------------------------------------------------------------- collectors

    def _apps(self) -> list[App]:
        """
        Read the applications, or none when the store cannot be read.

        Returns:
            The applications.
        """
        try:
            return self.store.list_apps()
        except COLLECT_ERRORS as exc:
            logger.warning("Support report: the applications could not be read: %s", exc)
            return []

    def _states(self, apps: list[App]) -> dict[str, AppState]:
        """
        Resolve what each application is doing, as ``noust list`` does.

        Args:
            apps: The applications.

        Returns:
            Domain to state; empty when systemd cannot be asked.
        """
        try:
            return self._resolve(apps)
        except COLLECT_ERRORS as exc:
            logger.warning("Support report: the states could not be resolved: %s", exc)
            return {}

    def _resolve_states(self, apps: list[App]) -> dict[str, AppState]:
        """
        Args:
            apps: The applications.

        Returns:
            Their states, resolved the way ``noust list`` resolves them.
        """
        return resolve_states(apps, ServiceManager(runner=self.runner, verbose=False))

    def _noust(self) -> str:
        """Returns: The version, how it was installed and the interpreter."""
        method = SelfUpdate(runner=self.runner).method()
        return "\n".join(
            [
                f"Noust:        {__version__}",
                f"Installed by: {method}",
                f"Python:       {platform.python_version()} ({sys.executable})",
            ]
        )

    def _machine(self) -> str:
        """Returns: The operating system, where it stands against the platform table, and memory."""
        verdict = platforms.assess_this_machine(platform.machine())
        lines = [
            f"System:   {verdict.pretty_name}, {verdict.arch}",
            f"Platform: {verdict.status}",
            f"Kernel:   {platform.release()}",
        ]
        lines += [f"  {reason}" for reason in verdict.reasons]
        result = self.runner.run(["systemctl", "--version"], timeout=10)
        if result.success and result.stdout.strip():
            lines.append("systemd:  " + result.stdout.splitlines()[0])
        return "\n".join(lines)

    def _health_text(self) -> str:
        """Returns: Each health check, then the issues and warnings that decide the verdict."""
        report = self._health()
        lines = [f"Verdict: {report.verdict}"]
        lines += [f"  [{c.status}] {c.name}: {c.value}" for c in report.checks]
        lines += [f"issue: {issue}" for issue in report.issues]
        lines += [f"warning: {warning}" for warning in report.warnings]
        return "\n".join(lines)

    def _applications(self, apps: list[App], states: dict[str, AppState]) -> str:
        """
        Args:
            apps: The applications.
            states: What each is doing.

        Returns:
            One line per application: its domain, type, layout and state.
        """
        if not apps:
            return "No applications deployed."
        lines = []
        for app in sorted(apps, key=lambda a: a.domain):
            state = states.get(app.domain)
            label = state.label if state is not None else "unknown"
            detail = f" - {state.detail}" if state is not None and state.detail else ""
            lines.append(f"{app.domain}  type={app.app_type}  layout={app.layout}  {label}{detail}")
        return "\n".join(lines)

    def _units(self) -> str:
        """Returns: Noust's own units and every failed unit, as systemd reports them."""
        listed = self.runner.run(
            [
                "systemctl",
                "list-units",
                "--all",
                "--no-legend",
                "--plain",
                "--no-pager",
                "noust-*",
                "wasm-*",
            ],
            timeout=30,
        )
        failed = self.runner.run(
            ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"], timeout=30
        )
        lines = ["Noust units:"]
        lines += [f"  {line}" for line in listed.stdout.splitlines()] or ["  (none listed)"]
        lines.append("Failed units:")
        lines += [f"  {line}" for line in failed.stdout.splitlines()] or ["  (none)"]
        return "\n".join(lines)

    def _diagnoses(self, states: dict[str, AppState]) -> str:
        """
        Args:
            states: What each application is doing.

        Returns:
            The diagnosis of the first applications that need attention: verdict, the probable
            cause and every check that is not fine, with a few lines of its evidence.
        """
        troubled = sorted(domain for domain, state in states.items() if not state.healthy)
        if not troubled:
            return "Every application is serving."
        lines: list[str] = []
        for domain in troubled[: self.diagnose_limit]:
            result = self._diagnosis(domain)
            lines.append(f"{domain}: {result.verdict}")
            if result.probable_cause:
                lines.append(f"  most likely: {result.probable_cause}")
            for check in result.checks:
                if check.status in ("warn", "fail"):
                    lines.append(f"  [{check.status}] {check.name}: {check.summary}")
                    lines += [
                        f"      {row}" for row in check.evidence.splitlines()[:EVIDENCE_LINES]
                    ]
        if len(troubled) > self.diagnose_limit:
            lines.append(
                f"({len(troubled) - self.diagnose_limit} more need attention: noust diagnose DOMAIN)"
            )
        return "\n".join(lines)

    def _journal_section(self) -> ReportSection:
        """Returns: The journal of the console and the monitor, or why it is not there."""
        if self.journal_left_out is not None:
            return ReportSection(
                "journal",
                "Journal of noust-web and noust-monitor",
                f"(left out: {self.journal_left_out})",
                collected=False,
            )
        return self._section("journal", "Journal of noust-web and noust-monitor", self._journal)

    def _journal(self) -> str:
        """Returns: The last lines of the journal of the console and the monitor."""
        argv = ["journalctl"]
        for unit in JOURNAL_UNITS:
            argv += ["--unit", unit]
        argv += ["--lines", str(int(self.journal_lines)), "--no-pager", "--output", "short-iso"]
        result = self.runner.run(argv, timeout=30)
        if not result.success:
            return f"(journalctl failed: {(result.stderr or result.stdout).strip()})"
        return result.stdout.rstrip("\n") or "(the journal has no lines for these units)"
