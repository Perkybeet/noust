"""
What a draft's ``include`` may show of /etc/nginx.

The site API analyses a draft the caller writes, and resolved every include
under the web server's directory, returning each file's text: a draft with
``include /etc/nginx/ssl/shop.key;`` or ``include /etc/nginx/.htpasswd;``
handed back a private key or the password hashes. Text now comes only from the
directories sites are made of (sites-available, sites-enabled, snippets,
conf.d) and Noust's upstream files, never from a key, certificate, password or
secret file; anything else an include names is listed by path only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.managers.site_topology import include_reader
from noust.managers.siteconf import parse, structure

KEY = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----\n"


@pytest.fixture
def conf_root(tmp_path: Path) -> Path:
    root = tmp_path / "etc/nginx"
    for name in ("sites-available", "sites-enabled", "snippets", "conf.d", "ssl"):
        (root / name).mkdir(parents=True)
    (root / "snippets/proxy.conf").write_text("proxy_set_header Host $host;\n")
    (root / "snippets/admin.htpasswd").write_text("admin:$apr1$hash\n")
    (root / "snippets/tls.key").write_text(KEY)
    (root / "conf.d/app-secret.conf").write_text("set $token abc;\n")
    (root / "ssl/shop.key").write_text(KEY)
    (root / ".htpasswd").write_text("admin:$apr1$hash\n")
    (root / "fastcgi_params").write_text("fastcgi_param QUERY_STRING $query_string;\n")
    return root


def _files(conf_root: Path, pattern: str) -> list[tuple[str, str]]:
    return list(include_reader(conf_root)(pattern))


@pytest.mark.parametrize(
    "pattern",
    [
        "ssl/shop.key",
        ".htpasswd",
        "snippets/admin.htpasswd",
        "snippets/tls.key",
        "conf.d/app-secret.conf",
        "snippets/*",
    ],
)
def test_secrets_are_never_returned(conf_root: Path, pattern: str) -> None:
    for _path, text in _files(conf_root, pattern):
        assert "PRIVATE KEY" not in text
        assert "$apr1$" not in text
        assert "$token" not in text


def test_files_outside_the_site_directories_are_listed_without_text(conf_root: Path) -> None:
    assert _files(conf_root, "fastcgi_params") == [(str(conf_root / "fastcgi_params"), "")]
    assert _files(conf_root, "ssl/shop.key") == [(str(conf_root / "ssl/shop.key"), "")]


def test_a_snippet_is_still_read(conf_root: Path) -> None:
    assert _files(conf_root, "snippets/proxy.conf") == [
        (str(conf_root / "snippets/proxy.conf"), "proxy_set_header Host $host;\n")
    ]


def test_a_draft_cannot_read_a_key_through_the_structure(conf_root: Path) -> None:
    draft = f"server {{ listen 80; include {conf_root}/ssl/shop.key; }}\n"
    model = structure(parse(draft, "nginx"), read_include=include_reader(conf_root))
    assert model.includes and model.includes[0].files
    assert all("PRIVATE KEY" not in file.text for file in model.includes[0].files)
