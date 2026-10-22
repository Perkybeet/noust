# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The site of an application whose names do not all share one certificate.

A name that holds a certificate of its own is written as a server block pair of its own,
presenting its own lineage, and the block that serves the rest stops claiming it. Pinned here,
for both web servers and every template an application can be written with:

- the pair is written once the lineage is on disk, and not before, so a name whose order failed
  is still served (with the shared certificate) and the web server never opens a missing file;
- a redirect holds a certificate of its own the same way, since it is served over TLS too;
- a name that also has a port of its own is one pair with both;
- an application whose names all share one certificate is written byte for byte as it was.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, DomainKind, NoustStore
from noust.managers.apache_manager import ApacheManager
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import APACHE_BACKEND, NGINX_BACKEND

PRIMARY = "example.com"
SHOP = "shop.example.com"
ADMIN = "admin.example.com"
OLD = "old.example.com"

#: What the shared server blocks present: a site names it without asking where certbot keeps its files.
SHARED_CHAIN = f"/etc/letsencrypt/live/{PRIMARY}/fullchain.pem"


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store with an application that answers on a few names."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain=PRIMARY, app_type="nodejs", port=3000))
    instance.add_domain(PRIMARY, SHOP, DomainKind.ALIAS.value)
    instance.add_domain(PRIMARY, ADMIN, DomainKind.ALIAS.value)
    instance.add_domain(PRIMARY, OLD, DomainKind.REDIRECT.value)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def letsencrypt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """certbot's directory, in the test's own tree."""
    directory = tmp_path / "letsencrypt"
    monkeypatch.setattr(CertManager, "LETSENCRYPT_DIR", directory)
    monkeypatch.setattr(CertManager, "LIVE_DIR", directory / "live")
    return directory


@pytest.fixture
def nginx(tmp_path: Path, runner: FakeRunner, store: NoustStore, letsencrypt: Path) -> NginxManager:
    """An nginx manager writing into a temporary configuration tree."""
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


@pytest.fixture
def apache(
    tmp_path: Path, runner: FakeRunner, store: NoustStore, letsencrypt: Path
) -> ApacheManager:
    """An apache manager writing into a temporary configuration tree."""
    return ApacheManager(
        backend=replace(
            APACHE_BACKEND,
            sites_available=tmp_path / "apache/sites-available",
            sites_enabled=tmp_path / "apache/sites-enabled",
        )
    )


def lineage(letsencrypt: Path, name: str) -> Path:
    """Put the files of a live lineage on disk and return its directory."""
    live = letsencrypt / "live" / name
    live.mkdir(parents=True, exist_ok=True)
    for file in ("fullchain.pem", "privkey.pem", "cert.pem", "chain.pem"):
        (live / file).write_text("-----BEGIN CERTIFICATE-----\n")
    return live


def nginx_blocks(config: str) -> list[str]:
    """The ``server { }`` blocks of an nginx site, outermost only."""
    return re.split(r"(?m)^(?=server \{)", config)[1:]


def apache_blocks(config: str) -> list[str]:
    """The ``<VirtualHost>`` blocks of an apache site."""
    return re.split(r"(?m)^(?=<VirtualHost )", config)[1:]


def serving(blocks: list[str], name: str, directive: str) -> list[str]:
    """The blocks that answer on a name, narrowed to the value of one directive."""
    found = []
    for block in blocks:
        names = re.findall(r"(?m)^\s*(?:server_name|ServerName|ServerAlias)\s+(.+?);?\s*$", block)
        if any(name in line.split() for line in names):
            found.extend(re.findall(rf"(?m)^\s*{directive}\s+(\S+?);?\s*$", block))
    return found


def render(web: Any, template: str = "proxy", **context: Any) -> str:
    """Write the site of the application and read it back."""
    web.create_site(PRIMARY, template=template, context={"port": 3000, **context})
    return web.config_path(PRIMARY).read_text()


