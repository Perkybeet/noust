# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust console expose``: the console on a name of its own, over TLS.

Each test names one promise the exposure makes: what it refuses before writing
anything (an account without a second factor, the ENS profile, a name that is
an application's or an operator's site), what it writes (an nginx site that
replaces forwarding headers and lets only the allow-list in), what it undoes
when a step fails, and that ``unexpose`` removes exactly what was made, no more.
The web server and the certificate authority are the project's real managers
over a temporary tree, with certbot replaced by a fake; the console's service is
a recording stand-in.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.central import RoleError
from noust.cli.app import cli as root_cli
from noust.cli.commands import console as console_cmd
from noust.core.config import Config
from noust.core.console_exposure_store import STATE_ACTIVE, STATE_PENDING, ConsoleExposureStore
from noust.core.exceptions import (
    CertificateError,
    DomainError,
    SecurityError,
    ServiceError,
    SiteError,
)
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers import console_exposure
from noust.managers.console_exposure import (
    PROXY_PEER,
    ConsoleExposure,
    SystemConsole,
    parse_allow_ips,
    second_factor_blockers,
)
from noust.managers.webserver import NGINX_BACKEND, WebServerManager

DOMAIN = "console.example.com"
PORT = 8080


class FakeCerts:
    """A certificate manager that issues on paper."""

    def __init__(self, *, already: bool = False, fail: bool = False) -> None:
        self.installed = True
        self.fail = fail
        self.obtained: list[str] = []
        self.existing = {DOMAIN} if already else set()
        self.deleted: list[str] = []

    def is_installed(self) -> bool:
        return self.installed

    def cert_exists(self, domain: str) -> bool:
        return domain in self.existing or domain in self.obtained

    def test_cert(self, domain: str) -> dict[str, Any]:
        return {"valid": True}

    def cert_covers_domains(self, domain: str, names: list[str]) -> bool:
        return True

    def obtain(self, domain: str, **kwargs: Any) -> bool:
        if self.fail:
            raise CertificateError("Certificate issuance failed", details="DNS does not point here")
        self.obtained.append(domain)
        return True

    def get_cert_path(self, domain: str) -> dict[str, Path]:
        live = Path("/etc/letsencrypt/live") / domain
        return {"fullchain": live / "fullchain.pem", "privkey": live / "privkey.pem"}

    def delete(self, domain: str) -> bool:
        self.deleted.append(domain)
        self.obtained = [d for d in self.obtained if d != domain]
        self.existing.discard(domain)
        return True


class FakeConsole:
    """The console's service, as far as the exposure can tell."""

    def __init__(
        self, *, service: bool = True, trusted: bool = False, scheme: str = "http"
    ) -> None:
        self.service = service
        self.trusted = trusted
        self.scheme = scheme
        self.restarts: list[str] = []
        self.fail_trust = False

    def is_service(self) -> bool:
        return self.service

    def upstream(self) -> tuple[int, str]:
        return PORT, self.scheme

    def trust_proxy(self, address: str) -> bool:
        if self.fail_trust:
            raise ServiceError("The console's unit could not be rewritten", details="disk full")
        changed = not self.trusted
        self.trusted = True
        return changed

    def forget_proxy(self, address: str) -> bool:
        changed = self.trusted
        self.trusted = False
        return changed

    def restart(self, mode: str) -> None:
        self.restarts.append(mode)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of its own, where the exposure and the application names live."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The real configuration singleton, over a file in the test's directory."""
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    yield path
    Config.reset_instance()


