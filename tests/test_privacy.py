# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The privacy guarantee of docs/PRIVACY.md, held by tests (checklist N-36).

Noust sends nothing about an installation to anyone: no usage analytics, no error
reports, no "phone home". That sentence is only worth something if it can be checked,
and a code review cannot check a sentence about what a codebase will never do. So the
guarantee is three structural facts that fail loudly when a change breaks them:

1. No telemetry or error-reporting library is imported or declared as a dependency.
2. Every module that can open an outbound connection is declared here, with whose
   address it talks to. A new client is a new place data can leave from: it is added
   to the table below, with a reason, by whoever writes it, and the reviewer reads it.
3. The only request Noust makes without being told where to (the update check) says
   nothing about the installation and is turned off by ``updates.check``.

The table is the inventory docs/PRIVACY.md describes in words.
"""

from __future__ import annotations

import ast
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import noust

SRC = Path(noust.__file__).resolve().parent
REPO = SRC.parent.parent

#: Modules whose import can reach a host. ``socket`` is not in the list because most of
#: its uses read the machine's own name; its connecting calls are checked on their own.
CLIENT_MODULES = (
    "urllib.request",
    "http.client",
    "smtplib",
    "ftplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "xmlrpc.client",
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "websocket",
    "websockets.client",
    "websockets.asyncio.client",
    "websockets.legacy.client",
    "websockets.sync.client",
    "grpc",
)

#: Libraries whose whole purpose is to report what an installation does to a service
#: that belongs to somebody else. None may be imported, and none may be a dependency.
TELEMETRY_LIBRARIES = (
    "sentry_sdk",
    "sentry-sdk",
    "raven",
    "rollbar",
    "bugsnag",
    "honeybadger",
    "airbrake",
    "posthog",
    "segment",
    "analytics",
    "mixpanel",
    "amplitude",
    "heap",
    "opentelemetry",
    "newrelic",
    "ddtrace",
    "datadog",
    "elastic_apm",
    "appsignal",
    "scout_apm",
    "logfire",
    "statsd",
)

#: Whose address a client talks to.
OPERATOR = "an address the operator configured or typed"
OWN = "this machine or the operator's own application"
NODE = "an enrolled node, through an SSH tunnel to this machine's loopback"
RELEASE = "release information, the one request made unasked; updates.check turns it off"
PUBLIC_LIST = "a public list, only when the operator asks to refresh it"
CI = "a release gate run by the project's CI, never by an installation"

#: Every module that imports an outbound client, and for whom it speaks. Keep it sorted by path.
CLIENTS: dict[str, tuple[str, str]] = {
    "client/api.py": (OPERATOR, "the remote command line, to the console named by --context"),
    "core/accounts/oidc_client.py": (OPERATOR, "the identity provider set up for single sign-on"),
    "core/email_api.py": (
        OPERATOR,
        "the email service a channel sends through (Resend, Postmark, Brevo, Amazon SES)",
    ),
    "core/notifier.py": (
        OPERATOR,
        "notification channels: webhooks, Slack, Discord, ntfy, Telegram, Pushover, Gotify",
    ),
    "core/notifier_requests.py": (OPERATOR, "the same channels' request builder"),
    "core/package_index.py": (
        RELEASE,
        "the newest version, from GitHub, PyPI and the package repository this installation upgrades from",
    ),
    "core/variables/providers.py": (
        OPERATOR,
        "the secret managers an application's variables refer to",
    ),
    "deployers/helpers/health.py": (OWN, "the health gate of an application being deployed"),
    "deployers/importers/remote.py": (
        OPERATOR,
        "a Coolify or Dokploy server whose applications are imported",
    ),
    "fleet/aggregate.py": (NODE, "the fleet's view over its nodes"),
    "fleet/client.py": (NODE, "a node's API"),
    "fleet/cloud/http.py": (OPERATOR, "a cloud provider's API, with the operator's token"),
    "fleet/nodes.py": (NODE, "the registry of nodes"),
    "integrations/github/client.py": (
        OPERATOR,
        "GitHub or GitHub Enterprise, as the connected account",
    ),
    "managers/diagnose.py": (OWN, "the probe that says why an application is down"),
    "managers/dns/rest.py": (
        OPERATOR,
        "the DNS API of Cloudflare, DigitalOcean, Hetzner, Gandi or Route 53, with the operator's token",
    ),
    "managers/log_transports.py": (OPERATOR, "the HTTP log destinations the operator configured"),
    "managers/probes.py": (OPERATOR, "the addresses the operator asked to probe from outside"),
    "managers/source_manager.py": (OPERATOR, "an archive URL an application is deployed from"),
    "managers/trusted_proxies.py": (PUBLIC_LIST, "Cloudflare's published address ranges"),
    "monitor/email_notifier.py": (OPERATOR, "the SMTP server the operator configured"),
    "monitor/reachability.py": (OWN, "whether an application answers"),
    "web/api/node_proxy.py": (NODE, "every call, event and socket a central forwards to a node"),
    "web/fleet_jobs.py": (NODE, "a job run on a node"),
}

#: The modules allowed to call the update check's reader, and why each is not a leak: the check
#: itself, the release notes (both off with ``updates.check``), and the release gate the project's
#: CI runs (``scripts/obs_published.py``), which no installation ever calls.
UNASKED_READERS: dict[str, str] = {
    "core/release_notes.py": "release notes, read only while updates.check is on",
    "core/update_checker.py": "the update check, off with updates.check",
    "managers/server/publication.py": CI,
}

#: Files that connect a socket by hand, and where to.
CONNECTORS: dict[str, tuple[str, str]] = {
    "core/accounts/ldap_client.py": (
        OPERATOR,
        "the directory (LDAP, Active Directory) set up for sign-in",
    ),
    "core/app_state.py": (OWN, "is the application's port open"),
    "core/audit/sinks.py": (OPERATOR, "the syslog destination"),
    "core/net.py": (
        OWN,
        "a UDP connect, which sends nothing: it asks the kernel which address it would use",
    ),
    "deployers/helpers/summary.py": (
        OWN,
        "a UDP connect, which sends nothing: it asks the kernel which address it would use",
    ),
    "fleet/tunnels.py": (NODE, "is the tunnel's local end open"),
    "validators/port.py": (OWN, "is a port free on this machine"),
}


def sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def imported(path: Path) -> set[str]:
    """Every dotted name a module imports, including ``from x import y`` as ``x.y``."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def uses(name: str, module: str) -> bool:
    return name == module or name.startswith(module + ".")


