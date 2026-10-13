"""
The Apache virtual host for PHP-FPM applications (checklist A-37).

PHP was served by nginx only. The Apache site proxies ``.php`` to the application's pool with
``mod_proxy_fcgi``, makes ``index.php`` the front controller with ``FallbackResource``, and
carries the refusals nginx's locations make. What the tests pin is what an operator would
otherwise find out from an exposed ``.env``: every refusal is present, the handler is the pool's
own socket, ``.htaccess`` is not read, the upload limit is in bytes, and the module the site
needs is enabled when the site is written.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.managers.apache_manager import APACHE_BACKEND, ApacheManager
from tests.test_webserver_managers import FakeStore

CONTEXT: dict[str, Any] = {
    "document_root": "/var/www/apps/example-com/current",
    "fastcgi_socket": "/run/php/noust-example-com.sock",
    "max_upload": "64m",
    "max_upload_bytes": 64 * 1024**2,
    "deny_paths": ["wp-config.php", "readme.html"],
    "deny_patterns": [r"wp-config\.php", r"readme\.html"],
}


@pytest.fixture
def runner() -> FakeRunner:
    """
    Returns:
        A runner that records the ``a2enmod`` calls.
    """
    return FakeRunner()


@pytest.fixture
def apache(
    tmp_path: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ApacheManager]:
    """
    An Apache manager over a temporary tree and an in-memory store.

    Args:
        tmp_path: Per-test directory.
        runner: What records the commands.
        monkeypatch: Patching helper.

    Yields:
        The manager.
    """
    fake = FakeStore()
    monkeypatch.setattr("noust.managers.webserver.get_store", lambda: fake)
    monkeypatch.setattr("noust.managers.cert_manager.get_store", lambda: fake)
    yield ApacheManager(
        runner=runner,
        backend=replace(
            APACHE_BACKEND,
            sites_available=tmp_path / "apache/sites-available",
            sites_enabled=tmp_path / "apache/sites-enabled",
        ),
    )


def render(apache: ApacheManager, **extra: Any) -> str:
    """
    Args:
        apache: The manager.
        **extra: Context over :data:`CONTEXT`.

    Returns:
        The rendered virtual host.
    """
    return apache.render_config("example.com", "fastcgi", {**CONTEXT, **extra})


def test_the_handler_is_the_applications_own_pool(apache: ApacheManager) -> None:
    text = render(apache)

    assert 'SetHandler "proxy:unix:/run/php/noust-example-com.sock|fcgi://localhost"' in text
    assert "DocumentRoot /var/www/apps/example-com/current" in text
    assert "DirectoryIndex index.php index.html" in text
    assert "FallbackResource /index.php" in text


def test_a_htaccess_is_never_read_and_nothing_is_listed(apache: ApacheManager) -> None:
    text = render(apache)

    assert "AllowOverride None" in text
    assert "AllowOverride All" not in text
    assert "Options -Indexes +FollowSymLinks" in text


def test_the_upload_limit_is_in_bytes(apache: ApacheManager) -> None:
    assert "LimitRequestBody 67108864" in render(apache)
    assert "LimitRequestBody 134217728" in render(apache, max_upload_bytes=128 * 1024**2)


def test_every_refusal_nginx_makes_is_here(apache: ApacheManager) -> None:
    text = render(apache)

    for refusal in (
        r'<LocationMatch "^/wp-config\.php$">',
        r'<LocationMatch "^/readme\.html$">',
        r'<LocationMatch "/\.(?!well-known(?:/|$))">',
        r'<LocationMatch "(?i)^/(?:composer\.(?:json|lock)|vendor/)">',
        r'<LocationMatch "(?i)/(?:uploads|files)/.*\.php$">',
        r'<LocationMatch "(?i)\.(?:phtml|phar|php[0-9]|inc)$">',
        r"\.(?:log|sql|sqlite3?|bak|backup|old|orig|save|swp|swo|dist)",
    ):
        assert refusal in text, refusal
    assert text.count("Require all denied") == 7


def test_the_acme_challenge_is_served_from_the_shared_webroot(apache: ApacheManager) -> None:
    text = render(apache)

    assert "Alias /.well-known/acme-challenge/ /var/www/html/.well-known/acme-challenge/" in text


def test_a_site_with_a_certificate_redirects_to_https_except_the_challenge(
    apache: ApacheManager,
) -> None:
    text = render(apache, ssl=True, hsts="max-age=31536000")

    http, https = text.split("<VirtualHost *:443>")
    assert "RewriteCond %{REQUEST_URI} !^/\\.well-known/acme-challenge/" in http
    assert "RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]" in http
    assert "DocumentRoot" not in http
    assert "SSLEngine on" in https
    assert "DocumentRoot /var/www/apps/example-com/current" in https
    assert 'Header always set Strict-Transport-Security "max-age=31536000"' in https
    assert "SetHandler" in https


def test_aliases_and_redirects_are_served_like_every_other_site(apache: ApacheManager) -> None:
    text = render(
        apache,
        server_names="example.com www.example.com",
        redirect_domains=["old.example.com"],
    )

    assert "ServerAlias www.example.com" in text
    assert "# old.example.com redirects permanently to example.com" in text


def test_creating_the_site_enables_the_module_that_proxies_to_fastcgi(
    apache: ApacheManager, runner: FakeRunner
) -> None:
    apache.create_site("example.com", "fastcgi", CONTEXT)

    enabled = [call[1] for call in runner.calls_to("a2enmod")]
    assert "proxy_fcgi" in enabled
    assert enabled.index("proxy") < enabled.index("proxy_fcgi")


def test_rewriting_the_site_enables_it_again(apache: ApacheManager, runner: FakeRunner) -> None:
    apache.create_site("example.com", "fastcgi", CONTEXT)
    runner.calls.clear()

    apache.update_site("example.com", "fastcgi", CONTEXT)

    assert ("a2enmod", "proxy_fcgi") in runner.calls


def test_a_proxy_site_does_not_need_it(apache: ApacheManager, runner: FakeRunner) -> None:
    apache.create_site("example.com", "proxy", {"port": 3000})

    assert ("a2enmod", "proxy_fcgi") not in runner.calls
