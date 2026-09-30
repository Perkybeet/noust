# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The review gallery keeps working.

``scripts/notification_gallery.py`` is what a person opens to review every
message; a script nobody runs rots. This runs it without screenshots (no
browser, no network) and checks that it wrote what it promises.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _gallery() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "notification_gallery", ROOT / "scripts" / "notification_gallery.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_writes_every_channel_of_every_event_in_both_languages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["notification_gallery.py", "--out", str(tmp_path)])

    _gallery().main()

    event = tmp_path / "en" / "deploy.rolled_back"
    for name in (
        "telegram.json",
        "slack.json",
        "discord.json",
        "webhook.json",
        "email.subject.txt",
        "email.text.txt",
        "email.html",
        "email.preview.html",
        "telegram.html",
        "slack.html",
        "discord.html",
        "index.html",
    ):
        assert (event / name).is_file(), name
    assert (tmp_path / "es" / "cert.expired" / "telegram.json").is_file()
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "assets" / "noust-wordmark.png").is_file()


def test_the_email_preview_can_show_the_wordmark_and_the_real_html_still_says_cid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["notification_gallery.py", "--out", str(tmp_path), "--only", "test"]
    )

    _gallery().main()

    event = tmp_path / "en" / "test"
    assert "cid:noust-wordmark" in (event / "email.html").read_text()
    preview = (event / "email.preview.html").read_text()
    assert "cid:" not in preview and "assets/noust-wordmark.png" in preview
    assert json.loads((event / "webhook.json").read_text())["code"] == "test"


def test_the_mock_ups_say_they_are_approximations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["notification_gallery.py", "--out", str(tmp_path), "--only", "test"]
    )

    _gallery().main()

    for name in ("telegram.html", "slack.html", "discord.html"):
        assert "Approximation" in (tmp_path / "en" / "test" / name).read_text()