@pytest.fixture
def nginx(tmp_path: Path, runner: FakeRunner, store: NoustStore) -> WebServerManager:
    """The real nginx manager over a temporary tree."""
    return WebServerManager(
        replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


@pytest.fixture
def certs() -> FakeCerts:
    return FakeCerts()


@pytest.fixture
def service() -> FakeConsole:
    return FakeConsole()


def points_here(_domain: str) -> SimpleNamespace:
    return SimpleNamespace(
        points_here=True, resolved_addresses=("203.0.113.5",), expected_addresses=()
    )


@pytest.fixture
def exposure(
    store: NoustStore,
    config_file: Path,
    nginx: WebServerManager,
    certs: FakeCerts,
    service: FakeConsole,
) -> ConsoleExposure:
    """An exposure over the sandbox, with every account holding a second factor."""
    return ConsoleExposure(
        console=service,
        store=ConsoleExposureStore(store),
        nginx=nginx,
        certs=certs,  # type: ignore[arg-type]
        config=Config(),
        dns_check=points_here,
        second_factor=list,
    )


def site_text(nginx: WebServerManager) -> str:
    return nginx.get_site_config(DOMAIN) or ""


# ---------------------------------------------------------------------------
# What it writes
# ---------------------------------------------------------------------------


def test_the_console_is_proxied_over_tls_with_headers_nginx_replaces(
    exposure: ConsoleExposure, nginx: WebServerManager, certs: FakeCerts, service: FakeConsole
) -> None:
    """Loopback upstream, HTTPS only, and the client's own X-Forwarded-For never believed."""
    result = exposure.expose(DOMAIN, allow_ips=["203.0.113.0/24"], restart="later")

    text = site_text(nginx)
    assert text.startswith(console_exposure.MARKER)
    assert "Generated by Noust" in text
    assert "proxy_pass http://127.0.0.1:8080;" in text
    assert "listen 443 ssl" in text
    assert "/etc/letsencrypt/live/console.example.com/fullchain.pem" in text
    # Replaced, never appended to: the address the rate limit is keyed on is nginx's own view.
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in text
    assert "$proxy_add_x_forwarded_for" not in text
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in text
    assert "proxy_buffering off;" in text
    assert "allow 203.0.113.0/24;" in text and "deny all;" in text
    assert "return 301 https://$host$request_uri;" in text
    assert result.url == f"https://{DOMAIN}"
    assert certs.obtained == [DOMAIN]
    assert nginx.site_enabled(DOMAIN)


def test_without_an_allow_list_nothing_is_denied_and_the_note_says_so(
    exposure: ConsoleExposure, nginx: WebServerManager
) -> None:
    """No allow-list writes no deny, and the operator is told the second factor is all there is."""
    result = exposure.expose(DOMAIN)

    assert "deny all" not in site_text(nginx)
    assert any("second factor alone" in note for note in result.notes)


def test_a_console_serving_tls_is_proxied_over_https_without_verification(
    exposure: ConsoleExposure, nginx: WebServerManager, service: FakeConsole
) -> None:
    """Its own self-signed certificate on loopback is not what the browser sees."""
    service.scheme = "https"

    exposure.expose(DOMAIN)

    text = site_text(nginx)
    assert "proxy_pass https://127.0.0.1:8080;" in text
    assert "proxy_ssl_verify off;" in text


def test_until_there_is_a_certificate_nothing_of_the_console_is_served(
    nginx: WebServerManager,
) -> None:
    """The first, plain write answers 404 beside the challenge: never the console in clear."""
    text = nginx.render_config(
        DOMAIN, "console", {"ssl": False, "port": PORT, "allow_ips": ["203.0.113.7/32"]}
    )

    assert "acme-challenge" in text
    assert "return 404;" in text
    assert "proxy_pass" not in text
    assert "listen 443" not in text


def test_the_console_answers_only_its_name_and_knows_its_public_origin(
    exposure: ConsoleExposure, config_file: Path
) -> None:
    exposure.expose(DOMAIN)

    config = Config()
    assert config.get("web.public_url") == f"https://{DOMAIN}"
    assert config.get("web.allowed_hosts") == [DOMAIN]
    assert f"https://{DOMAIN}" in config_file.read_text(encoding="utf-8")


def test_the_console_is_told_to_trust_nginx_and_restarted(
    exposure: ConsoleExposure, service: FakeConsole
) -> None:
    exposure.expose(DOMAIN, restart="later")

    assert service.trusted
    assert service.restarts == ["later"]
    assert PROXY_PEER == "127.0.0.1"


def test_what_was_made_is_recorded_and_the_exposure_is_active(
    exposure: ConsoleExposure, store: NoustStore
) -> None:
    exposure.expose(DOMAIN, allow_ips=["203.0.113.7"], actor="cli:maria")

    state = ConsoleExposureStore(store).get()
    assert state is not None
    assert state.state == STATE_ACTIVE
    assert state.allow_ips == ["203.0.113.7/32"]
    assert state.site_created and state.certificate_created
    assert state.proxy_added == "127.0.0.1"
    assert state.created_by == "cli:maria"
    assert exposure.status()["exposed"] is True


# ---------------------------------------------------------------------------
# Removing exactly what it made
# ---------------------------------------------------------------------------


def test_unexpose_removes_what_expose_made(
    exposure: ConsoleExposure,
    nginx: WebServerManager,
    certs: FakeCerts,
    service: FakeConsole,
    store: NoustStore,
) -> None:
    exposure.expose(DOMAIN)

    result = exposure.unexpose(restart="now")

    assert not nginx.site_exists(DOMAIN)
    assert certs.deleted == [DOMAIN]
    assert not service.trusted
    assert service.restarts[-1] == "now"
    assert ConsoleExposureStore(store).get() is None
    config = Config()
    assert not config.get("web.public_url")
    assert not config.get("web.allowed_hosts")
    assert any("nginx site" in item for item in result.removed)
    assert any("certificate" in item for item in result.removed)


def test_a_certificate_that_was_already_there_is_left_alone(
    store: NoustStore,
    config_file: Path,
    nginx: WebServerManager,
    service: FakeConsole,
) -> None:
    """Noust removes only what it obtained: an existing, valid certificate stays."""
    certs = FakeCerts(already=True)
    exposure = ConsoleExposure(
        console=service,
        store=ConsoleExposureStore(store),
        nginx=nginx,
        certs=certs,  # type: ignore[arg-type]
        config=Config(),
        dns_check=points_here,
        second_factor=list,
    )
    exposure.expose(DOMAIN)

    exposure.unexpose()

    assert certs.obtained == [] and certs.deleted == []


def test_a_proxy_trust_the_unit_already_had_is_not_taken_away(
    exposure: ConsoleExposure, service: FakeConsole
) -> None:
    service.trusted = True

    exposure.expose(DOMAIN)
    exposure.unexpose()

    assert service.trusted
    state = exposure.state()
    assert state is None


def test_settings_the_operator_changed_since_are_theirs(
    exposure: ConsoleExposure, config_file: Path
) -> None:
    exposure.expose(DOMAIN)
    config = Config()
    config.set("web.public_url", "https://panel.example.org")
    config.set("web.allowed_hosts", ["panel.example.org", DOMAIN])
    config.write()

    exposure.unexpose()

    config = Config()
    assert config.get("web.public_url") == "https://panel.example.org"
    # Only the name Noust added goes; the operator's own stays.
    assert config.get("web.allowed_hosts") == ["panel.example.org"]


def test_what_the_settings_held_before_comes_back(
    exposure: ConsoleExposure, config_file: Path
) -> None:
    config = Config()
    config.set("web.public_url", "https://old.example.org")
    config.set("web.allowed_hosts", ["old.example.org"])
    config.write()

    exposure.expose(DOMAIN)
    exposure.unexpose()

    config = Config()
    assert config.get("web.public_url") == "https://old.example.org"
    assert config.get("web.allowed_hosts") == ["old.example.org"]


def test_unexpose_when_nothing_is_published_says_so(exposure: ConsoleExposure) -> None:
    result = exposure.unexpose()

    assert result.removed == []
    assert result.notes == ["The console was not published."]


def test_a_run_that_died_halfway_is_cleaned_up_by_unexpose(
    exposure: ConsoleExposure, nginx: WebServerManager, store: NoustStore, certs: FakeCerts
) -> None:
    """A pending record says what exists: the site, the certificate."""
    exposure.expose(DOMAIN)
    state = ConsoleExposureStore(store).get()
    assert state is not None
    state.state = STATE_PENDING
    ConsoleExposureStore(store).save(state)
    assert "did not finish" in exposure.status()["problems"][0]

    exposure.unexpose()

    assert not nginx.site_exists(DOMAIN)
    assert ConsoleExposureStore(store).get() is None


# ---------------------------------------------------------------------------
# What it undoes when a step fails
# ---------------------------------------------------------------------------


def test_no_certificate_leaves_nothing_behind(
    exposure: ConsoleExposure,
    nginx: WebServerManager,
    certs: FakeCerts,
    service: FakeConsole,
    store: NoustStore,
) -> None:
    certs.fail = True

    with pytest.raises(CertificateError) as raised:
        exposure.expose(DOMAIN)

    assert "No certificate could be obtained" in raised.value.message
    assert "ports 80 and 443" in (raised.value.details or "")
    assert not nginx.site_exists(DOMAIN)
    assert not service.trusted and service.restarts == []
    assert ConsoleExposureStore(store).get() is None
    assert not Config().get("web.public_url")


def test_a_unit_that_cannot_be_rewritten_undoes_the_site_the_certificate_and_the_settings(
    exposure: ConsoleExposure,
    nginx: WebServerManager,
    certs: FakeCerts,
    service: FakeConsole,
    store: NoustStore,
) -> None:
    service.fail_trust = True

    with pytest.raises(ServiceError):
        exposure.expose(DOMAIN)

    assert not nginx.site_exists(DOMAIN)
    assert certs.deleted == [DOMAIN]
    assert not Config().get("web.public_url")
    assert not Config().get("web.allowed_hosts")
    assert ConsoleExposureStore(store).get() is None


def test_running_it_again_updates_the_allow_list_and_keeps_what_the_first_run_made(
    exposure: ConsoleExposure, nginx: WebServerManager, certs: FakeCerts, store: NoustStore
) -> None:
    exposure.expose(DOMAIN)

    exposure.expose(DOMAIN, allow_ips=["198.51.100.0/24"])

    assert "allow 198.51.100.0/24;" in site_text(nginx)
    state = ConsoleExposureStore(store).get()
    assert state is not None and state.site_created and state.certificate_created
    exposure.unexpose()
    assert not nginx.site_exists(DOMAIN)
    assert certs.deleted == [DOMAIN]


def test_a_failing_second_run_leaves_the_working_exposure_as_it_was(
    exposure: ConsoleExposure, nginx: WebServerManager, service: FakeConsole, store: NoustStore
) -> None:
    exposure.expose(DOMAIN)
    service.fail_trust = True
    service.trusted = False

    with pytest.raises(ServiceError):
        exposure.expose(DOMAIN, allow_ips=["198.51.100.0/24"])

    assert nginx.site_exists(DOMAIN)
    state = ConsoleExposureStore(store).get()
    assert state is not None and state.state == STATE_ACTIVE and state.site_created


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


def test_an_account_without_a_second_factor_stops_it_before_anything_is_written(
    store: NoustStore,
    config_file: Path,
    nginx: WebServerManager,
    certs: FakeCerts,
    service: FakeConsole,
) -> None:
    exposure = ConsoleExposure(
        console=service,
        store=ConsoleExposureStore(store),
        nginx=nginx,
        certs=certs,  # type: ignore[arg-type]
        config=Config(),
        dns_check=points_here,
        second_factor=lambda: ["The account maria signs in with a password alone."],
    )

    with pytest.raises(SecurityError) as raised:
        exposure.expose(DOMAIN)

    assert "second factor" in raised.value.message
    assert "maria" in (raised.value.details or "")
    assert not nginx.site_exists(DOMAIN)
    assert certs.obtained == []
    assert ConsoleExposureStore(store).get() is None


def test_the_ens_profile_keeps_the_console_on_loopback_unless_forced(
    exposure: ConsoleExposure, config_file: Path, nginx: WebServerManager
) -> None:
    config = Config()
    config.set("security.profile", "ens-medium")
    config.write()

    with pytest.raises(SecurityError) as raised:
        exposure.expose(DOMAIN, allow_ips=["203.0.113.7"])

    assert "ENS profile" in raised.value.message
    assert "--force-ens" in (raised.value.details or "")
    assert not nginx.site_exists(DOMAIN)


def test_a_forced_exposure_under_ens_still_needs_an_allow_list(
    exposure: ConsoleExposure, config_file: Path
) -> None:
    config = Config()
    config.set("security.profile", "ens-medium")
    config.write()

    with pytest.raises(SecurityError) as raised:
        exposure.expose(DOMAIN, force_ens=True)

    assert "allow-list" in raised.value.message


def test_a_forced_exposure_under_ens_with_an_allow_list_goes_through_and_is_audited(
    exposure: ConsoleExposure,
    config_file: Path,
    nginx: WebServerManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.set("security.profile", "ens-medium")
    config.write()
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        console_exposure.audit,
        "record",
        lambda event, **kwargs: recorded.append((event, kwargs.get("details") or {})),
    )

    exposure.expose(DOMAIN, allow_ips=["203.0.113.7"], force_ens=True)

    assert nginx.site_exists(DOMAIN)
    assert recorded == [
        (
            "console.expose",
            {"allow_ips": ["203.0.113.7/32"], "certificate_created": True, "forced_ens": True},
        )
    ]


def test_the_console_must_be_a_service(exposure: ConsoleExposure, service: FakeConsole) -> None:
    service.service = False

    with pytest.raises(ServiceError) as raised:
        exposure.expose(DOMAIN)

    assert "noust web enable" in (raised.value.details or "")


def test_an_applications_name_is_never_taken(
    exposure: ConsoleExposure, store: NoustStore, nginx: WebServerManager
) -> None:
    from noust.core.store import App

    store.create_app(App(domain=DOMAIN, source="https://example.com/r.git", port=3000))

    with pytest.raises(DomainError) as raised:
        exposure.expose(DOMAIN)

    assert "belongs to the application" in raised.value.message
    assert not nginx.site_exists(DOMAIN)


def test_a_site_the_operator_wrote_is_never_rewritten(
    exposure: ConsoleExposure, nginx: WebServerManager
) -> None:
    nginx.fs.write_text(
        nginx.config_path(DOMAIN), "server { server_name console.example.com; }\n", mode=0o644
    )

    with pytest.raises(SiteError) as raised:
        exposure.expose(DOMAIN)

    assert "did not write" in raised.value.message
    assert "server_name console.example.com" in site_text(nginx)


def test_only_one_name_at_a_time(exposure: ConsoleExposure) -> None:
    exposure.expose(DOMAIN)

    with pytest.raises(SiteError) as raised:
        exposure.expose("other.example.com")

    assert DOMAIN in raised.value.message


def test_a_name_that_does_not_point_here_is_refused_with_what_it_resolves_to(
    store: NoustStore,
    config_file: Path,
    nginx: WebServerManager,
    certs: FakeCerts,
    service: FakeConsole,
) -> None:
    def elsewhere(_domain: str) -> SimpleNamespace:
        return SimpleNamespace(
            points_here=False,
            resolved_addresses=("198.51.100.9",),
            expected_addresses=("203.0.113.5",),
        )

    exposure = ConsoleExposure(
        console=service,
        store=ConsoleExposureStore(store),
        nginx=nginx,
        certs=certs,  # type: ignore[arg-type]
        config=Config(),
        dns_check=elsewhere,
        second_factor=list,
    )

    with pytest.raises(DomainError) as raised:
        exposure.expose(DOMAIN)

    assert "198.51.100.9" in (raised.value.details or "")
    assert "203.0.113.5" in (raised.value.details or "")
    assert "--skip-dns-check" in (raised.value.details or "")
    assert not nginx.site_exists(DOMAIN)
    # Behind NAT the operator says so, and it goes through.
    exposure.expose(DOMAIN, skip_dns_check=True)
    assert nginx.site_exists(DOMAIN)


@pytest.mark.parametrize(
    "name", ["localhost", "203.0.113.5", "-bad.example.com", "a b.example.com", ""]
)
def test_a_name_that_cannot_be_a_public_name_is_refused(
    exposure: ConsoleExposure, name: str
) -> None:
    with pytest.raises(DomainError):
        exposure.expose(name)


def test_a_hub_has_no_nginx_to_publish_through(
    exposure: ConsoleExposure, config_file: Path
) -> None:
    config = Config()
    config.set("central.role", "hub")
    config.write()

    with pytest.raises(RoleError):
        exposure.expose(DOMAIN)
    with pytest.raises(RoleError):
        exposure.unexpose()


def test_a_preview_runs_the_checks_and_changes_nothing(
    exposure: ConsoleExposure, nginx: WebServerManager, store: NoustStore, certs: FakeCerts
) -> None:
    steps = exposure.plan(DOMAIN, allow_ips=["203.0.113.7"])

    assert any("Let's Encrypt" in step for step in steps)
    assert any("203.0.113.7/32" in step for step in steps)
    assert not nginx.site_exists(DOMAIN)
    assert ConsoleExposureStore(store).get() is None
    assert certs.obtained == []


# ---------------------------------------------------------------------------
# The allow-list, parsed
# ---------------------------------------------------------------------------


def test_the_allow_list_is_parsed_and_normalised() -> None:
    assert parse_allow_ips(["203.0.113.7", "203.0.113.0/24, 2001:db8::/32", "203.0.113.7/32"]) == [
        "203.0.113.7/32",
        "203.0.113.0/24",
        "2001:db8::/32",
    ]
    assert parse_allow_ips([]) == []


@pytest.mark.parametrize(
    "entry",
    [
        "203.0.113.7; deny",
        "203.0.113.7\ndeny all",
        "all",
        "203.0.113.999",
        "$remote_addr",
        "0.0.0.0/0",
        "::/0",
    ],
)
def test_a_bad_allow_list_entry_never_reaches_a_directive(entry: str) -> None:
    """Each entry lands in a file nginx reads as root: only an address or a network does."""
    with pytest.raises(SecurityError):
        parse_allow_ips([entry])


def test_an_allow_list_has_a_ceiling() -> None:
    with pytest.raises(SecurityError):
        parse_allow_ips([f"10.0.{i}.0/24" for i in range(65)])


# ---------------------------------------------------------------------------
# Two-factor, as the accounts say it
# ---------------------------------------------------------------------------


def account(
    name: str, *, mfa: bool, signs_in: bool = True, password: bool = True
) -> SimpleNamespace:
    return SimpleNamespace(
        username=name, has_mfa=mfa, has_password=password, can_sign_in=lambda: signs_in
    )


def test_every_account_that_can_sign_in_must_hold_a_second_factor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.core import accounts

    monkeypatch.setattr(
        accounts.AccountManager,
        "list_all",
        lambda self: [
            account("zoe", mfa=False),
            account("ana", mfa=True),
            account("old", mfa=False, signs_in=False),
            account("bob", mfa=False),
        ],
    )

    blockers = second_factor_blockers()

    assert [b.split()[2] for b in blockers] == ["bob", "zoe"]
    assert all("password alone" in b for b in blockers)
    assert not any("old" in b for b in blockers)


def test_an_account_that_signs_in_through_its_provider_alone_is_named_as_such(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.core import accounts

    monkeypatch.setattr(
        accounts.AccountManager,
        "list_all",
        lambda self: [account("sso", mfa=False, password=False)],
    )

    (blocker,) = second_factor_blockers()

    assert "its identity provider alone" in blocker and "password" not in blocker


def test_accounts_all_with_a_second_factor_leave_nothing_in_the_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.core import accounts

    monkeypatch.setattr(
        accounts.AccountManager, "list_all", lambda self: [account("ana", mfa=True)]
    )

    assert second_factor_blockers() == []


def test_before_any_account_the_access_tokens_own_second_factor_is_what_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.core import accounts

    monkeypatch.setattr(accounts.AccountManager, "list_all", lambda self: [])
    monkeypatch.setattr(console_exposure, "_master_second_factor", lambda: False)
    blockers = second_factor_blockers()
    assert len(blockers) == 1 and "No account exists" in blockers[0]

    monkeypatch.setattr(console_exposure, "_master_second_factor", lambda: True)
    assert second_factor_blockers() == []


# ---------------------------------------------------------------------------
# The console's own restart, and the command
# ---------------------------------------------------------------------------


def test_a_deferred_restart_is_a_timer_unit_so_the_request_can_be_answered(
    runner: FakeRunner,
) -> None:
    SystemConsole(runner).restart("later")

    assert runner.ran("systemd-run")
    argv = runner.calls_to("systemd-run")[0]
    assert any(part.startswith("--on-active=") for part in argv)
    assert argv[-3:] == ("systemctl", "try-restart", "noust-web.service")


def test_no_restart_is_asked_for_when_the_caller_restarts_it(runner: FakeRunner) -> None:
    SystemConsole(runner).restart("never")

    assert runner.calls == []


class CommandPlan:
    """What the CLI's manager would do, scripted."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.exposed = SimpleNamespace(domain=DOMAIN)

    def plan(self, domain: str, **kwargs: Any) -> list[str]:
        self.calls.append(("plan", {"domain": domain, **kwargs}))
        return ["write the nginx site", "obtain a certificate"]

    def expose(self, domain: str, **kwargs: Any) -> Any:
        self.calls.append(("expose", {"domain": domain, **kwargs}))
        return SimpleNamespace(
            url=f"https://{domain}",
            allow_ips=list(kwargs["allow_ips"]),
            notes=["Keep the access token offline."],
            to_dict=lambda: {"domain": domain, "url": f"https://{domain}"},
        )

    def state(self) -> Any:
        return None


@pytest.fixture
def command_manager(monkeypatch: pytest.MonkeyPatch) -> CommandPlan:
    manager = CommandPlan()
    monkeypatch.setattr(console_cmd, "_manager", lambda ctx: manager)
    return manager


def test_the_command_confirms_then_publishes(command_manager: CommandPlan) -> None:
    result = CliRunner().invoke(
        root_cli,
        ["console", "expose", "--domain", DOMAIN, "--allow-ip", "203.0.113.0/24"],
        input="y\n",
    )

    assert result.exit_code == 0, result.output
    assert "write the nginx site" in result.output
    assert f"https://{DOMAIN}" in result.output
    assert [name for name, _ in command_manager.calls] == ["plan", "expose"]
    assert command_manager.calls[1][1]["allow_ips"] == ("203.0.113.0/24",)
    assert command_manager.calls[1][1]["restart"] == "now"


def test_the_command_publishes_nothing_when_the_operator_says_no(
    command_manager: CommandPlan,
) -> None:
    result = CliRunner().invoke(root_cli, ["console", "expose", "--domain", DOMAIN], input="n\n")

    assert result.exit_code == 0
    assert [name for name, _ in command_manager.calls] == ["plan"]
    assert "Cancelled" in result.output


def test_a_dry_run_only_says_what_it_would_do(command_manager: CommandPlan) -> None:
    result = CliRunner().invoke(
        root_cli, ["--dry-run", "console", "expose", "--domain", DOMAIN, "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert "would write the nginx site" in result.output
    assert [name for name, _ in command_manager.calls] == ["plan"]


def test_json_output_is_the_result(command_manager: CommandPlan) -> None:
    result = CliRunner().invoke(
        root_cli, ["--json", "console", "expose", "--domain", DOMAIN, "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output.strip().splitlines()[-1])["url"] == f"https://{DOMAIN}"


def test_forcing_ens_asks_for_a_confirmation_of_its_own(command_manager: CommandPlan) -> None:
    refused = CliRunner().invoke(
        root_cli,
        ["console", "expose", "--domain", DOMAIN, "--allow-ip", "203.0.113.7", "--force-ens"],
        input="n\n",
    )
    assert "the console stays on loopback" in refused.output
    assert [name for name, _ in command_manager.calls] == ["plan"]

    accepted = CliRunner().invoke(
        root_cli,
        ["console", "expose", "--domain", DOMAIN, "--allow-ip", "203.0.113.7", "--force-ens"],
        input="y\ny\n",
    )
    assert accepted.exit_code == 0, accepted.output
    assert command_manager.calls[-1][1]["force_ens"] is True


def test_a_hub_refuses_the_whole_group(monkeypatch: pytest.MonkeyPatch, config_file: Path) -> None:
    config = Config()
    config.set("central.role", "hub")
    config.write()

    result = CliRunner().invoke(root_cli, ["console", "status"])

    assert isinstance(result.exception, RoleError)
    assert "not available on this central" in result.exception.message