def clients_in_source() -> set[str]:
    found = set()
    for path in sources():
        if any(uses(name, module) for name in imported(path) for module in CLIENT_MODULES):
            found.add(path.relative_to(SRC).as_posix())
    return found


def connectors_in_source() -> set[str]:
    """Files that call ``create_connection`` / ``connect_ex``, or connect a socket to a literal address."""
    found = set()
    for path in sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr in {"create_connection", "connect_ex"}:
                found.add(path.relative_to(SRC).as_posix())
            elif node.func.attr == "connect" and node.args and isinstance(node.args[0], ast.Tuple):
                found.add(path.relative_to(SRC).as_posix())
    return found


def test_no_telemetry_or_error_reporting_library_is_imported() -> None:
    offenders = {
        path.relative_to(SRC).as_posix(): sorted(
            name
            for name in imported(path)
            for library in TELEMETRY_LIBRARIES
            if uses(name, library.replace("-", "_"))
        )
        for path in sources()
    }
    offenders = {path: names for path, names in offenders.items() if names}
    assert not offenders, (
        "A telemetry or error-reporting library is imported, which docs/PRIVACY.md promises never happens:\n"
        + "\n".join(f"  {path}: {', '.join(names)}" for path, names in sorted(offenders.items()))
    )


#: The lines of each file that declare what Noust depends on; prose, changelogs and the
#: names of recipes an operator may deploy are not dependencies.
DEPENDENCY_LINE = re.compile(
    r"^\s*(?:[\"'][A-Za-z0-9_.\-]+(?:\[[^\]]*\])?\s*(?:[<>=!~;,].*)?[\"'],?\s*|(?:Build-?)?(?:Requires|Depends|Recommends|Suggests)\s*:.*)$",
    re.IGNORECASE,
)