class TestNginx:
    def test_without_a_name_of_its_own_nothing_is_set_apart(
        self, nginx: NginxManager, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)

        config = render(nginx, ssl=True)

        shared = SHARED_CHAIN
        assert serving(nginx_blocks(config), ADMIN, "ssl_certificate") == [shared]
        assert f"server_name {PRIMARY} {SHOP} {ADMIN};" in config

    def test_a_name_with_a_certificate_of_its_own_is_one_pair_presenting_it(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")

        config = render(nginx, ssl=True)

        blocks = nginx_blocks(config)
        assert serving(blocks, ADMIN, "ssl_certificate") == [
            f"{letsencrypt}/live/{ADMIN}/fullchain.pem"
        ]
        assert serving(blocks, ADMIN, "ssl_certificate_key") == [
            f"{letsencrypt}/live/{ADMIN}/privkey.pem"
        ]
        # The rest keep the shared one, and nobody claims the name twice.
        assert serving(blocks, SHOP, "ssl_certificate") == [SHARED_CHAIN]
        assert f"server_name {PRIMARY} {SHOP};" in config
        assert config.count(f"server_name {ADMIN};") == 2

    def test_its_port_80_block_keeps_the_challenge_so_the_name_can_be_proven_and_renewed(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")

        blocks = nginx_blocks(render(nginx, ssl=True))

        port_80 = [b for b in blocks if f"server_name {ADMIN};" in b and "listen 80;" in b]
        assert len(port_80) == 1
        assert "location /.well-known/acme-challenge/" in port_80[0]
        assert "return 301 https://$host$request_uri;" in port_80[0]

    def test_it_proxies_to_the_application_like_the_rest(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")

        blocks = nginx_blocks(render(nginx, ssl=True))

        own = next(b for b in blocks if f"server_name {ADMIN};" in b and "listen 443" in b)
        assert "proxy_pass http://127.0.0.1:3000;" in own

    def test_with_a_port_of_its_own_the_pair_has_both(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        store.set_domain_port(PRIMARY, ADMIN, 9000)

        blocks = nginx_blocks(render(nginx, ssl=True))

        own = next(b for b in blocks if f"server_name {ADMIN};" in b and "listen 443" in b)
        assert "proxy_pass http://127.0.0.1:9000;" in own
        assert f"{letsencrypt}/live/{ADMIN}/fullchain.pem" in own
        assert sum(f"server_name {ADMIN};" in b for b in blocks) == 2

    def test_a_name_whose_certificate_is_not_on_disk_is_still_served_with_the_shared_one(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        """The order failed or has not run: nginx must never be pointed at a file it lacks."""
        lineage(letsencrypt, PRIMARY)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")

        config = render(nginx, ssl=True)

        assert f"server_name {PRIMARY} {SHOP} {ADMIN};" in config
        assert f"live/{ADMIN}/" not in config

    def test_a_redirect_with_a_certificate_of_its_own_presents_it(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, OLD)
        store.set_domain_certificate(PRIMARY, OLD, "own")

        blocks = nginx_blocks(render(nginx, ssl=True))

        assert serving(blocks, OLD, "ssl_certificate") == [
            f"{letsencrypt}/live/{OLD}/fullchain.pem"
        ]
        assert any(
            f"server_name {OLD};" in b and f"return 301 https://{PRIMARY}$request_uri;" in b
            for b in blocks
        )

    def test_a_redirect_without_one_keeps_the_shared_certificate(
        self, nginx: NginxManager, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)

        blocks = nginx_blocks(render(nginx, ssl=True))

        assert serving(blocks, OLD, "ssl_certificate") == [SHARED_CHAIN]

    def test_a_site_without_tls_has_nothing_to_present_and_nothing_is_set_apart_to_it(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")

        config = render(nginx, ssl=False)

        assert "ssl_certificate" not in config
        assert f"server_name {PRIMARY} {SHOP};" in config
        assert f"server_name {ADMIN};" in config

    def test_a_name_that_is_switched_off_is_not_served_whatever_it_holds(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        store.set_domain_enabled(PRIMARY, ADMIN, False)

        config = render(nginx, ssl=True)

        assert ADMIN not in config

    def test_the_canonical_pair_is_served_together_and_keeps_the_shared_certificate(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path, tmp_path: Path
    ) -> None:
        from noust.managers.site_policy import SitePolicyManager

        store.add_domain(PRIMARY, f"www.{PRIMARY}", DomainKind.REDIRECT.value)
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, f"www.{PRIMARY}")
        store.set_domain_certificate(PRIMARY, f"www.{PRIMARY}", "own")
        manager = SitePolicyManager()
        manager.save(PRIMARY, replace(manager.get(PRIMARY), canonical="www"), actor=None)

        config = render(nginx, ssl=True)

        assert f"live/www.{PRIMARY}/" not in config

    def test_a_certificate_for_a_name_with_a_stranger_in_it_never_reaches_the_file(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")

        with pytest.raises(Exception, match="Invalid domain"):
            nginx.render_config(
                PRIMARY,
                "proxy",
                {
                    "ssl": True,
                    "extra_servers": [{"names": "evil.example.com;", "port": None}],
                },
            )


class TestOtherTemplates:
    @pytest.mark.parametrize("template", ["static", "fastcgi"])
    def test_static_and_php_sites_do_the_same(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path, template: str
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        context: dict[str, Any] = {"ssl": True, "app_path": "/srv/app", "fastcgi_socket": "/run/x"}

        config = render(nginx, template, **context)

        blocks = nginx_blocks(config)
        assert serving(blocks, ADMIN, "ssl_certificate") == [
            f"{letsencrypt}/live/{ADMIN}/fullchain.pem"
        ]
        assert serving(blocks, SHOP, "ssl_certificate") == [SHARED_CHAIN]
        own = next(b for b in blocks if f"server_name {ADMIN};" in b and "listen 443" in b)
        assert "/.well-known/" in own

    def test_a_monorepo_writes_one_certificate_for_everything_and_sets_nothing_apart(
        self, nginx: NginxManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        names = nginx._application_names(PRIMARY, "monorepo")

        assert names["server_names"] == f"{PRIMARY} {SHOP} {ADMIN}"
        assert names["extra_servers"] == []
        assert names["redirect_certificates"] == {}


class TestApache:
    def test_a_name_with_a_certificate_of_its_own_is_a_pair_of_virtual_hosts_presenting_it(
        self, apache: ApacheManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")

        config = render(apache, ssl=True)

        blocks = apache_blocks(config)
        assert serving(blocks, ADMIN, "SSLCertificateFile") == [
            f"{letsencrypt}/live/{ADMIN}/fullchain.pem"
        ]
        assert serving(blocks, SHOP, "SSLCertificateFile") == [SHARED_CHAIN]
        assert "ServerAlias shop.example.com" in config
        assert "ServerAlias shop.example.com admin.example.com" not in config

    @pytest.mark.parametrize("template", ["static", "fastcgi"])
    def test_static_and_php_sites_do_the_same(
        self, apache: ApacheManager, store: NoustStore, letsencrypt: Path, template: str
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        context: dict[str, Any] = {
            "ssl": True,
            "app_path": "/srv/app",
            "document_root": "/srv/app",
            "max_upload_bytes": 1000,
            "deny_patterns": [],
            "fastcgi_socket": "/run/x",
        }

        config = render(apache, template, **context)

        blocks = apache_blocks(config)
        assert serving(blocks, ADMIN, "SSLCertificateFile") == [
            f"{letsencrypt}/live/{ADMIN}/fullchain.pem"
        ]

    def test_a_redirect_with_a_certificate_of_its_own_presents_it(
        self, apache: ApacheManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, OLD)
        store.set_domain_certificate(PRIMARY, OLD, "own")

        blocks = apache_blocks(render(apache, ssl=True))

        assert serving(blocks, OLD, "SSLCertificateFile") == [
            f"{letsencrypt}/live/{OLD}/fullchain.pem"
        ]

    def test_with_a_port_of_its_own_the_virtual_host_has_both(
        self, apache: ApacheManager, store: NoustStore, letsencrypt: Path
    ) -> None:
        lineage(letsencrypt, PRIMARY)
        lineage(letsencrypt, ADMIN)
        store.set_domain_certificate(PRIMARY, ADMIN, "own")
        store.set_domain_port(PRIMARY, ADMIN, 9000)

        config = render(apache, ssl=True)

        assert "ProxyPass / http://127.0.0.1:9000/" in config
        assert "ProxyPass / http://127.0.0.1:3000/" in config
