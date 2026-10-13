# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``noust app policy``.

The command decides nothing: :mod:`noust.deployers.access` does, and is covered
in ``tests/test_site_policy.py``. Pinned here is the translation: the flags that
become changes (a flag left out changes nothing), the address of the SSH session
handed over as the operator's, a password read from standard input and never an
argument, and a refusal becoming exit code 1 with the operation's own words.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.deployers import access
from noust.managers.site_policy import SitePolicy, validated

APP = "example.com"


def view(**changes: Any) -> access.AccessView:
    return access.AccessView(
        app=APP,
        policy=validated(SitePolicy(), changes),
        users=("ana",),
        webserver="nginx",
        headers=(("X-Frame-Options", "SAMEORIGIN"),),
    )


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    recorded: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def stub(name: str) -> Any:
        def fake(*args: Any, **kwargs: Any) -> Any:
            recorded.append((name, args, kwargs))
            return view()

        return fake

    for name in ("describe", "set_policy", "reset_policy", "set_user", "remove_user"):
        monkeypatch.setattr(access, name, stub(name))
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    return recorded


def invoke(args: list[str], input_text: str | None = None) -> Result:
    return CliRunner().invoke(root_cli, args, input=input_text)


def test_show_prints_json_on_either_side_of_the_command(calls: list[Any]) -> None:
    for args in (
        ["app", "policy", "show", APP, "--json"],
        ["--json", "app", "policy", "show", APP],
    ):
        result = invoke(args)

        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["app"] == APP
        assert body["users"] == ["ana"]
        assert "hash" not in result.output


def test_only_the_flags_given_become_changes(calls: list[Any], log: list[str]) -> None:
    result = invoke(
        [
            "app",
            "policy",
            "set",
            APP,
            "--rate-limit",
            "10r/s",
            "--allow",
            "203.0.113.0/24",
            "--allow",
            "198.51.100.7",
            "--noindex",
            "--canonical",
            "www",
            "--hsts",
            "off",
        ]
    )

    assert result.exit_code == 0, result.output
    name, args, kwargs = calls[-1]
    assert name == "set_policy"
    assert args == (
        APP,
        {
            "noindex": True,
            "allow_ips": ["203.0.113.0/24", "198.51.100.7"],
            "rate_limit": "10r/s",
            "canonical": "www",
            "hsts": False,
        },
    )
    assert kwargs["accept_lockout"] is False
    assert kwargs["operator_addresses"] == []


def test_naming_nothing_to_change_is_a_usage_error(calls: list[Any]) -> None:
    result = invoke(["app", "policy", "set", APP])

    assert result.exit_code == 2
    assert calls == []


def test_clearing_a_list_and_a_rate_limit(calls: list[Any]) -> None:
    result = invoke(
        [
            "app",
            "policy",
            "set",
            APP,
            "--clear-allow",
            "--rate-limit",
            "none",
            "--canonical",
            "none",
        ]
    )

    assert result.exit_code == 0, result.output
    assert calls[-1][1][1] == {"allow_ips": [], "rate_limit": "", "canonical": ""}


def test_the_ssh_session_is_the_operator_and_the_lockout_can_be_accepted(
    calls: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "198.51.100.4 50222 203.0.113.10 22")

    invoke(["app", "policy", "set", APP, "--allow", "203.0.113.0/24", "--accept-lockout"])

    _, _, kwargs = calls[-1]
    assert kwargs["operator_addresses"] == ["198.51.100.4"]
    assert kwargs["accept_lockout"] is True


def test_headers_are_merged_with_the_ones_already_set(
    calls: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        access,
        "describe",
        lambda *a, **k: view(headers={"Permissions-Policy": "geolocation=()", "X-A": "1"}),
    )

    result = invoke(
        [
            "app",
            "policy",
            "set",
            APP,
            "--header",
            "X-Frame-Options: DENY",
            "--remove-header",
            "X-A",
            "--hide-header",
            "Referrer-Policy",
        ]
    )

    assert result.exit_code == 0, result.output
    assert calls[-1][1][1]["headers"] == {
        "Permissions-Policy": "geolocation=()",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": None,
    }


def test_a_header_without_a_colon_is_a_usage_error(calls: list[Any]) -> None:
    result = invoke(["app", "policy", "set", APP, "--header", "X-Frame-Options"])

    assert result.exit_code == 2
    assert "Name: value" in result.output


def test_a_password_comes_from_standard_input_and_never_from_an_argument(
    calls: list[Any],
) -> None:
    result = invoke(
        ["app", "policy", "user", "add", APP, "ana", "--password-stdin"],
        input_text="correct horse battery\n",
    )

    assert result.exit_code == 0, result.output
    assert calls[-1][:2] == ("set_user", (APP, "ana", "correct horse battery"))
    assert "correct horse battery" not in result.output


def test_an_empty_password_on_standard_input_is_refused(calls: list[Any]) -> None:
    result = invoke(
        ["app", "policy", "user", "add", APP, "ana", "--password-stdin"], input_text="\n"
    )

    assert result.exit_code == 1
    assert calls == []


def test_a_refusal_is_exit_code_one_with_the_operations_words(
    monkeypatch: pytest.MonkeyPatch, log: list[str]
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise ValidationError("These lists would refuse your own address, 198.51.100.4")

    monkeypatch.setattr(access, "set_policy", refuse)

    assert main(["app", "policy", "set", APP, "--allow", "10.0.0.1"]) == 1
    assert any("198.51.100.4" in line for line in log)


def test_a_rehearsal_changes_nothing(calls: list[Any]) -> None:
    result = invoke(["--dry-run", "app", "policy", "set", APP, "--noindex"])

    assert result.exit_code == 0, result.output
    assert [name for name, _, _ in calls] == []


def test_ranges_only_read_unless_asked_to_refresh(
    calls: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    refreshed = {"changed": True, "applied": [APP], "failed": {}}
    monkeypatch.setattr(access, "refresh_trusted_ranges", lambda **k: refreshed)

    read = invoke(["app", "policy", "ranges", "--json"])
    refresh = invoke(["app", "policy", "ranges", "--refresh", "--json"])

    assert "changed" not in json.loads(read.output)
    assert json.loads(refresh.output)["applied"] == [APP]
