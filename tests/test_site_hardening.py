# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The nginx and Apache sites Noust writes stop announcing themselves and send
the headers a current scan expects (ENS G20).

- ``server_tokens off`` in every nginx ``server`` block (and ``ServerTokens
  Prod`` / ``ServerSignature Off`` in every Apache site file), so the web
  server's version is not in a header or an error page.
- ``Strict-Transport-Security`` only on the HTTPS side of a site, and only
  when ``ssl.hsts`` asked for it: browsers remember it for a year.
- No ``X-XSS-Protection``: browsers ignore it, and where they do not (old ones)
  it introduced XSS of its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import HSTS_VALUE, hsts_header

TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "noust" / "templates"

PROXY = {"ssl": False, "port": 3000}
PROXY_SSL = {"ssl": True, "port": 3000}
STATIC_SSL = {"ssl": True, "static_dir": "/var/www/apps/example.com/dist"}
REDIRECTS_SSL = {
    "ssl": True,
    "port": 3000,
    "redirect_domains": ["www.example.com", "old.example.org"],
}


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """
    Point the configuration at a sandbox file the tests write.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The configuration file's path; it does not exist yet.
    """
    path = tmp_path / "etc" / "noust" / "config.yaml"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


def hsts_on(path: Path) -> None:
    """
    Turn ``ssl.hsts`` on in the sandbox configuration.

    Args:
        path: The configuration file.
    """
    path.write_text(yaml.safe_dump({"ssl": {"hsts": True}}), encoding="utf-8")
    Config.reset_instance()


def render(backend: str, template: str, context: dict[str, Any]) -> str:
    """
    Render a site template the way the manager does.

    Args:
        backend: ``nginx`` or ``apache``.
        template: Template name.
        context: Variables merged over the defaults.

    Returns:
        The configuration text.
    """
    manager = NginxManager if backend == "nginx" else ApacheManager
    return manager(runner=FakeRunner()).render_config("example.com", template, context)


# -- server_tokens off ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "context"),
    [
        ("proxy", PROXY),
        ("proxy", PROXY_SSL),
        ("proxy", REDIRECTS_SSL),
        ("static", PROXY),
        ("static", STATIC_SSL),
        ("fastcgi", {"ssl": True, "php_socket": "/run/php/x.sock"}),
    ],
)
def test_every_nginx_server_block_hides_the_version(
    config_file: Path, template: str, context: dict[str, Any]
) -> None:
    rendered = render("nginx", template, context)

    servers = len(re.findall(r"(?m)^server \{", rendered))
    assert servers >= 1
    assert len(re.findall(r"(?m)^    server_tokens off;$", rendered)) == servers


def test_the_hooks_site_hides_the_version_too(config_file: Path) -> None:
    rendered = render("nginx", "hooks", {"ssl": True})

    assert rendered.count("server_tokens off;") == len(re.findall(r"(?m)^server \{", rendered))


@pytest.mark.parametrize("template", ["proxy", "static"])
def test_every_apache_site_file_hides_the_version(config_file: Path, template: str) -> None:
    context = PROXY_SSL if template == "proxy" else STATIC_SSL

    rendered = render("apache", template, context)

    assert "\nServerTokens Prod\n" in rendered
    assert "\nServerSignature Off\n" in rendered
    # Server-wide directives sit outside every virtual host.
    assert rendered.index("ServerTokens Prod") < rendered.index("<VirtualHost")


def test_the_monorepo_template_hides_the_version_and_can_send_hsts() -> None:
    from jinja2 import Template

    text = (TEMPLATES / "nginx" / "monorepo.conf.j2").read_text()
    workspaces = [type("W", (), {"subdomain": "api"})(), type("W", (), {"subdomain": "web"})()]

    rendered = Template(text).render(
        domain="example.com",
        workspaces=workspaces,
        ssl=True,
        primary_subdomain="web",
        hsts=HSTS_VALUE,
    )

    servers = len(re.findall(r"(?m)^server \{", rendered))
    assert rendered.count("server_tokens off;") == servers
    assert rendered.count(f'Strict-Transport-Security "{HSTS_VALUE}"') == len(workspaces)
    assert "X-XSS-Protection" not in rendered


# -- no X-XSS-Protection -------------------------------------------------------------


def test_no_template_sends_x_xss_protection() -> None:
    offenders = [
        str(path.relative_to(TEMPLATES))
        for path in TEMPLATES.rglob("*.j2")
        if "X-XSS-Protection" in path.read_text()
    ]

    assert offenders == []


