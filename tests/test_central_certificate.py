# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central's self-signed certificate (ENS G07): ECDSA P-256 with the names it
is reached by, and an expiry the operator can see.

A certificate without ``subjectAltName`` is refused by every current browser,
so it cannot even be installed as trusted (and Chrome and Firefox refuse
WebAuthn on a certificate they do not trust). The names come from the machine:
its host name, its FQDN, the addresses of its interfaces, and what the
operator adds with ``NOUST_TLS_NAMES``.
"""

from __future__ import annotations

import socket
import types
from pathlib import Path

import pytest

from noust.central import setup
from noust.core.runner import FakeRunner


def fake_psutil(monkeypatch: pytest.MonkeyPatch, interfaces: dict[str, list[tuple[int, str]]]):
    """
    Replace ``psutil.net_if_addrs`` with a scripted machine.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        interfaces: Interface name to ``(family, address)`` pairs.
    """
    import psutil

    def net_if_addrs() -> dict[str, list[types.SimpleNamespace]]:
        return {
            name: [
                types.SimpleNamespace(family=family, address=address) for family, address in rows
            ]
            for name, rows in interfaces.items()
        }

    monkeypatch.setattr(psutil, "net_if_addrs", net_if_addrs)


@pytest.fixture(autouse=True)
def machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Give every test the same machine: a NAS with a LAN address and a Docker bridge.

    Args:
        monkeypatch: Patching helper, scoped to the test.
    """
    monkeypatch.setattr(socket, "gethostname", lambda: "nas")
    monkeypatch.setattr(socket, "getfqdn", lambda *a: "nas.corp.example.com")
    fake_psutil(
        monkeypatch,
        {
            "lo": [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")],
            "eth0": [
                (socket.AF_INET, "192.168.1.50"),
                (socket.AF_INET6, "fe80::1%eth0"),
                (socket.AF_INET6, "2001:db8::50"),
                (socket.AF_PACKET, "aa:bb:cc:dd:ee:ff"),
            ],
        },
    )


def test_the_names_are_the_hostname_the_fqdn_the_lan_addresses_and_loopback() -> None:
    names = setup.certificate_names("nas", environ={})

    assert names == [
        "DNS:nas",
        "DNS:nas.corp.example.com",
        "DNS:localhost",
        "IP:127.0.0.1",
        "IP:::1",
        "IP:192.168.1.50",
        "IP:2001:db8::50",
    ]


def test_link_local_and_hardware_addresses_are_not_names() -> None:
    names = setup.certificate_names("nas", environ={})

    assert not any("fe80" in name or "aa:bb" in name for name in names)


def test_the_bind_address_is_a_name_when_it_is_an_address() -> None:
    names = setup.certificate_names("10.1.2.3", environ={})

    assert names[0] == "IP:10.1.2.3"


def test_the_operator_can_add_names_and_addresses() -> None:
    names = setup.certificate_names(
        "nas", environ={"NOUST_TLS_NAMES": "central.example.com, 203.0.113.9 nas.lan"}
    )

    assert {"DNS:central.example.com", "IP:203.0.113.9", "DNS:nas.lan"} <= set(names)


@pytest.mark.parametrize(
    "hostile",
    [
        "a,DNS:evil.example.com",
        "x;y",
        "a b=c",
        "-bad.example.com",
        "a..b",
        "*.example.com",
        "é.example.com",
    ],
)
def test_a_name_that_could_restructure_the_extension_is_dropped(hostile: str) -> None:
    names = setup.certificate_names("nas", environ={"NOUST_TLS_NAMES": hostile})

    joined = ",".join(names)
    assert "evil" not in joined
    assert hostile not in names
    assert all(name.startswith(("DNS:", "IP:")) for name in names)
    assert "," not in "".join(name[4:] for name in names if name.startswith("DNS:"))


def test_no_name_appears_twice() -> None:
    names = setup.certificate_names("nas", environ={"NOUST_TLS_NAMES": "nas localhost 127.0.0.1"})

    assert len(names) == len(set(names))


def test_a_machine_that_cannot_say_its_fqdn_still_gets_a_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object) -> str:
        raise OSError("name resolution is off")

    monkeypatch.setattr(socket, "getfqdn", refuse)

    names = setup.certificate_names("nas", environ={})

    assert "DNS:nas" in names
    assert "IP:192.168.1.50" in names


def test_without_psutil_the_hostname_addresses_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def no_psutil(name: str, *args: object, **kwargs: object):
        if name == "psutil":
            raise ImportError("psutil")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_psutil)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.77", 0))],
    )

    names = setup.certificate_names("nas", environ={})

    assert "IP:192.168.1.77" in names


def test_the_number_of_names_is_bounded() -> None:
    many = " ".join(f"host{index}.example.com" for index in range(200))

    names = setup.certificate_names("nas", environ={"NOUST_TLS_NAMES": many})

    assert len(names) <= setup.MAX_CERTIFICATE_NAMES


# -- expiry ---------------------------------------------------------------------------

