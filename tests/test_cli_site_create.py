"""
`noust site create` tells what the web server did with the site it wrote.

It printed "Site created" whatever the web server said; against a real server whose nginx was
stopped (a Coolify proxy still held the port) the site existed and nothing answered.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.cli.commands import site as site_command
from noust.core.logger import Logger
from noust.managers.webserver import SecuredSite

BIND = "nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address already in use)"


class Recorder(Logger):
    """A logger that keeps what it was asked to print, by level."""

    def __init__(self) -> None:
        super().__init__(verbose=False)
        self.lines: list[tuple[str, str]] = []

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.lines.append(("info", message))

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.lines.append(("warning", message))

    def success(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.lines.append(("success", message))


def _create(monkeypatch: pytest.MonkeyPatch, outcome: SecuredSite) -> Recorder:
    monkeypatch.setattr(site_command, "create_secured_site", lambda *a, **k: outcome)
    monkeypatch.setattr(site_command, "_get_manager", lambda *a, **k: object())
    monkeypatch.setattr(site_command, "CertManager", lambda *a, **k: object())
    logger = Recorder()
    site_command._site_create(
        domain="example.com",
        webserver="nginx",
        template="proxy",
        port=3000,
        no_ssl=True,
        www=False,
        logger=logger,
        verbose=False,
    )
    return logger


def test_a_web_server_that_would_not_load_the_site_is_a_warning_with_its_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _create(
        monkeypatch, SecuredSite(domain="example.com", webserver="nginx", load_error=BIND)
    )

    assert any(BIND in text for _level, text in logger.lines)
    assert any(level == "warning" and "nothing serves" in text for level, text in logger.lines)


def test_a_site_the_web_server_loaded_has_no_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = _create(monkeypatch, SecuredSite(domain="example.com", webserver="nginx"))

    assert not any(level == "warning" for level, _ in logger.lines)
    assert ("success", "Site created: example.com") in logger.lines
