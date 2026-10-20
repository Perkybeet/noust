# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application can be left to answer over plain HTTP too (the Coolify and Dokploy "force HTTPS" switch).

Noust redirected every request on port 80 to HTTPS the moment a site had a certificate, and had no
way to say that one application must not (an old client that cannot speak TLS 1.2, a health probe
from a device without a trust store, a vendor webhook that refuses redirects). What is pinned: the
default is unchanged, a policy that does not force HTTPS makes the nginx site one server on ports 80
and 443 with no redirect, the ACME challenge and the password are still sane in it, HSTS is never
sent for a site that also answers over HTTP, Apache says it is nginx's, and the API and the command
carry the flag.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.deployers import access
from noust.managers.site_policy import SitePolicy, SitePolicyManager, response_headers, validated
from noust.web.api import site_policy as site_policy_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth
from tests.test_domains import Machine, certs, machine, store, web
from tests.test_site_policy import served

FIXTURES = (certs, machine, served, store, web)


class TestThePolicy:
    def test_https_is_forced_unless_the_policy_says_otherwise(self) -> None:
        assert SitePolicy().force_https is True
        assert SitePolicy().to_dict()["force_https"] is True
        assert SitePolicy().is_default

    def test_a_policy_that_does_not_force_it_is_not_the_default(self) -> None:
        policy = validated(SitePolicy(), {"force_https": False})

        assert policy.force_https is False
        assert policy.to_dict()["force_https"] is False
        assert not policy.is_default

    def test_the_value_is_a_boolean(self) -> None:
        with pytest.raises(ValidationError) as raised:
            validated(SitePolicy(), {"force_https": "no"})

        assert raised.value.field == "force_https"

    def test_hsts_promises_a_browser_https_only_so_it_cannot_go_with_http(self) -> None:
        with pytest.raises(ValidationError) as raised:
            validated(SitePolicy(hsts=True), {"force_https": False})

        assert raised.value.field == "hsts"
        assert "HTTP" in raised.value.message

    def test_a_site_that_also_answers_over_http_sends_no_hsts_whatever_the_server_says(
        self,
    ) -> None:
        headers = dict(
            response_headers(SitePolicy(force_https=False), hsts="max-age=31536000", ssl=True)
        )

        assert "Strict-Transport-Security" not in headers

    def test_a_forced_site_still_sends_it(self) -> None:
        headers = dict(response_headers(SitePolicy(), hsts="max-age=31536000", ssl=True))

        assert headers["Strict-Transport-Security"] == "max-age=31536000"

    def test_a_stored_document_without_the_field_is_forced(self) -> None:
        assert SitePolicy.from_document(json.dumps({"noindex": True})).force_https is True


