# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust cert ca``: the server's own certificate authority from the command line.

The authority is made by the real ``openssl`` (the managers are tested in
``tests/test_internal_ca.py``); what is pinned here is the translation: a refusal is exit code 1
with the operation's own words, the key is never printed, ``root`` gives the public certificate
and a binary form only into a file, ``trust`` says how on each device and changes only this server
(its store here is a folder of the test's, rebuilt by a fake), and a rehearsal makes nothing.
"""

from __future__ import annotations

import json
import shutil
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.cli.commands import cert_ca
from noust.core import paths
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers import ca_trust
from noust.managers.ca_trust import ANCHOR_NAME, ServerStore

needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is not installed"
)
real = pytest.mark.allow_subprocess


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A configuration directory of the test's, and a system store of the test's."""
    NoustStore.reset_instance()
    NoustStore._instance = NoustStore(tmp_path / "state" / "noust.db")
    config = tmp_path / "etc-noust"
    monkeypatch.setattr(paths, "config_dir", lambda: config)
    anchors = tmp_path / "anchors"
    anchors.mkdir()
    store = ServerStore("debian", "Debian", anchors, "update-ca-certificates", (), ("--fresh",))
    original = ca_trust.ServerTrust
    rebuilds = FakeRunner()
    monkeypatch.setattr(
        cert_ca.ca_trust,
        "ServerTrust",
        lambda: original(runner=rebuilds, stores=(store,), which=lambda tool: f"/usr/sbin/{tool}"),
    )
    try:
        yield tmp_path
    finally:
        NoustStore.reset_instance()


def invoke(*args: str) -> Any:
    return CliRunner().invoke(root_cli, ["cert", "ca", *args])


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the logger would have printed: a refusal is said by the error boundary, not by Click."""
    written: list[str] = []
    monkeypatch.setattr(
        Logger, "_write", lambda self, message, newline=True: written.append(message)
    )
    return written


def refused(lines: list[str], *args: str) -> str:
    """Run a command that must be refused, and give what was said (exit code 1 is checked)."""
    assert main(["cert", "ca", *args]) == 1
    return "\n".join(lines)


def made() -> dict[str, Any]:
    result = invoke("init", "--json")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)  # type: ignore[no-any-return]


# -- before there is one --------------------------------------------------------------------


def test_a_server_without_an_authority_says_how_to_make_one(world: Path) -> None:
    text = invoke("status")
    as_json = invoke("status", "--json")
    listed = invoke("list", "--json")

    assert text.exit_code == 0 and "noust cert ca init" in text.output
    assert json.loads(as_json.output) == {"authority": None, "trusted_here": False}
    assert json.loads(listed.output) == {"certificates": []}


@pytest.mark.parametrize(
    "arguments",
    [
        ("root",),
        ("trust", "debian"),
        ("trust", "--here"),
        ("issue", "wiki", "wiki.lan"),
        ("remove", "-y"),
    ],
)
def test_everything_that_needs_the_authority_says_there_is_none(
    world: Path, lines: list[str], arguments: tuple[str, ...]
) -> None:
    said = refused(lines, *arguments)

    assert "no internal certificate authority" in said
    assert "noust cert ca init" in said


# -- making it ------------------------------------------------------------------------------------


@needs_openssl
@real
def test_init_makes_the_authority_and_shows_what_it_signs_for(world: Path) -> None:
    status = made()

    assert status["subject"].startswith("CN = Noust internal CA (")
    assert "home.arpa" in status["permitted_dns"] and "10.0.0.0/8" in status["permitted_networks"]
    assert status["issued"] == 0 and status["warnings"] == []
    key = world / "etc-noust" / "ca" / "ca.key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert "PRIVATE KEY" not in json.dumps(status)


@needs_openssl
@real
def test_a_second_init_is_refused_with_how_to_start_over(world: Path, lines: list[str]) -> None:
    made()

    said = refused(lines, "init")

    assert "already has an internal certificate authority" in said
    assert "noust cert ca remove" in said


@needs_openssl
@real
def test_init_limited_to_what_the_operator_names(world: Path) -> None:
    result = invoke(
        "init", "--only", "--permit-dns", "corp.example.com", "--permit-network", "10.20.0.0/16",
        "--days", "400", "--name", "Corp CA", "--json",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    status = json.loads(result.output)
    assert status["permitted_dns"] == ["corp.example.com"]
    assert status["permitted_networks"] == ["10.20.0.0/16"]
    assert status["subject"] == "CN = Corp CA" and 399 <= status["days_left"] <= 400


@pytest.mark.parametrize(
    "arguments",
    [
        ("--permit-dns", "com"),
        ("--permit-network", "8.8.8.0/24"),
        ("--name", "x"),
        ("--only",),
        ("--days", "10"),
    ],
)
def test_init_refuses_what_would_let_it_sign_for_the_public_internet(
    world: Path, arguments: tuple[str, ...]
) -> None:
    result = invoke("init", *arguments)

    assert result.exit_code != 0
    assert not (world / "etc-noust" / "ca").exists()


def test_a_rehearsal_makes_nothing(world: Path, runner: FakeRunner) -> None:
    result = CliRunner().invoke(root_cli, ["--dry-run", "cert", "ca", "init"])

    assert (
        result.exit_code == 0 and "Would make the internal certificate authority" in result.output
    )
    assert not (world / "etc-noust").exists()


# -- using it ---------------------------------------------------------------------------------------


@needs_openssl
@real
def test_a_certificate_is_signed_listed_and_signed_again(world: Path) -> None:
    made()

    issued = invoke("issue", "wiki", "wiki.home.arpa", "10.0.0.5", "--days", "60", "--json")
    listed = json.loads(invoke("list", "--json").output)["certificates"]
    renewed = invoke("renew", "internal-wiki", "--json")

    assert issued.exit_code == 0, issued.output
    body = json.loads(issued.output)
    assert body["name"] == "internal-wiki" and body["names"] == ["wiki.home.arpa"]
    assert [item["name"] for item in listed] == ["internal-wiki"]
    assert listed[0]["apps"] == [] and listed[0]["expiring"] is False
    assert renewed.exit_code == 0, renewed.output
    assert json.loads(renewed.output) == {"renewed": ["internal-wiki"]}
    after = json.loads(invoke("list", "--json").output)["certificates"][0]
    assert after["fingerprint"] != listed[0]["fingerprint"]


@needs_openssl
@real
def test_a_public_name_is_refused_with_what_the_authority_does_sign_for(
    world: Path, lines: list[str]
) -> None:
    made()

    said = refused(lines, "issue", "bank", "www.bank.com")

    assert "outside what this authority signs for" in said
    assert "home.arpa" in said


@needs_openssl
@real
def test_renew_wants_a_name_or_due_and_not_both(world: Path) -> None:
    made()

    assert invoke("renew").exit_code == 2
    assert invoke("renew", "internal-wiki", "--due").exit_code == 2
    nothing = invoke("renew", "--due")
    assert nothing.exit_code == 0 and "Nothing is within its renewal window" in nothing.output


# -- handing it to a device ---------------------------------------------------------------------


@needs_openssl
@real
def test_root_prints_the_public_certificate_and_never_the_key(world: Path) -> None:
    made()

    result = invoke("root")

    assert result.exit_code == 0
    assert result.output.startswith("-----BEGIN CERTIFICATE-----")
    assert "PRIVATE KEY" not in result.output


@needs_openssl
@real
def test_the_binary_form_goes_into_a_file_and_only_there(world: Path, tmp_path: Path) -> None:
    status = made()

    to_terminal = invoke("root", "--der")
    target = tmp_path / "ca.cer"
    to_file = invoke("root", "--der", "--out", str(target), "--json")

    assert to_terminal.exit_code == 2 and "give --out FILE" in to_terminal.output
    assert to_file.exit_code == 0, to_file.output
    assert json.loads(to_file.output)["fingerprint"] == status["fingerprint"]
    assert target.read_bytes()[:1] == b"\x30"  # DER begins with a SEQUENCE
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@needs_openssl
@real
def test_trust_says_how_on_each_device_with_the_fingerprint_to_compare(world: Path) -> None:
    status = made()

    listed = invoke("trust")
    one = invoke("trust", "windows", "--json")
    unknown = invoke("trust", "plan9")

    assert listed.exit_code == 0 and "windows" in listed.output and "macos" in listed.output
    assert status["fingerprint"] in listed.output
    body = json.loads(one.output)
    assert body["file"] == "der" and any("certutil" in step for step in body["steps"])
    assert unknown.exit_code == 2


@needs_openssl
@real
def test_trusting_this_server_changes_its_store_and_nothing_else(world: Path) -> None:
    made()
    anchor = world / "anchors" / ANCHOR_NAME
    assert json.loads(invoke("status", "--json").output)["trusted_here"] is False

    trusted = invoke("trust", "--here")
    assert anchor.read_text().startswith("-----BEGIN CERTIFICATE-----")
    after = json.loads(invoke("status", "--json").output)
    forgotten = invoke("trust", "--here", "--remove")

    assert trusted.exit_code == 0, trusted.output
    assert after["trusted_here"] is True
    assert forgotten.exit_code == 0 and not anchor.exists()
    assert json.loads(invoke("status", "--json").output)["trusted_here"] is False


@needs_openssl
@real
def test_init_can_trust_it_here_in_the_same_step(world: Path) -> None:
    result = invoke("init", "--trust-here", "--json")

    assert result.exit_code == 0, result.output
    assert (world / "anchors" / ANCHOR_NAME).exists()


def test_a_target_and_here_are_exclusive_and_remove_goes_with_here(world: Path) -> None:
    assert invoke("trust", "debian", "--here").exit_code == 2
    assert invoke("trust", "--remove").exit_code == 2


# -- taking it away ------------------------------------------------------------------------------


@needs_openssl
@real
def test_removing_the_authority_asks_and_needs_force_while_certificates_are_kept(
    world: Path, lines: list[str]
) -> None:
    made()
    invoke("issue", "wiki", "wiki.home.arpa")

    said = refused(lines, "remove", "-y")
    forced = invoke("remove", "-y", "--force")

    assert "internal-wiki" in said
    assert forced.exit_code == 0, forced.output
    assert not (world / "etc-noust" / "ca").exists()


@needs_openssl
@real
def test_declining_the_question_keeps_the_authority(world: Path) -> None:
    made()

    result = CliRunner().invoke(root_cli, ["cert", "ca", "remove"], input="n\n")

    assert result.exit_code == 0 and "Cancelled" in result.output
    assert (world / "etc-noust" / "ca" / "ca.key").exists()


# -- the answer of a program is the JSON alone ---------------------------------------------------


def test_a_manager_that_reports_its_steps_does_not_spoil_a_json_answer(
    world: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The web server's reload is reported by a logger of its own, on standard output. Found by a
    script reading ``noust cert use APP --internal --json``: a line before the JSON broke it.
    """

    def chatty(name: str) -> str:
        print("i wiki.home.arpa is served with the uploaded certificate " + name)
        return name

    monkeypatch.setattr(cert_ca.internal_certificates, "renew", chatty)

    as_json = invoke("renew", "internal-wiki", "--json")
    as_text = invoke("renew", "internal-wiki")

    assert json.loads(as_json.output) == {"renewed": ["internal-wiki"]}
    assert "is served with the uploaded certificate" in as_text.output  # a person sees the steps


@needs_openssl
@real
def test_deleting_it_reminds_only_of_a_trust_that_is_still_there(
    world: Path, lines: list[str]
) -> None:
    made()
    invoke("trust", "--here")

    assert main(["cert", "ca", "remove", "-y"]) == 0
    reminded = "\n".join(lines)
    lines.clear()
    made()
    assert main(["cert", "ca", "remove", "-y"]) == 0

    assert "still trusts it: noust cert ca trust --here --remove" in reminded
    assert "still trusts it" not in "\n".join(lines)