def declared_dependencies(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        if not DEPENDENCY_LINE.match(line):
            continue
        for token in re.findall(r"[a-z0-9][a-z0-9._-]*", line.lower().replace("_", "-")):
            names.add(re.sub(r"^python3?-", "", token))
    return names


@pytest.mark.parametrize(
    "declaration", ["pyproject.toml", "setup.py", "obs/debian.control", "rpm/noust.spec"]
)
def test_no_telemetry_or_error_reporting_library_is_a_dependency(declaration: str) -> None:
    path = REPO / declaration
    if not path.exists():
        pytest.skip(f"{declaration} is not in this checkout")
    dependencies = declared_dependencies(path.read_text(encoding="utf-8"))
    assert dependencies, (
        f"{declaration}: no dependency line was recognised, so this test would pass for nothing"
    )
    found = sorted(dependencies & {library.replace("_", "-") for library in TELEMETRY_LIBRARIES})
    assert not found, (
        f"{declaration} depends on {found}: Noust reports nothing to a third party (docs/PRIVACY.md)"
    )


def test_every_outbound_client_is_declared_with_whose_address_it_talks_to() -> None:
    found = clients_in_source()
    declared = set(CLIENTS)
    assert not found - declared, (
        "These modules can now open a connection and are not in tests/test_privacy.py CLIENTS. "
        "Say whose address each one talks to (and put it in docs/PRIVACY.md if it is a new kind of destination):\n"
        + "\n".join(f"  {name}" for name in sorted(found - declared))
    )
    assert not declared - found, (
        "These declared clients no longer import a network client; remove them from CLIENTS so the inventory stays true:\n"
        + "\n".join(f"  {name}" for name in sorted(declared - found))
    )


#: The functions of ``package_index`` that open a connection; the rest compare versions or parse files.
RELEASE_READERS = frozenset(
    {
        "fetch",
        "github_latest",
        "github_security_releases",
        "pypi_latest",
        "offer",
        "installable_version",
        "apt_latest",
        "apt_offer",
        "rpm_latest",
        "rpm_offer",
        "rpm_index_latest",
    }
)


def test_nothing_new_reads_the_release_information_unasked() -> None:
    readers = set()
    for path in sources():
        if path.name == "package_index.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in RELEASE_READERS
                and isinstance(node.value, ast.Name)
                and node.value.id == "package_index"
            ):
                readers.add(path.relative_to(SRC).as_posix())
    assert readers == set(UNASKED_READERS), (
        "The modules that read release information changed. A new reader is a new request made without anyone "
        "asking for it: gate it on updates.check, then declare it in UNASKED_READERS and in docs/PRIVACY.md.\n"
        f"  now: {sorted(readers)}"
    )


def test_every_socket_connection_is_declared() -> None:
    found = connectors_in_source()
    declared = set(CONNECTORS)
    assert not found - declared, (
        "These modules connect a socket and are not in tests/test_privacy.py CONNECTORS:\n"
        + "\n".join(f"  {name}" for name in sorted(found - declared))
    )
    assert not declared - found, (
        "These declared connectors no longer connect a socket; remove them from CONNECTORS:\n"
        + "\n".join(f"  {name}" for name in sorted(declared - found))
    )


@pytest.mark.allow_sockets
def test_the_only_unasked_request_is_the_update_check_and_names_no_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release information is read from three public places and says only that it is the update check."""
    from noust.core import package_index

    urls = (package_index.GITHUB_LATEST, package_index.GITHUB_RELEASES, package_index.PYPI_JSON)
    assert {urlsplit(url).hostname for url in urls} == {"api.github.com", "pypi.org"}

    sent: list[dict[str, str]] = []

    class Opened:
        def __enter__(self) -> Opened:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            return b""

    class Opener:
        def open(self, request: urllib.request.Request, timeout: float) -> Opened:
            sent.append(dict(request.header_items()))
            return Opened()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    package_index.fetch(package_index.GITHUB_LATEST, accept="application/vnd.github+json")

    # Nothing but the name of the check and what is asked for: no version, no host, no identifier.
    assert sent == [{"User-agent": "noust-update-check", "Accept": "application/vnd.github+json"}]


def test_the_privacy_statement_names_every_kind_of_destination_the_table_declares() -> None:
    statement = (REPO / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    for needle in ("updates.check", "tests/test_privacy.py", "api.github.com", "pypi.org"):
        assert needle in statement, f"docs/PRIVACY.md does not mention {needle}"
