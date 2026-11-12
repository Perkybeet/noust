# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether the renewal of certificates is working (item 172).

certbot renews certificates on a timer of its own, and Noust leaves the work to it. What Noust
looks at is the result, not only how many days are left: a renewal that fails (port 80 closed, a
rule that refuses the validation, a DNS record that moved) is silent until the certificate is
close to its end, and with 45 days to live there is far less time to notice than with 90.

Everything here only reads: ``systemctl show`` and ``journalctl`` (both declared read-only probes
of :mod:`noust.core.runner`) for a systemd timer, and certbot's own log for the cron entry
:meth:`~noust.managers.cert_manager.CertManager.setup_auto_renewal` falls back to. A renewal that
failed carries certbot's words verbatim, never a paraphrase.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from noust.core.runner import CommandRunner

#: ``certbot.timer`` on Debian and Ubuntu, ``certbot-renew.timer`` on the RHEL family's EPEL
#: package and ``snap.certbot.renew.timer`` on the snap, each with the service it starts.
TIMER_UNITS: tuple[tuple[str, str], ...] = (
    ("certbot.timer", "certbot.service"),
    ("certbot-renew.timer", "certbot-renew.service"),
    ("snap.certbot.renew.timer", "snap.certbot.renew.service"),
)

#: What certbot logs and the cron entry it falls back to; the same files
#: :mod:`noust.managers.cert_manager` writes and reads.
LETSENCRYPT_LOG = Path("/var/log/letsencrypt/letsencrypt.log")
CRON_FILE = Path("/etc/cron.d/certbot-renew")

#: How much of what certbot said a notification and a report carry.
OUTPUT_LIMIT = 4000

#: Lines of the service's journal fetched for a failed run.
_JOURNAL_LINES = "40"

#: How far from the end of certbot's log a cron renewal is read.
_LOG_TAIL_BYTES = 65536

#: What certbot's log says when a renewal worked, and when it did not.
_LOG_OK = ("all renewals succeeded", "no renewals were attempted", "not due for renewal yet")
_LOG_FAILED = ("failed to renew certificate", "renewal failure(s)", "could not be renewed")

#: Problems a status can carry.
FAILED = "failed"
TIMER_STOPPED = "timer_stopped"


@dataclass(frozen=True)
class RenewalStatus:
    """
    What can be read of how certificates are renewed.

    Attributes:
        mechanism: ``timer`` (a systemd timer), ``cron`` (the entry Noust writes where there is
            none) or ``none`` (neither found, or systemd cannot be asked: the operator may renew
            some other way, which is not a failure).
        unit: The timer's name, for ``timer``.
        timer_active: Whether the timer is running; None when there is no timer.
        last_run: When the renewal last ran, as systemd or certbot's log wrote it; None when it
            never did or it cannot be told.
        last_ok: Whether that run worked; None when it is not known.
        next_run: When the timer fires next, as systemd wrote it.
        output: certbot's own words of a failed run, verbatim and cut to :data:`OUTPUT_LIMIT`;
            empty when the last run did not fail.
    """

    mechanism: str = "none"
    unit: str = ""
    timer_active: bool | None = None
    last_run: str | None = None
    last_ok: bool | None = None
    next_run: str | None = None
    output: str = ""

    @property
    def problem(self) -> str | None:
        """``failed`` when the last run failed, ``timer_stopped`` when the timer is not running."""
        if self.last_ok is False:
            return FAILED
        if self.timer_active is False:
            return TIMER_STOPPED
        return None


def read_renewal(
    runner: CommandRunner,
    *,
    log_path: Path = LETSENCRYPT_LOG,
    cron_file: Path = CRON_FILE,
) -> RenewalStatus:
    """
    Read how renewal is going.

    Args:
        runner: Runs ``systemctl`` and ``journalctl``.
        log_path: certbot's log, read for a cron renewal.
        cron_file: The cron entry Noust writes where certbot ships no timer.

    Returns:
        The status. Never raises for a machine that cannot say: that is ``mechanism="none"``.
    """
    for timer, service in TIMER_UNITS:
        properties = _show(
            runner, timer, "LoadState,ActiveState,UnitFileState,NextElapseUSecRealtime"
        )
        if properties.get("LoadState") != "loaded":
            continue
        return _timer_status(runner, timer, service, properties)
    if _has_cron_entry(cron_file):
        return _cron_status(log_path)
    return RenewalStatus()