class TestTheNginxSite:
    def test_the_default_redirects_to_https(self, machine: Machine, served: NoustStore) -> None:
        machine.deploy("tls.example.com", app_type="nodejs", tls=True)

        config = machine.config("tls.example.com")

        assert "return 301 https://$host$request_uri;" in config
        assert config.count("server {") == 2  # the redirect and the HTTPS server

    def test_a_policy_that_does_not_force_it_is_one_server_on_both_ports(
        self, machine: Machine, served: NoustStore
    ) -> None:
        machine.deploy("tls.example.com", app_type="nodejs", tls=True)

        access.set_policy("tls.example.com", {"force_https": False})

        config = machine.config("tls.example.com")
        assert "return 301 https://$host$request_uri;" not in config
        assert config.count("server {") == 1
        assert "listen 80;" in config
        assert "listen 443 ssl http2;" in config
        assert "ssl_certificate " in config
        assert "proxy_pass http://127.0.0.1:3000;" in config

    def test_putting_it_back_redirects_again(self, machine: Machine, served: NoustStore) -> None:
        machine.deploy("tls.example.com", app_type="nodejs", tls=True)
        access.set_policy("tls.example.com", {"force_https": False})

        access.set_policy("tls.example.com", {"force_https": True})

        assert "return 301 https://$host$request_uri;" in machine.config("tls.example.com")

    @pytest.mark.parametrize("app_type", ["static", "nodejs"])
    def test_static_and_proxied_sites_both_follow_it(
        self, machine: Machine, served: NoustStore, app_type: str
    ) -> None:
        machine.deploy("kind.example.com", app_type=app_type, tls=True)

        access.set_policy("kind.example.com", {"force_https": False})

        config = machine.config("kind.example.com")
        assert "return 301 https://$host$request_uri;" not in config
        assert "listen 443 ssl http2;" in config

    def test_the_acme_challenge_is_open_over_http_even_with_a_password(
        self, machine: Machine, served: NoustStore
    ) -> None:
        """The challenge is asked on port 80 with no credentials; the same server now asks for them."""
        machine.deploy("tls.example.com", app_type="nodejs", tls=True)
        policies = SitePolicyManager()
        policies.records.set_user(
            "tls.example.com", "ana", "$6$salt$" + "a" * 86, created_by="test"
        )

        access.set_policy("tls.example.com", {"force_https": False, "basic_auth": True})

        config = machine.config("tls.example.com")
        challenge = config.split("location /.well-known/acme-challenge/", 1)[1].split("}", 1)[0]
        assert "allow all;" in challenge
        assert "auth_basic off;" in challenge

    def test_a_site_without_a_certificate_has_nothing_to_force(
        self, machine: Machine, served: NoustStore
    ) -> None:
        machine.deploy("plain.example.com", app_type="nodejs")

        access.set_policy("plain.example.com", {"force_https": False})

        config = machine.config("plain.example.com")
        assert "listen 443" not in config
        assert "listen 80;" in config


class TestApache:
    def test_apache_says_it_is_nginxs(self, machine: Machine, served: NoustStore) -> None:
        app = served.get_app("example.com")
        assert app is not None
        app.webserver = "apache"
        served.update_app(app)

        with pytest.raises(ValidationError) as raised:
            access.set_policy("example.com", {"force_https": False})

        assert raised.value.field == "force_https"
        assert "nginx" in raised.value.message
        assert "Move it to nginx" in (raised.value.details or "")

    def test_apache_can_still_be_told_to_force_it(
        self, machine: Machine, served: NoustStore
    ) -> None:
        app = served.get_app("example.com")
        assert app is not None
        app.webserver = "apache"
        served.update_app(app)

        access.set_policy("example.com", {"force_https": True})


class TestTheSurfaces:
    @pytest.fixture
    def client(self, machine: Machine, served: NoustStore) -> TestClient:
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(site_policy_api.router, prefix="/api/apps")
        app.dependency_overrides[require_auth] = lambda: {
            "sid": "token",
            "scope": "admin",
            "source": "bearer",
            "type": "api_token",
        }
        return TestClient(app)

    def test_the_api_reads_and_changes_it(self, client: TestClient) -> None:
        before = client.get("/api/apps/example.com/policy").json()
        assert before["policy"]["force_https"] is True

        response = client.put("/api/apps/example.com/policy", json={"force_https": False})

        assert response.status_code == 200, response.text
        assert response.json()["policy"]["force_https"] is False

    def test_the_command_has_the_two_spellings(self, machine: Machine, served: NoustStore) -> None:
        off = CliRunner().invoke(
            root_cli, ["app", "policy", "set", "example.com", "--no-force-https"]
        )
        shown = CliRunner().invoke(root_cli, ["app", "policy", "show", "example.com", "--json"])

        assert off.exit_code == 0, off.output
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.output)["policy"]["force_https"] is False

        on = CliRunner().invoke(root_cli, ["app", "policy", "set", "example.com", "--force-https"])
        assert on.exit_code == 0, on.output
        assert access.describe("example.com").policy.force_https is True
