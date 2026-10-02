"""
A repository's noust.nginx.yaml is untrusted input to a site root's nginx serves.

``custom_directives`` were rendered verbatim (``{{ directive }};``), and so
were header names and values and route names: a repository could put any
directive in its site, ``include /etc/shadow``, ``location /x { alias /etc/; }``,
``access_log /etc/cron.d/x``, Lua. A custom directive is now one simple
directive from a closed list; every other value that ends up in a directive is
checked for what could close it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.deployers.helpers.nginx_config import NginxAdvancedConfig, NginxConfigBuilder

ROUTES = "routes:\n  - path: /\n    port: 3000\n"


def _config(tmp_path: Path, text: str) -> NginxAdvancedConfig:
    path = tmp_path / "noust.nginx.yaml"
    path.write_text(text)
    return NginxConfigBuilder().parse(path)


@pytest.mark.parametrize(
    "directive",
    [
        "include /etc/shadow",
        "location /x { alias /etc/; }",
        "access_log /etc/cron.d/evil",
        "error_log /root/x",
        "content_by_lua_block { os.execute('id') }",
        "ssl_certificate_key /etc/shadow",
        "load_module /tmp/x.so",
        "proxy_pass http://169.254.169.254",
        "root /",
        "add_header X 1; include /etc/shadow",
        "add_header X 1 # ;\n include /etc/shadow",
        "add_header X {",
        "alias /etc/",
    ],
)
def test_a_custom_directive_outside_the_list_is_refused(tmp_path: Path, directive: str) -> None:
    builder = NginxConfigBuilder()
    config = _config(tmp_path, ROUTES + f"custom_directives:\n  - {directive!r}\n")
    errors = builder.validate(config)
    assert any("custom_directives" in error for error in errors), errors
    with pytest.raises(ValidationError) as raised:
        builder.build_context(config, "example.com", app_path="/var/www/apps/x")
    assert "your own site" in (raised.value.details or "")


@pytest.mark.parametrize(
    "directive",
    [
        "client_max_body_size 50m",
        "proxy_intercept_errors on",
        'add_header X-Robots-Tag "noindex"',
        "proxy_set_header X-Real-IP $remote_addr",
        "gzip_min_length 1000",
        "proxy_read_timeout 120s",
        "expires 1d",
        "server_tokens off",
        "charset utf-8",
    ],
)
def test_safe_tuning_directives_are_kept(tmp_path: Path, directive: str) -> None:
    builder = NginxConfigBuilder()
    config = _config(tmp_path, ROUTES + f"custom_directives:\n  - {directive!r}\n")
    assert builder.validate(config) == []
    context = builder.build_context(config, "example.com", app_path="/var/www/apps/x")
    assert context["custom_directives"] == [directive]


@pytest.mark.parametrize(
    "text",
    [
        'security_headers:\n  "X-A; include /etc/shadow; add_header X": "1"\n',
        'security_headers:\n  X-A: "1\\" always; include /etc/shadow; #"\n',
        "routes:\n  - path: /\n    port: 3000\n    name: 'x { } include /etc/shadow; upstream y'\n",
        "routes:\n  - path: /\n    port: 3000\n    buffer_size: '4k; include /etc/shadow'\n",
        "routes:\n  - path: /\n    port: 3000\n    rate_limit: 10r/s\n    rate_limit_burst: '5 nodelay; include /etc/shadow; #'\n",
    ],
)
def test_other_values_cannot_close_their_directive(tmp_path: Path, text: str) -> None:
    builder = NginxConfigBuilder()
    config = _config(tmp_path, (ROUTES if not text.startswith("routes") else "") + text)
    assert builder.validate(config), "accepted"
    with pytest.raises(ValidationError):
        builder.build_context(config, "example.com", app_path="/var/www/apps/x")


def test_a_static_route_through_a_link_out_of_the_tree_is_refused(tmp_path: Path) -> None:
    """``static: public`` with public -> /etc would serve /etc through alias."""
    tree = tmp_path / "build"
    tree.mkdir()
    (tree / "public").symlink_to("/etc")
    (tree / "assets").mkdir()
    builder = NginxConfigBuilder()
    linked = _config(tmp_path, "routes:\n  - path: /files/\n    static: public\n")
    assert any("static" in error for error in builder.validate(linked, tree=tree))
    plain = _config(tmp_path, "routes:\n  - path: /files/\n    static: assets\n")
    assert builder.validate(plain, tree=tree) == []