def test_the_advanced_default_headers_no_longer_include_it() -> None:
    from noust.deployers.helpers.nginx_config import NginxConfigBuilder

    source = Path(NginxConfigBuilder.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).resolve().parent.parent / "src" / source).read_text()

    assert "X-XSS-Protection" not in text


# -- HSTS ----------------------------------------------------------------------------


def test_hsts_is_off_by_default(config_file: Path) -> None:
    for backend, template in (("nginx", "proxy"), ("apache", "proxy"), ("nginx", "static")):
        assert "Strict-Transport-Security" not in render(
            backend, template, PROXY_SSL if template == "proxy" else STATIC_SSL
        )


def test_the_header_value_is_empty_until_the_flag_is_on(config_file: Path) -> None:
    assert hsts_header(Config()) == ""

    hsts_on(config_file)

    assert hsts_header(Config()) == HSTS_VALUE == "max-age=31536000"


@pytest.mark.parametrize(
    ("backend", "template", "context", "line"),
    [
        (
            "nginx",
            "proxy",
            PROXY_SSL,
            f'add_header Strict-Transport-Security "{HSTS_VALUE}" always;',
        ),
        (
            "nginx",
            "static",
            STATIC_SSL,
            f'add_header Strict-Transport-Security "{HSTS_VALUE}" always;',
        ),
        (
            "apache",
            "proxy",
            PROXY_SSL,
            f'Header always set Strict-Transport-Security "{HSTS_VALUE}"',
        ),
        (
            "apache",
            "static",
            STATIC_SSL,
            f'Header always set Strict-Transport-Security "{HSTS_VALUE}"',
        ),
    ],
)
def test_hsts_is_sent_once_the_flag_is_on(
    config_file: Path, backend: str, template: str, context: dict[str, Any], line: str
) -> None:
    hsts_on(config_file)

    rendered = render(backend, template, context)

    assert rendered.count(line) == 1


@pytest.mark.parametrize(
    ("backend", "template"), [("nginx", "proxy"), ("nginx", "static"), ("apache", "proxy")]
)
def test_hsts_is_never_sent_over_plain_http(config_file: Path, backend: str, template: str) -> None:
    hsts_on(config_file)
    context = {"ssl": False, "port": 3000, "static_dir": "/var/www/apps/example.com/dist"}

    assert "Strict-Transport-Security" not in render(backend, template, context)


def test_hsts_sits_in_the_https_server_block_only(config_file: Path) -> None:
    hsts_on(config_file)

    rendered = render("nginx", "proxy", PROXY_SSL)

    http_block, https_block = rendered.split("listen 443 ssl http2;")
    assert "Strict-Transport-Security" not in http_block
    assert "Strict-Transport-Security" in https_block


def test_the_flag_is_a_documented_default_off() -> None:
    from noust.core.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["ssl"]["hsts"] is False


# -- the advanced (wasm.nginx.yaml) site ---------------------------------------------


def _advanced(builder_config: Any) -> dict[str, Any]:
    """
    Build the context of an advanced site the way the builder does.

    Args:
        builder_config: An :class:`NginxAdvancedConfig`.

    Returns:
        The template context, TLS on.
    """
    from noust.deployers.helpers.nginx_config import NginxConfigBuilder

    return NginxConfigBuilder().build_context(builder_config, "example.com", ssl=True)


def test_an_advanced_site_hides_the_version_and_does_not_send_x_xss_protection(
    config_file: Path,
) -> None:
    from noust.deployers.helpers.nginx_config import NginxAdvancedConfig, NginxRoute

    config = NginxAdvancedConfig(routes=[NginxRoute(path="/", upstream_port=3000)])

    rendered = render("nginx", "advanced", _advanced(config))

    servers = len(re.findall(r"(?m)^server \{", rendered))
    assert rendered.count("server_tokens off;") == servers
    assert "X-XSS-Protection" not in rendered
    assert "Strict-Transport-Security" not in rendered


def test_an_advanced_site_sends_hsts_over_tls_once_asked(config_file: Path) -> None:
    from noust.deployers.helpers.nginx_config import NginxAdvancedConfig, NginxRoute

    hsts_on(config_file)
    config = NginxAdvancedConfig(routes=[NginxRoute(path="/", upstream_port=3000)])

    rendered = render("nginx", "advanced", _advanced(config))

    assert rendered.count(f'add_header Strict-Transport-Security "{HSTS_VALUE}" always;') == 1