#: A certificate exactly as ``openssl req`` mints one for the manager's argv
#: (ECDSA P-256, names, CA:FALSE), made once with OpenSSL 3.0.13. Its
#: ``notAfter`` is ``Jan  1 22:43:09 2029 GMT``.
MINTED_CERTIFICATE = """\
-----BEGIN CERTIFICATE-----
MIIBqjCCAVGgAwIBAgIUS8aMarts/5cu8OIPcNRPdUR1jnwwCgYIKoZIzj0EAwIw
DjEMMAoGA1UEAwwDbmFzMB4XDTI2MDkyOTIyNDMwOVoXDTI5MDEwMTIyNDMwOVow
DjEMMAoGA1UEAwwDbmFzMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEeBGXmJak
nDuk6SzP6Oi3fKocIPoS/rcLEIa2qDfeDaoN0Orhj25I3cCf9nsb68eGoszoFJ+/
BBiIEx2DJH2FiaOBjDCBiTAdBgNVHQ4EFgQUnplfxIJ9Xrh0SBjF2rm2Er+s0Wgw
HwYDVR0jBBgwFoAUnplfxIJ9Xrh0SBjF2rm2Er+s0WgwFAYDVR0RBA0wC4IDbmFz
hwTAqAEyMAwGA1UdEwEB/wQCMAAwDgYDVR0PAQH/BAQDAgeAMBMGA1UdJQQMMAoG
CCsGAQUFBwMBMAoGCCqGSM49BAMCA0cAMEQCIBzMtwrXZky8/DjDDsRhDayc+lFT
4WKbQ1KtjDSpn5Q/AiBrn5W6CB/2z9+LjrrJW3dGkVQxMuvyH/mmGhM6JllCLg==
-----END CERTIFICATE-----
"""


def test_the_expiry_is_read_from_the_certificate(tmp_path: Path) -> None:
    cert = tmp_path / "panel.crt"
    cert.write_text(MINTED_CERTIFICATE)

    expiry = setup.certificate_expiry(cert)

    assert expiry is not None
    assert (expiry.year, expiry.month, expiry.day) == (2029, 1, 1)
    assert (expiry.hour, expiry.minute, expiry.second) == (22, 43, 9)
    assert expiry.utcoffset() is not None
    assert expiry.utcoffset().total_seconds() == 0


def test_reading_the_expiry_runs_nothing(tmp_path: Path, runner: FakeRunner) -> None:
    """A status report must work where openssl is not installed."""
    cert = tmp_path / "panel.crt"
    cert.write_text(MINTED_CERTIFICATE)

    setup.certificate_expiry(cert)

    assert runner.calls == []


def test_a_missing_or_unreadable_certificate_has_no_expiry(tmp_path: Path) -> None:
    assert setup.certificate_expiry(tmp_path / "missing.crt") is None

    junk = tmp_path / "junk.crt"
    junk.write_text("not a certificate")
    assert setup.certificate_expiry(junk) is None

    truncated = tmp_path / "truncated.crt"
    lines = MINTED_CERTIFICATE.splitlines()
    truncated.write_text("\n".join([lines[0], lines[1], lines[2], lines[-1]]) + "\n")
    assert setup.certificate_expiry(truncated) is None

    not_base64 = tmp_path / "b64.crt"
    not_base64.write_text("-----BEGIN CERTIFICATE-----\n!!!!\n-----END CERTIFICATE-----\n")
    assert setup.certificate_expiry(not_base64) is None


def test_a_certificate_without_a_version_is_read_too(tmp_path: Path) -> None:
    """X.509 v1 has no [0] version element; the walk must not assume one."""
    import base64

    def tlv(tag: int, content: bytes) -> bytes:
        return bytes([tag, len(content)]) + content

    validity = tlv(0x30, tlv(0x17, b"260101000000Z") + tlv(0x17, b"330615123000Z"))
    tbs = tlv(
        0x30,
        tlv(0x02, b"\x01")  # serial
        + tlv(0x30, tlv(0x06, b"\x2a\x03"))  # signature algorithm
        + tlv(0x30, b"")  # issuer
        + validity,
    )
    der = tlv(0x30, tbs)
    cert = tmp_path / "v1.crt"
    cert.write_text(
        "-----BEGIN CERTIFICATE-----\n"
        + base64.b64encode(der).decode()
        + "\n-----END CERTIFICATE-----\n"
    )

    expiry = setup.certificate_expiry(cert)

    assert expiry is not None
    assert (expiry.year, expiry.month, expiry.day, expiry.hour, expiry.minute) == (
        2033,
        6,
        15,
        12,
        30,
    )


# -- what `noust central status` says -------------------------------------------------


def test_the_status_says_when_the_certificate_expires() -> None:
    from datetime import datetime, timedelta, timezone

    from noust.cli.commands import central as central_cmd

    in_a_year = datetime.now(timezone.utc) + timedelta(days=366, hours=1)
    soon = datetime.now(timezone.utc) + timedelta(days=6, hours=1)
    gone = datetime.now(timezone.utc) - timedelta(days=3, hours=1)

    assert central_cmd._expiry_text(None) is None
    assert central_cmd._expiry_text(in_a_year) == f"{in_a_year:%Y-%m-%d} (in 366 days)"
    assert central_cmd._expiry_text(soon) == f"{soon:%Y-%m-%d} (in 6 days: renew it)"
    assert central_cmd._expiry_text(gone) == f"{gone:%Y-%m-%d} (expired 4 days ago: renew it)"
