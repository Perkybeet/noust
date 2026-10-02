"""
``POST /api/sites/{d}/config/test`` must not read files for its caller.

An operator (apps.operate) may test a candidate configuration. ``nginx -t``
runs as root and, for ``include /etc/shadow;``, answers ``unknown directive
"root:$6$..." in /etc/shadow:1``: the first line of any file. Before the web
server sees a candidate, every directive that reads, creates or runs a file
is checked against the roots it may name, and anything else is refused with
what to do; what still echoes a file other than the candidate is redacted.
The PUT that saves a configuration goes through the same check.
"""

# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import WebServerManager
from tests.test_webserver_managers import (  # noqa: F401
    apache,
    managers,
    nginx,
    store,
    validation_tmp,
)


@pytest.mark.parametrize(
    "candidate",
    [
        "include /etc/shadow;\n",
        "server { listen 80; include /etc/shadow; }\n",
        "server { include ../../../etc/shadow; }\n",
        "server { include /etc/noust/config.yaml; }\n",
        "server { listen 443 ssl; ssl_certificate /etc/shadow; }\n",
        "server { ssl_certificate_key /root/.ssh/id_ed25519; }\n",
        "server { ssl_trusted_certificate /etc/shadow; }\n",
        "server { ssl_dhparam /etc/shadow; }\n",
        "server { access_log /etc/cron.d/evil; }\n",
        "server { error_log /root/.bashrc; }\n",
        "server { location / { content_by_lua_block { os.execute('id') } } }\n",
        "perl_modules /tmp;\n",
        "js_import /tmp/x.js;\n",
        "server { ssl_certificate_key engine:pkcs11:x; }\n",
        # A candidate the analyzer cannot parse is still not tested blind.
        "include /etc/shadow; server {\n",
    ],
)
def test_a_candidate_naming_a_file_outside_its_roots_is_refused_unrun(
    nginx: NginxManager, runner: FakeRunner, validation_tmp: Path, candidate: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        nginx.test_config_text(candidate, domain="example.com")
    assert raised.value.details
    assert not any(call[:2] == ("nginx", "-t") for call in runner.calls)


@pytest.mark.parametrize(
    "candidate",
    [
        "<VirtualHost *:80>\nInclude /etc/shadow\n</VirtualHost>\n",
        "<VirtualHost *:443>\nSSLCertificateFile /etc/shadow\n</VirtualHost>\n",
        '<VirtualHost *:80>\nCustomLog "|/bin/sh -c id" combined\n</VirtualHost>\n',
        "<VirtualHost *:80>\nErrorLog /etc/cron.d/evil\n</VirtualHost>\n",
        "<VirtualHost *:80>\nRewriteMap m prg:/tmp/x\n</VirtualHost>\n",
    ],
)
def test_apache_candidates_are_checked_too(
    managers: dict[str, WebServerManager],
    runner: FakeRunner,
    validation_tmp: Path,
    candidate: str,
) -> None:
    with pytest.raises(ValidationError):
        managers["apache"].test_config_text(candidate, domain="example.com")
    assert not any("-t" in call for call in runner.calls)


def test_what_noust_writes_itself_is_still_tested(
    nginx: NginxManager, runner: FakeRunner, validation_tmp: Path
) -> None:
    conf = nginx.backend.sites_available.parent
    candidate = (
        "server {\n"
        "  listen 443 ssl;\n"
        "  server_name example.com;\n"
        "  ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;\n"
        "  ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;\n"
        "  include /etc/letsencrypt/options-ssl-nginx.conf;\n"
        "  ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;\n"
        f"  include {conf}/snippets/proxy.conf;\n"
        "  include snippets/fastcgi-php.conf;\n"
        "  include fastcgi_params;\n"
        "  access_log /var/log/nginx/example.com.access.log;\n"
        "  error_log /var/log/nginx/example.com.error.log;\n"
        "  location /x { access_log off; }\n"
        "}\n"
    )
    ok, _ = nginx.test_config_text(candidate, domain="example.com")
    assert ok
    assert any(call[:2] == ("nginx", "-t") for call in runner.calls)


def test_output_echoing_another_file_is_redacted(
    nginx: NginxManager, runner: FakeRunner, validation_tmp: Path
) -> None:
    """Whatever still names another file's content: the quoted part is withheld."""
    conf = nginx.backend.sites_available.parent
    runner.script(
        ["nginx", "-t"],
        stderr=f'nginx: [emerg] unknown directive "s3cr3t-line" in {conf}/fastcgi_params:1\n',
        exit_code=1,
    )
    ok, output = nginx.test_config_text(
        "server { include fastcgi_params; }\n", domain="example.com"
    )
    assert not ok
    assert "s3cr3t-line" not in output
    assert f"{conf}/fastcgi_params:1" in output


def test_saving_runs_the_same_check(
    nginx: NginxManager, runner: FakeRunner, validation_tmp: Path
) -> None:
    with pytest.raises(ValidationError):
        nginx.validate_config_text("include /etc/shadow;\n", domain="example.com")
