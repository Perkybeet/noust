# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A public status page of the probes that were marked public (checklist G-17).

A status page that lives on the server it reports about is gone when the server
is. So it is **a directory of static files** (``index.html`` and ``status.json``)
that the monitor rewrites every time the probes were asked, and that any web server
can serve: on a Noust central or on a small server of its own, which is the point
of view the probes have (:mod:`noust.managers.probes`). Nothing of the console is
public for it, and there is no route to attack: a request is a file read by nginx.

What a visitor sees is what the operator chose to show and no more: a component's
**title** (never its address, which may carry a key in its query), whether it
answers, how often it did over ninety days, one bar per day, and when it was last
down and for how long. Never the reason of a failure (a TLS error or a status line
describes the inside), never a latency, never a count of checks.

The page is one self-contained document: no script, no external font, no image,
its style inline, a ``Content-Security-Policy`` that permits that and nothing else,
light and dark by the visitor's preference. Every value is escaped; a title with
markup in it is text.

:class:`StatusPageSite` is the one command that puts it on a name over TLS: an
nginx site (the static template) and a certificate, recorded so that taking the
page off removes exactly what publishing made.
"""

from __future__ import annotations

import html
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import paths
from noust.core.config import Config
from noust.core.exceptions import DomainError, NoustError, SiteError
from noust.core.fs import FileSystem, RealFileSystem
from noust.core.messages import Locale, message, normalize_locale
from noust.core.notification_state import NotificationState
from noust.core.notifications.formatting import format_duration
from noust.core.probe_store import DayBar, ProbeStore
from noust.core.store import get_store
from noust.managers.webserver import NGINX_BACKEND, WebServerManager, create_secured_site

log = logging.getLogger(__name__)

#: The files, and where they go when the configuration does not say.
INDEX = "index.html"
DATA = "status.json"
DEFAULT_DIRECTORY = "status-page"

#: Days of history a bar chart shows.
HISTORY_DAYS = 90

#: Incidents shown for each component.
INCIDENTS_SHOWN = 5

#: Longest title of the page.
MAX_PAGE_TITLE = 80

#: What a bar of the chart is, by how often the address answered that day.
GOOD_FROM = 99.9
WARN_FROM = 95.0

#: The state of the whole page, and of a component.
OVERALL = ("operational", "degraded", "outage", "unknown")

#: Where the record of what publishing made is kept.
SCOPE = "status-page"
SITE_ITEM = "site"

_SECURITY_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)

_STYLE = """
:root{color-scheme:light dark;--bg:#fff;--fg:#141414;--muted:#5c5c5c;--line:#d9d9d9;--good:#16784a;--warn:#8c5800;--bad:#c12c24;--none:#d9d9d9}
@media (prefers-color-scheme:dark){:root{--bg:#121212;--fg:#ececec;--muted:#a0a0a0;--line:#333;--good:#4dc47e;--warn:#e3a73c;--bad:#ff736a;--none:#333}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:46rem;margin:0 auto;padding:2rem 1rem 4rem}
h1{font-size:1.5rem;margin:0 0 1rem}
h2{font-size:1rem;margin:2rem 0 .5rem}
.banner{border:1px solid var(--line);border-left-width:.4rem;border-radius:.4rem;padding:.75rem 1rem;font-weight:600}
.banner.operational{border-left-color:var(--good)}
.banner.degraded{border-left-color:var(--warn)}
.banner.outage{border-left-color:var(--bad)}
.banner.unknown{border-left-color:var(--none)}
article{border-top:1px solid var(--line);padding:1rem 0}
.row{display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap}
.name{font-weight:600}
.state{font-weight:600}
.state.up{color:var(--good)}
.state.down{color:var(--bad)}
.state.unknown{color:var(--muted)}
.muted{color:var(--muted);font-size:.9rem}
.bars{display:grid;grid-template-columns:repeat(90,1fr);gap:2px;margin:.5rem 0;height:2rem}
.bars i{display:block;border-radius:2px;background:var(--none)}
.bars i.good{background:var(--good)}
.bars i.warn{background:var(--warn)}
.bars i.bad{background:var(--bad)}
ul{margin:.25rem 0 0;padding-left:1.25rem}
footer{margin-top:2rem}
""".strip()


@dataclass
class Component:
    """
    One public probe, as the page shows it.

    Attributes:
        name: What the page calls it.
        state: ``up``, ``down`` or ``unknown``.
        uptime: How often it answered over ninety days, in percent; None when never asked.
        days: One bar per day, oldest first.
        incidents: Its latest incidents, newest first, as ``{started_at, ended_at}``.
    """

    name: str
    state: str
    uptime: float | None
    days: list[DayBar] = field(default_factory=list)
    incidents: list[dict[str, float | None]] = field(default_factory=list)


@dataclass
class Snapshot:
    """
    What the page says.

    Attributes:
        title: The page's title.
        generated_at: Epoch seconds.
        overall: One of :data:`OVERALL`.
        components: The public probes, by title.
    """

    title: str
    generated_at: float
    overall: str
    components: list[Component] = field(default_factory=list)


def bar_class(day: DayBar) -> str:
    """
    Args:
        day: One day of a probe.

    Returns:
        ``none`` for a day nothing was asked, else ``good``, ``warn`` or ``bad``
        by how often the address answered.
    """
    if not day.checks:
        return "none"
    percent = 100.0 * day.good / day.checks
    if percent >= GOOD_FROM:
        return "good"
    return "warn" if percent >= WARN_FROM else "bad"


def take_snapshot(store: ProbeStore, title: str, now: float) -> Snapshot:
    """
    Gather what the page says.

    Args:
        store: The probes' tables.
        title: The page's title.
        now: Epoch seconds.

    Returns:
        The public probes only, with their state, uptime, days and incidents.
    """
    states = store.states()
    components: list[Component] = []
    for probe in store.probes():
        if not probe.public or not probe.enabled or probe.id is None:
            continue
        state = states.get(probe.id)
        shown = state.state if state is not None else "unknown"
        uptime = store.uptime(probe.id, HISTORY_DAYS)
        components.append(
            Component(
                name=probe.title or probe.name,
                state=shown,
                uptime=uptime.percent,
                days=store.days(probe.id, HISTORY_DAYS),
                incidents=[
                    {"started_at": item.started_at, "ended_at": item.ended_at}
                    for item in store.incidents(probe.id, limit=INCIDENTS_SHOWN)
                ],
            )
        )
    components.sort(key=lambda item: item.name.lower())
    known = [c for c in components if c.state in ("up", "down")]
    if not known:
        overall = "unknown"
    elif all(c.state == "down" for c in known):
        overall = "outage"
    elif any(c.state == "down" for c in known):
        overall = "degraded"
    else:
        overall = "operational"
    return Snapshot(title=title, generated_at=now, overall=overall, components=components)


def _utc(moment: float) -> str:
    """
    Args:
        moment: Epoch seconds.

    Returns:
        ``2026-10-16 08:14 UTC``.
    """
    return datetime.fromtimestamp(moment, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_json(snapshot: Snapshot) -> str:
    """
    Args:
        snapshot: What the page says.

    Returns:
        The same as a document a program can read: no address, no reason, no
        latency.
    """
    document: dict[str, Any] = {
        "title": snapshot.title,
        "generated_at": datetime.fromtimestamp(snapshot.generated_at, tz=timezone.utc).isoformat(),
        "status": snapshot.overall,
        "components": [
            {
                "name": component.name,
                "status": component.state,
                "uptime_percent": component.uptime,
                "days": [
                    {
                        "date": datetime.fromtimestamp(day.day, tz=timezone.utc).strftime(
                            "%Y-%m-%d"
                        ),
                        "uptime_percent": round(100.0 * day.good / day.checks, 2)
                        if day.checks
                        else None,
                    }
                    for day in component.days
                ],
                "incidents": [
                    {
                        "started_at": datetime.fromtimestamp(
                            item["started_at"] or 0, tz=timezone.utc
                        ).isoformat(),
                        "ended_at": datetime.fromtimestamp(
                            item["ended_at"], tz=timezone.utc
                        ).isoformat()
                        if item["ended_at"] is not None
                        else None,
                    }
                    for item in component.incidents
                ],
            }
            for component in snapshot.components
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _e(text: str) -> str:
    """
    Args:
        text: Any text.

    Returns:
        It escaped for HTML text and attribute values.
    """
    return html.escape(text, quote=True)


def render_html(snapshot: Snapshot, locale: Locale) -> str:
    """
    Lay the page out.

    Args:
        snapshot: What the page says.
        locale: The language of Noust's own words on it.

    Returns:
        One self-contained HTML document.
    """
    banner = message(f"ui.status.{snapshot.overall}", locale)
    glyph = {"operational": "●", "degraded": "▲", "outage": "✕", "unknown": "○"}[snapshot.overall]
    parts: list[str] = [
        "<!doctype html>",
        f'<html lang="{locale}">',
        "<head>",
        '<meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{_e(_SECURITY_POLICY)}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="refresh" content="60">',
        '<meta name="robots" content="noindex">',
        f"<title>{_e(snapshot.title)}</title>",
        f"<style>{_STYLE}</style>",
        "</head>",
        "<body><main>",
        f"<h1>{_e(snapshot.title)}</h1>",
        f'<div class="banner {snapshot.overall}" role="status">{glyph} {_e(banner)}</div>',
    ]
    if not snapshot.components:
        parts.append(f'<p class="muted">{_e(message("ui.status.nothing", locale))}</p>')
    for component in snapshot.components:
        parts.append(_component(component, locale))
    parts.append(
        f'<footer class="muted">{_e(message("ui.status.updated", locale, when=_utc(snapshot.generated_at)))}</footer>'
    )
    parts.append("</main></body></html>")
    return "\n".join(parts) + "\n"


def _component(component: Component, locale: Locale) -> str:
    """
    Args:
        component: One public probe.
        locale: The language.

    Returns:
        Its section of the page.
    """
    glyph = {"up": "●", "down": "✕", "unknown": "○"}[component.state]
    label = message(f"ui.status.component.{component.state}", locale)
    uptime = (
        message("ui.status.uptime", locale, percent=f"{component.uptime:g}", days=HISTORY_DAYS)
        if component.uptime is not None
        else ""
    )
    bars = "".join(
        f'<i class="{bar_class(day)}" title="{_e(_bar_title(day, locale))}"></i>'
        for day in component.days
    )
    summary = message("ui.status.history", locale, days=HISTORY_DAYS)
    lines = [
        "<article>",
        f'<div class="row"><span class="name">{_e(component.name)}</span>'
        f'<span class="state {component.state}">{glyph} {_e(label)}</span></div>',
        f'<div class="bars" role="img" aria-label="{_e(summary)}">{bars}</div>',
        f'<div class="row muted"><span>{_e(summary)}</span><span>{_e(uptime)}</span></div>',
    ]
    if component.incidents:
        lines.append(f"<h2>{_e(message('ui.status.incidents', locale))}</h2><ul>")
        for item in component.incidents:
            started = _utc(item["started_at"] or 0)
            if item["ended_at"] is None:
                lasted = message("ui.status.ongoing", locale)
            else:
                lasted = format_duration((item["ended_at"] or 0) - (item["started_at"] or 0))
            lines.append(f"<li>{_e(started)} · {_e(lasted)}</li>")
        lines.append("</ul>")
    lines.append("</article>")
    return "\n".join(lines)


def _bar_title(day: DayBar, locale: Locale) -> str:
    """
    Args:
        day: One day of a probe.
        locale: The language.

    Returns:
        What the browser shows over the bar.
    """
    date = datetime.fromtimestamp(day.day, tz=timezone.utc).strftime("%Y-%m-%d")
    if not day.checks:
        return f"{date}: {message('ui.status.no_data', locale)}"
    return f"{date}: {message('ui.status.day', locale, percent=f'{100.0 * day.good / day.checks:.1f}')}"


class StatusPage:
    """
    Writes the page's files from the probes.

    Args:
        store: The probes' tables.
        config: The configuration (``status_page.*`` and the language).
        fs: Where files are written; the real one by default.
        clock: Epoch seconds.
    """

    def __init__(
        self,
        store: ProbeStore | None = None,
        *,
        config: Config | None = None,
        fs: FileSystem | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store or ProbeStore()
        self._config = config
        self._fs = fs or RealFileSystem()
        self._clock = clock or time.time

    @property
    def config(self) -> Config:
        """The configuration."""
        if self._config is None:
            self._config = Config()
        return self._config

    @property
    def enabled(self) -> bool:
        """Whether the monitor writes the page."""
        return bool(self.config.get("status_page.enabled", False))

    @property
    def directory(self) -> Path:
        """Where the files go."""
        chosen = str(self.config.get("status_page.directory", "") or "").strip()
        return Path(chosen) if chosen else paths.state_dir() / DEFAULT_DIRECTORY

    @property
    def title(self) -> str:
        """What the page is called."""
        chosen = str(self.config.get("status_page.title", "") or "").strip()
        return chosen or "Status"

    def snapshot(self) -> Snapshot:
        """
        Returns:
            What the page says now.
        """
        return take_snapshot(self._store, self.title, self._clock())

    def write(self) -> list[Path]:
        """
        Write the page and its data, each atomically.

        Returns:
            The files written.

        Raises:
            OSError: When the directory cannot be written.
        """
        snapshot = self.snapshot()
        locale = normalize_locale(self.config.get("notifications.language"))
        directory = self.directory
        self._fs.make_dir(directory, mode=0o755)
        written: list[Path] = []
        for name, content in (
            (INDEX, render_html(snapshot, locale)),
            (DATA, render_json(snapshot)),
        ):
            target = directory / name
            self._fs.write_text(target, content, mode=0o644)
            written.append(target)
        return written

    def remove(self) -> None:
        """Delete the files this page wrote; the directory stays."""
        for name in (INDEX, DATA):
            self._fs.remove(self.directory / name, missing_ok=True)


def validate_title(value: Any) -> str:
    """
    Args:
        value: What ``status_page.title`` was set to.

    Returns:
        The title, trimmed.

    Raises:
        ValueError: When it is too long or has control characters.
    """
    text = " ".join(str(value or "").split())
    if len(text) > MAX_PAGE_TITLE:
        raise ValueError(f"status_page.title is at most {MAX_PAGE_TITLE} characters")
    return text


def validate_directory(value: Any) -> str:
    """
    Args:
        value: What ``status_page.directory`` was set to.

    Returns:
        The path, or an empty string for the default.

    Raises:
        ValueError: When it is not an absolute path without ``..``.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute() or ".." in path.parts or "\x00" in text:
        raise ValueError("status_page.directory must be an absolute path without '..'")
    return str(path)


@dataclass
class SiteRecord:
    """
    What publishing the page made.

    Attributes:
        domain: The name it is on.
        site_created: The nginx site did not exist.
        certificate_created: The certificate did not exist.
    """

    domain: str
    site_created: bool = False
    certificate_created: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The record as it is stored.
        """
        return {
            "domain": self.domain,
            "site_created": self.site_created,
            "certificate_created": self.certificate_created,
        }


class StatusPageSite:
    """
    Puts the page on a name over TLS, and takes it off again.

    Args:
        page: The page whose directory is served.
        state: Where the record of what was made is kept.
        nginx: The nginx manager (tests).
        certs: The certificate manager (tests).
        dns_check: Tells whether a name resolves to this machine (tests).
    """

    def __init__(
        self,
        page: StatusPage,
        *,
        state: NotificationState | None = None,
        nginx: WebServerManager | None = None,
        certs: Any = None,
        dns_check: Callable[[str], Any] | None = None,
    ) -> None:
        self._page = page
        self._state = state or NotificationState()
        self._nginx = nginx
        self._certs = certs
        self._dns_check = dns_check

    @property
    def nginx(self) -> WebServerManager:
        """The nginx manager."""
        if self._nginx is None:
            self._nginx = WebServerManager(NGINX_BACKEND)
        return self._nginx

    @property
    def certs(self) -> Any:
        """The certificate manager."""
        if self._certs is None:
            from noust.managers.cert_manager import CertManager

            self._certs = CertManager()
        return self._certs

    def record(self) -> SiteRecord | None:
        """
        Returns:
            What publishing made, or None when the page is not on a name.
        """
        stored = self._state.get_json(SCOPE, SITE_ITEM)
        if not isinstance(stored, dict) or not stored.get("domain"):
            return None
        return SiteRecord(
            domain=str(stored["domain"]),
            site_created=bool(stored.get("site_created")),
            certificate_created=bool(stored.get("certificate_created")),
        )

    def publish(self, domain: str, *, skip_dns_check: bool = False) -> SiteRecord:
        """
        Serve the page on a name, over TLS.

        The page's files are written first, so the site never serves an
        empty directory.

        Args:
            domain: The name, already pointing at this server.
            skip_dns_check: Do not require the name to resolve to this machine
                (a server behind NAT cannot see its public address).

        Returns:
            What was made.

        Raises:
            RoleError: On a hub, which has no nginx.
            DomainError: The name is invalid, an application's, or not pointing here.
            SiteError: A site Noust did not make for the page is on the name, or
                another name already carries the page.
            CertificateError: No certificate could be obtained; nothing this
                call made is left.
        """
        from noust.managers.console_exposure import normalise_domain

        require_server_role("The status page")
        name = normalise_domain(domain)
        existing = self.record()
        if existing is not None and existing.domain != name:
            raise SiteError(
                f"The status page is already published at {existing.domain}",
                details="Take it off first: noust probe status-page unpublish",
            )
        owner = get_store().domain_owner(name)
        if owner is not None:
            raise DomainError(
                f"{name} belongs to the application {owner[0]}",
                details="The status page needs a name of its own (status.example.com, say).",
            )
        if self.nginx.site_exists(name) and existing is None:
            raise SiteError(
                f"{name} already has an nginx site Noust did not make for the status page",
                details=f"Choose another name, or remove {self.nginx.config_path(name)} first.",
            )
        if not skip_dns_check:
            self._require_dns(name)
        self._page.write()
        record = existing or SiteRecord(domain=name)
        self._state.put_json(SCOPE, SITE_ITEM, record.to_dict())
        completed = False
        try:
            secured = create_secured_site(
                name,
                manager=self.nginx,
                webserver="nginx",
                cert_manager=self.certs,
                template="static",
                context={"static_dir": str(self._page.directory)},
            )
            if not secured.site_existed:
                record.site_created = True
            if secured.ssl_enabled and not secured.certificate_reused:
                record.certificate_created = True
            self._state.put_json(SCOPE, SITE_ITEM, record.to_dict())
            if not secured.ssl_enabled:
                from noust.core.exceptions import CertificateError

                raise CertificateError(
                    f"No certificate could be obtained for {name}",
                    details=(
                        f"{secured.certificate_error or 'certbot gave no reason'}\n"
                        f"Check that {name} points at this server and that ports 80 and 443 "
                        "reach it, then run the command again."
                    ),
                )
            completed = True
        finally:
            if not completed and existing is None:
                self._undo(record)
        return record

    def unpublish(self) -> list[str]:
        """
        Take the page off its name, removing exactly what publishing made.

        Returns:
            What was removed; empty when nothing was published.
        """
        record = self.record()
        if record is None:
            return []
        return self._undo(record)

    def _undo(self, record: SiteRecord) -> list[str]:
        """
        Remove what a record says was made, each step on its own.

        Args:
            record: What was made.

        Returns:
            What was removed.
        """
        removed: list[str] = []
        name = record.domain
        if record.site_created:
            try:
                if self.nginx.site_exists(name):
                    self.nginx.disable_site(name)
                    self.nginx.delete_site(name)
                    self.nginx.reload()
                    removed.append(f"nginx site of {name}")
            except (NoustError, OSError) as exc:
                log.warning("Could not remove the nginx site of %s: %s", name, exc)
                removed.append(f"NOT removed: nginx site of {name} ({exc})")
        if record.certificate_created:
            try:
                if self.certs.is_installed() and self.certs.cert_exists(name):
                    self.certs.delete(name)
                    removed.append(f"certificate of {name}")
            except (NoustError, OSError) as exc:
                log.warning("Could not remove the certificate of %s: %s", name, exc)
                removed.append(f"NOT removed: certificate of {name} ({exc})")
        self._state.delete(SCOPE, SITE_ITEM)
        return removed

    def _require_dns(self, name: str) -> None:
        """
        Refuse a name that does not resolve to this machine.

        Args:
            name: The public name.

        Raises:
            DomainError: When it does not point here.
        """
        check = self._dns_check
        if check is None:
            from noust.deployers.domains import check_dns

            check = check_dns
        result = check(name)
        if result.points_here:
            return
        resolved = ", ".join(result.resolved_addresses) or "nothing"
        raise DomainError(
            f"{name} does not point at this server",
            details=(
                f"It resolves to {resolved}; this server answers on "
                f"{', '.join(result.expected_addresses) or 'no public address'}. Create the DNS "
                "record, wait for it to spread, and run the command again. On a server behind "
                "NAT, add --skip-dns-check."
            ),
        )
