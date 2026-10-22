# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Trusting the internal authority: the steps for each kind of device, and this server's own store.

What is pinned: every guide names the file it wants and carries the fingerprint to compare (the
person who follows it knows the file is the one this server made); an unknown target is refused
naming the ones there are; on this server the root goes in the system's own folder, the bundle is
rebuilt through the runner and not by a shell, and taking it out rebuilds it again; a system
without a store Noust knows is told so instead of guessing; and nothing is trusted that is not the
exact certificate that was given.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import CertificateError, ValidationError
from noust.core.runner import FakeRunner
from noust.managers import ca_trust
from noust.managers.ca_trust import ANCHOR_NAME, ServerStore, ServerTrust

FINGERPRINT = "AA:BB:CC:" + ":".join(["00"] * 29)
ROOT = "-----BEGIN CERTIFICATE-----\nMIIBROOT\n-----END CERTIFICATE-----\n"


# -- the steps for a device -------------------------------------------------------------------


def test_every_guide_says_which_file_it_wants_and_what_to_compare() -> None:
    guides = ca_trust.guides(FINGERPRINT, "nas")

    assert [item.id for item in guides] == list(ca_trust.TARGETS)
    for item in guides:
        assert item.file in ("pem", "der")
        assert item.steps, item.id
        text = " ".join(item.steps)
        assert FINGERPRINT in text or "double-click" in text or item.id in ("node", "firefox"), (
            item.id
        )


def test_the_formats_are_the_ones_each_system_imports() -> None:
    by_id = {item.id: item for item in ca_trust.guides(FINGERPRINT, "nas")}

    assert by_id["windows"].file == "der" and by_id["android"].file == "der"
    assert by_id["debian"].file == "pem" and by_id["macos"].file == "pem"


def test_the_file_on_the_device_is_named_after_the_server() -> None:
    chosen = ca_trust.guide("debian", FINGERPRINT, "nas")

    assert "/usr/local/share/ca-certificates/noust-nas.crt" in " ".join(chosen.steps)
    assert chosen.to_dict()["id"] == "debian"


def test_a_target_nobody_wrote_a_guide_for_is_refused_naming_the_ones_there_are() -> None:
    with pytest.raises(ValidationError) as raised:
        ca_trust.guide("plan9", FINGERPRINT, "nas")

    assert raised.value.field == "target"
    assert "debian" in raised.value.details and "windows" in raised.value.details


# -- this server -----------------------------------------------------------------------------------


def stores(tmp_path: Path) -> tuple[ServerStore, ...]:
    for name in ("rhel", "suse", "debian"):
        (tmp_path / name).mkdir()
    return (
        ServerStore("rhel", "RHEL", tmp_path / "rhel", "update-ca-trust"),
        ServerStore("suse", "SUSE", tmp_path / "suse", "update-ca-certificates", (), ("--fresh",)),
        ServerStore(
            "debian", "Debian", tmp_path / "debian", "update-ca-certificates", (), ("--fresh",)
        ),
    )


def present(*tools: str):  # type: ignore[no-untyped-def]
    return lambda tool: f"/usr/sbin/{tool}" if tool in tools else None


def test_the_system_is_recognised_by_its_folder_and_its_tool(tmp_path: Path) -> None:
    found = stores(tmp_path)

    assert ServerTrust(stores=found, which=present("update-ca-trust")).store() == found[0]
    # openSUSE has Debian's tool: its own folder decides
    assert ServerTrust(stores=found, which=present("update-ca-certificates")).store() == found[1]
    (tmp_path / "suse").rmdir()
    assert ServerTrust(stores=found, which=present("update-ca-certificates")).store() == found[2]


def test_a_system_without_the_tool_has_no_store(tmp_path: Path) -> None:
    trust = ServerTrust(stores=stores(tmp_path), which=present())

    assert trust.store() is None
    assert trust.installed(ROOT) is False
    assert trust.remove() is False
    with pytest.raises(CertificateError) as raised:
        trust.install(ROOT)
    assert "no store of authorities" in raised.value.message
    assert "noust cert ca trust TARGET" in raised.value.details


def test_installing_puts_the_certificate_in_the_folder_and_rebuilds_the_bundle(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    trust = ServerTrust(runner=runner, stores=stores(tmp_path), which=present("update-ca-trust"))

    store = trust.install(ROOT)

    written = tmp_path / "rhel" / ANCHOR_NAME
    assert store.id == "rhel" and written.read_text() == ROOT
    assert (written.stat().st_mode & 0o777) == 0o644
    assert runner.calls == [("update-ca-trust",)]
    assert trust.installed(ROOT) is True


def test_a_different_certificate_under_the_same_name_is_not_the_one_trusted(
    tmp_path: Path,
) -> None:
    trust = ServerTrust(
        runner=FakeRunner(), stores=stores(tmp_path), which=present("update-ca-trust")
    )
    trust.install(ROOT)

    assert trust.installed(ROOT.replace("MIIBROOT", "MIIBOTHER")) is False


def test_removing_takes_the_file_out_and_rebuilds_with_the_tools_own_clean_up(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    trust = ServerTrust(
        runner=runner, stores=stores(tmp_path), which=present("update-ca-certificates")
    )
    trust.install(ROOT)

    removed = trust.remove()

    assert removed is True and not (tmp_path / "suse" / ANCHOR_NAME).exists()
    assert runner.calls == [
        ("update-ca-certificates",),
        ("update-ca-certificates", "--fresh"),
    ]
    assert trust.remove() is False  # nothing left to take out


def test_a_bundle_that_cannot_be_rebuilt_is_reported_with_the_tools_own_words(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    runner.script(["update-ca-trust"], exit_code=1, stderr="permission denied")
    trust = ServerTrust(runner=runner, stores=stores(tmp_path), which=present("update-ca-trust"))

    with pytest.raises(CertificateError) as raised:
        trust.install(ROOT)

    assert "update-ca-trust could not trust" in raised.value.message
    assert raised.value.output == "permission denied"
