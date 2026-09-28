# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``wasm web expose-hooks`` and ``wasm github``.

The hooks site is WASM's own nginx site on a dedicated name: it forwards
``/hooks/`` to the console on loopback and nothing else, refuses a name that
belongs to an application or to a site it did not write, and records the
public URL the GitHub App uses.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from wasm.cli.app import cli
from wasm.core.exceptions import CertificateError, DomainError, SiteError
from wasm.core.runner import FakeRunner
from wasm.core.store import App, WASMStore
from wasm.integrations import hooks_site
from wasm.managers.webserver import NGINX_BACKEND, WebServerManager

DOMAIN = "hooks.example.com"


class FakeCerts:
    """A certificate manager that issues on paper."""

    def __init__(self, installed: bool = True, fail: bool = False) -> None:
        self.installed = installed
        self.fail = fail
        self.obtained: list[str] = []
        self.deleted: list[str] = []

    def is_installed(self) -> bool:
        return self.installed

    def cert_exists(self, domain: str) -> bool:
        return domain in self.obtained

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
        return True


@pytest.fixture
def nginx(tmp_path: Path, runner: FakeRunner, store: WASMStore) -> WebServerManager:
    """
    An nginx manager over a temporary tree.

    Returns:
        The manager.
    """
    return WebServerManager(
        replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """
    Capture the recorded hooks URL instead of writing /etc/wasm/config.yaml.

    Returns:
        Each value recorded.
    """
    values: list[str | None] = []
    monkeypatch.setattr(hooks_site, "save_hooks_url", values.append)
    monkeypatch.setattr(hooks_site, "public_hooks_url", lambda: values[-1] if values else None)
    return values


def test_the_site_forwards_only_hooks_to_loopback(
    nginx: WebServerManager, saved: list[str | None], runner: FakeRunner
) -> None:
    """/hooks/ to the console; everything else 404; TLS once certified."""
    certs = FakeCerts()
    result = hooks_site.expose(DOMAIN, port=9090, manager=nginx, cert_manager=certs)
    text = nginx.get_site_config(DOMAIN) or ""
    assert text.startswith(hooks_site.MARKER)
    assert "location /hooks/ {\n        proxy_pass http://127.0.0.1:9090;" in text
    assert "location / {\n        return 404;" in text
    assert "listen 443 ssl" in text
    assert "/etc/letsencrypt/live/hooks.example.com/fullchain.pem" in text
    assert "proxy_ssl_verify" not in text
    assert result.hooks_url == "https://hooks.example.com/hooks"
    assert result.ssl_enabled and certs.obtained == [DOMAIN]
    assert saved == ["https://hooks.example.com/hooks"]
    assert result.github_webhook is None
    assert nginx.site_enabled(DOMAIN)
    assert runner.ran("systemctl", "reload", "nginx")


def test_a_console_serving_tls_is_reached_over_https(
    nginx: WebServerManager, saved: list[str | None]
) -> None:
    """A self-signed console on loopback is proxied without verification."""
    hooks_site.expose(DOMAIN, port=8443, scheme="https", manager=nginx, cert_manager=FakeCerts())
    text = nginx.get_site_config(DOMAIN) or ""
    assert "proxy_pass https://127.0.0.1:8443;" in text
    assert "proxy_ssl_verify off;" in text


def test_an_applications_name_is_refused(
    nginx: WebServerManager, store: WASMStore, saved: list[str | None]
) -> None:
    """The hooks site never takes over, or edits, an application's site."""
    store.create_app(App(domain=DOMAIN, source="github:you/app"))
    with pytest.raises(DomainError, match="belongs to the application"):
        hooks_site.expose(DOMAIN, port=8080, manager=nginx, cert_manager=FakeCerts())
    assert not nginx.site_exists(DOMAIN)


def test_a_site_it_did_not_write_is_refused(
    nginx: WebServerManager, saved: list[str | None]
) -> None:
    """Somebody else's site on the name is left alone."""
    nginx.create_site(DOMAIN, template="proxy", context={"port": 3000})
    with pytest.raises(SiteError, match="did not write"):
        hooks_site.expose(DOMAIN, port=8080, manager=nginx, cert_manager=FakeCerts())
    with pytest.raises(SiteError):
        hooks_site.unexpose(DOMAIN, manager=nginx, cert_manager=FakeCerts())
    assert saved == []


def test_running_again_updates_its_own_site(
    nginx: WebServerManager, saved: list[str | None]
) -> None:
    """Exposing twice is an update, not a conflict."""
    hooks_site.expose(DOMAIN, port=8080, manager=nginx, cert_manager=FakeCerts())
    hooks_site.expose(DOMAIN, port=9000, manager=nginx, cert_manager=FakeCerts())
    assert "127.0.0.1:9000" in (nginx.get_site_config(DOMAIN) or "")


def test_a_failed_certificate_records_nothing(
    nginx: WebServerManager, saved: list[str | None]
) -> None:
    """The site stays over HTTP for a retry; the URL is not recorded."""
    with pytest.raises(CertificateError):
        hooks_site.expose(DOMAIN, port=8080, manager=nginx, cert_manager=FakeCerts(fail=True))
    assert nginx.site_exists(DOMAIN)
    assert saved == []


def test_plain_http_when_asked(nginx: WebServerManager, saved: list[str | None]) -> None:
    """--no-ssl records an http URL and says what that means."""
    result = hooks_site.expose(
        DOMAIN, port=8080, ssl=False, manager=nginx, cert_manager=FakeCerts()
    )
    assert result.hooks_url == "http://hooks.example.com/hooks"
    assert "listen 443" not in (nginx.get_site_config(DOMAIN) or "")
    assert result.notes


def test_the_github_webhook_follows(
    nginx: WebServerManager,
    github_configured: WASMStore,
    saved: list[str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an App, its webhook is pointed at the new URL and the operator told to switch it on."""
    pointed: list[str] = []

    def configure(url: str) -> bool:
        pointed.append(url)
        return False

    monkeypatch.setattr("wasm.integrations.github.service.configure_webhook", configure)
    result = hooks_site.expose(DOMAIN, port=8080, manager=nginx, cert_manager=FakeCerts())
    assert pointed == ["https://hooks.example.com/hooks/github"]
    assert result.github_webhook == "inactive"
    assert any("Active" in note for note in result.notes)


def test_removal(nginx: WebServerManager, saved: list[str | None]) -> None:
    """The site, its certificate and the recorded URL go."""
    certs = FakeCerts()
    hooks_site.expose(DOMAIN, port=8080, manager=nginx, cert_manager=certs)
    assert hooks_site.unexpose(DOMAIN, manager=nginx, cert_manager=certs) is True
    assert not nginx.site_exists(DOMAIN)
    assert certs.deleted == [DOMAIN]
    assert saved[-1] is None
    assert hooks_site.unexpose(DOMAIN, manager=nginx, cert_manager=certs) is False


# -- CLI ---------------------------------------------------------------------


def test_expose_hooks_reads_the_consoles_port_from_its_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: WASMStore
) -> None:
    """The service's own command line says where the console listens."""
    unit = tmp_path / "wasm-web.service"
    unit.write_text(
        "[Service]\nExecStart=/usr/bin/wasm web start --under-systemd --host 0.0.0.0 "
        "--port 9443 --self-signed\n"
    )
    monkeypatch.setattr("wasm.cli.commands.web._service_unit_path", lambda: unit)
    monkeypatch.setattr("wasm.cli.commands.web._service_status", lambda verbose: None)
    calls: list[dict[str, Any]] = []

    def expose(domain: str, **kwargs: Any) -> hooks_site.HooksExposure:
        calls.append({"domain": domain, **kwargs})
        return hooks_site.HooksExposure(domain=domain, hooks_url=f"https://{domain}/hooks")

    monkeypatch.setattr(hooks_site, "expose", expose)
    result = CliRunner().invoke(cli, ["web", "expose-hooks", DOMAIN, "--json"])
    assert result.exit_code == 0, result.output
    assert calls[0]["port"] == 9443 and calls[0]["scheme"] == "https" and calls[0]["ssl"] is True
    body = json.loads(result.output)
    assert body["hooks_url"] == "https://hooks.example.com/hooks"
    assert any("wasm web enable" in note for note in body["notes"])


def test_github_status_json(store: WASMStore) -> None:
    """Without an App, the status says so."""
    result = CliRunner().invoke(cli, ["github", "status", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["configured"] is False


def test_github_remove_asks_first(github_configured: WASMStore) -> None:
    """Declining keeps the App; --yes removes it and names GitHub's page."""
    declined = CliRunner().invoke(cli, ["github", "remove"], input="n\n")
    assert declined.exit_code != 0
    assert github_configured.get_github_app() is not None
    removed = CliRunner().invoke(cli, ["github", "remove", "--yes", "--json"])
    assert removed.exit_code == 0, removed.output
    assert (
        json.loads(removed.output)["settings_url"] == "https://github.com/settings/apps/wasm-test"
    )
    assert github_configured.get_github_app() is None


def test_github_setup_prints_the_manifest(store: WASMStore) -> None:
    """The manifest the console would post, for a look."""
    result = CliRunner().invoke(
        cli, ["github", "setup", "--print-manifest", "--origin", "http://localhost:9000"]
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["redirect_url"] == "http://localhost:9000/integrations/github/callback"