def _show(runner: CommandRunner, unit: str, properties: str) -> dict[str, str]:
    """
    Args:
        runner: Runs ``systemctl``.
        unit: The unit to ask about.
        properties: The properties wanted, comma separated.

    Returns:
        What systemd said, by property; empty when it could not be asked.
    """
    result = runner.run(["systemctl", "show", unit, "-p", properties], timeout=15)
    if not result.success:
        return {}
    found: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, _, value = line.partition("=")
        if name:
            found[name.strip()] = value.strip()
    return found


def _timer_status(
    runner: CommandRunner, timer: str, service: str, properties: dict[str, str]
) -> RenewalStatus:
    """
    Read the service a timer starts, for the result of its last run.

    Args:
        runner: Runs ``systemctl`` and ``journalctl``.
        timer: The timer's unit.
        service: The service it starts.
        properties: What systemd said about the timer.

    Returns:
        The status of that timer.
    """
    ran = _show(runner, service, "ActiveState,Result,ExecMainStatus,ExecMainExitTimestamp")
    result = ran.get("Result", "")
    stamp = ran.get("ExecMainExitTimestamp", "") or None
    running = ran.get("ActiveState") in ("activating", "active", "reloading")
    last_ok: bool | None
    if running or not result:
        last_ok = None
    elif result == "success":
        last_ok = True if stamp else None
    else:
        last_ok = False
    output = _journal(runner, service) if last_ok is False else ""
    return RenewalStatus(
        mechanism="timer",
        unit=timer,
        timer_active=properties.get("ActiveState") == "active",
        last_run=stamp,
        last_ok=last_ok,
        next_run=properties.get("NextElapseUSecRealtime") or None,
        output=output,
    )


def _journal(runner: CommandRunner, service: str) -> str:
    """
    Args:
        runner: Runs ``journalctl``.
        service: The unit whose last run failed.

    Returns:
        What certbot printed, as the journal kept it, cut to :data:`OUTPUT_LIMIT` from the end
        (the end is what says why it stopped); empty when the journal cannot be read.
    """
    result = runner.run(
        ["journalctl", "-u", service, "-n", _JOURNAL_LINES, "--no-pager", "-o", "cat"], timeout=15
    )
    return result.stdout.strip()[-OUTPUT_LIMIT:] if result.success else ""


def _has_cron_entry(cron_file: Path) -> bool:
    """
    Args:
        cron_file: The entry Noust writes.

    Returns:
        Whether it exists and runs certbot.
    """
    try:
        return "certbot" in cron_file.read_text(encoding="utf-8")
    except OSError:
        return False


def _cron_status(log_path: Path) -> RenewalStatus:
    """
    Read the last renewal of a cron entry from certbot's log, which is all a cron job leaves.

    Args:
        log_path: certbot's log.

    Returns:
        The status: ``last_ok`` is None when the log says nothing about a renewal.
    """
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return RenewalStatus(mechanism="cron")
    lines = text.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        lowered = lines[index].lower()
        if any(marker in lowered for marker in _LOG_OK):
            return RenewalStatus(mechanism="cron", last_ok=True, last_run=_stamp(lines[index]))
        if any(marker in lowered for marker in _LOG_FAILED):
            # The failure's own lines, from the run that began it to the end of the log.
            start = max(0, index - 10)
            for back in range(index, start - 1, -1):
                if "Processing /etc/letsencrypt/renewal" in lines[back]:
                    start = back
                    break
            return RenewalStatus(
                mechanism="cron",
                last_ok=False,
                last_run=_stamp(lines[index]),
                output="\n".join(lines[start:])[-OUTPUT_LIMIT:].strip(),
            )
    return RenewalStatus(mechanism="cron")


def _stamp(line: str) -> str | None:
    """
    Args:
        line: A line of certbot's log, which begins ``2026-11-10 12:00:09,4 - ``.

    Returns:
        Its time, or None when it does not begin with one.
    """
    head = line[:19]
    return head if len(head) == 19 and head[4] == "-" and head[10] == " " else None
