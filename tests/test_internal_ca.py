# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The internal certificate authority: names that only resolve inside a private network (E-38).

The authority and what it signs are made by the real ``openssl`` (the tests that need it are marked
and skipped on a machine without it), because what is defended is what a certificate really says:
the name constraints are in the root and critical, so *openssl itself* refuses a leaf for a public
name signed with the very key (the claim a leaked key cannot impersonate ``bank.com`` is proved
here, not assumed); a leaf serves and nothing else; the key is root's alone; and the authority is
never made twice, nor by anything but the operator. What needs no process (the rules that judge a
name or a range) is tested without one.
"""

from __future__ import annotations

import ipaddress
import shutil
import stat
import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core import paths
from noust.core.certificate_store import CertificateStore
from noust.core.exceptions import CertificateError, ValidationError
from noust.core.runner import SubprocessRunner
from noust.core.store import NoustStore
from noust.managers import internal_ca
from noust.managers.custom_certificates import CustomCertificates, fullchain_path
from noust.managers.database.tls_authority import TlsAuthority
from noust.managers.internal_ca import (
    DEFAULT_DNS,
    DEFAULT_NETWORKS,
    InternalCA,
    is_permitted,
    normal_network,
    normal_suffix,
    parse_name_constraints,
)

needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is not installed"
)
real = pytest.mark.allow_subprocess


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    NoustStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "etc-noust"
    monkeypatch.setattr(paths, "config_dir", lambda: directory)
    return directory


@pytest.fixture
def authority(store: NoustStore, config: Path) -> InternalCA:
    runner = SubprocessRunner()
    return InternalCA(
        uploads=CustomCertificates(CertificateStore(store), runner=runner), runner=runner
    )


def openssl(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["openssl", *args], input=input_text, capture_output=True, text=True, check=False
    )


def text_of(path: Path) -> str:
    return openssl("x509", "-in", str(path), "-noout", "-text").stdout


def verify(ca: Path, leaf: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return openssl("verify", "-CAfile", str(ca), *extra, str(leaf))


# -- what the root really is --------------------------------------------------------------------


@needs_openssl
@real
def test_the_root_limits_itself_in_a_critical_extension(
    authority: InternalCA, config: Path
) -> None:
    status = authority.init()

    described = text_of(config / "ca" / "ca.crt")
    assert "X509v3 Name Constraints: critical" in described
    assert "CA:TRUE, pathlen:0" in described
    assert "Certificate Sign, CRL Sign" in described
    assert "prime256v1" in described
    assert set(status.permitted_dns) == set(DEFAULT_DNS)
    assert set(status.permitted_networks) == {
        str(ipaddress.ip_network(text)) for text in DEFAULT_NETWORKS
    }
    assert status.subject.startswith("CN = Noust internal CA (")
    assert 3649 <= status.days_left <= 3650 and status.warnings == ()


@needs_openssl
@real
def test_the_key_is_roots_alone_and_the_certificate_is_public(
    authority: InternalCA, config: Path
) -> None:
    authority.init()

    directory = config / "ca"
    assert stat.S_IMODE((directory / "ca.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((directory / "ca.crt").stat().st_mode) == 0o644
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert "PRIVATE KEY" in (directory / "ca.key").read_text()
    assert "PRIVATE KEY" not in authority.root_pem()


@needs_openssl
@real
def test_the_authority_is_never_made_twice(authority: InternalCA) -> None:
    first = authority.init()

    with pytest.raises(CertificateError) as raised:
        authority.init(permit_dns=["corp.example.com"])

    assert "already has an internal certificate authority" in raised.value.message
    assert authority.status().fingerprint == first.fingerprint  # type: ignore[union-attr]


@needs_openssl
@real
def test_it_is_not_the_database_authority_and_never_signs_for_an_engine(
    authority: InternalCA, config: Path, tmp_path: Path
) -> None:
    authority.init()
    engines = TlsAuthority(directory=config / "tls", runner=SubprocessRunner())
    engines.ensure_ca()
    engine_leaf = engines.issue("pg17", ["127.0.0.1", "localhost"])
    leaf = tmp_path / "engine.crt"
    leaf.write_text(engine_leaf.certificate)

    assert (config / "ca" / "ca.crt").read_text() != (config / "tls" / "ca.crt").read_text()
    assert "Name Constraints" not in text_of(config / "tls" / "ca.crt")
    # an engine's certificate does not verify against the web authority, nor the other way round
    assert verify(config / "ca" / "ca.crt", leaf).returncode != 0
    web = authority.issue("wiki", ["wiki.home.arpa"])
    web_leaf = tmp_path / "web.crt"
    web_leaf.write_text(fullchain_path(web.name).read_text())
    assert verify(config / "tls" / "ca.crt", web_leaf).returncode != 0


@needs_openssl
@real
def test_the_database_authority_is_unchanged_by_sharing_its_machinery(
    config: Path, tmp_path: Path
) -> None:
    engines = TlsAuthority(directory=config / "tls", runner=SubprocessRunner())

    engines.ensure_ca()
    issued = engines.issue("pg17", ["127.0.0.1", "localhost"])

    root = text_of(config / "tls" / "ca.crt")
    assert "CA:TRUE, pathlen:0" in root and "Name Constraints" not in root
    leaf = tmp_path / "engine.crt"
    leaf.write_text(issued.certificate)
    described = text_of(leaf)
    assert "TLS Web Server Authentication, TLS Web Client Authentication" in described
    assert verify(config / "tls" / "ca.crt", leaf).returncode == 0


# -- what it signs ------------------------------------------------------------------------------


@needs_openssl
@real
def test_a_leaf_serves_and_nothing_else_and_verifies_against_the_root(
    authority: InternalCA, config: Path, tmp_path: Path
) -> None:
    authority.init()

    kept = authority.issue("Wiki", ["wiki.home.arpa", "10.0.0.5"])

    assert kept.name == "internal-wiki" and kept.uploaded_by == internal_ca.UPLOADED_BY
    assert kept.names == ("wiki.home.arpa",)  # what the web server routes by
    leaf = fullchain_path(kept.name)
    assert set(authority.authority.inspect(leaf).names) == {"wiki.home.arpa", "10.0.0.5"}
    described = text_of(leaf)
    assert "TLS Web Server Authentication" in described
    assert "TLS Web Client Authentication" not in described
    assert "CA:FALSE" in described
    result = verify(
        config / "ca" / "ca.crt",
        leaf,
        "-purpose",
        "sslserver",
        "-verify_hostname",
        "wiki.home.arpa",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    refused = verify(config / "ca" / "ca.crt", leaf, "-purpose", "sslclient")
    assert refused.returncode != 0
    assert stat.S_IMODE((leaf.parent / "privkey.pem").stat().st_mode) == 0o600
    assert stat.S_IMODE(leaf.parent.stat().st_mode) == 0o700


@needs_openssl
@real
def test_a_leaf_is_valid_for_ninety_days_unless_asked_otherwise(authority: InternalCA) -> None:
    authority.init()

    default = authority.issue("a", ["a.lan"])
    short = authority.issue("b", ["b.lan"], days=20)

    def span(name: str) -> int:
        info = authority.authority.inspect(fullchain_path(name))
        return (info.not_after - info.not_before).days

    assert 89 <= span(default.name) <= 90
    assert 19 <= span(short.name) <= 20


@needs_openssl
@real
def test_a_wildcard_covers_the_names_below_it_and_is_judged_by_its_base(
    authority: InternalCA, config: Path
) -> None:
    authority.init()

    kept = authority.issue("corp", ["*.corp.lan"])

    assert kept.names == ("*.corp.lan",)
    result = verify(
        config / "ca" / "ca.crt",
        fullchain_path(kept.name),
        "-verify_hostname",
        "wiki.corp.lan",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with pytest.raises(ValidationError):
        authority.issue("bad", ["*.example.com"])


@needs_openssl
@real
@pytest.mark.parametrize(
    "outside",
    ["bank.com", "www.example.org", "8.8.8.8", "93.184.216.34", "2606:4700::1111"],
)
def test_a_public_name_is_refused_before_openssl_is_asked(
    authority: InternalCA, outside: str
) -> None:
    authority.init()

    with pytest.raises(ValidationError) as raised:
        authority.issue("x", ["wiki.home.arpa", outside])

    assert raised.value.field == "names"
    assert "outside what this authority signs for" in raised.value.message
    assert authority.issued() == []


@needs_openssl
@real
def test_openssl_itself_refuses_a_leaf_for_a_public_name_signed_with_the_key(
    authority: InternalCA, config: Path, tmp_path: Path
) -> None:
    """
    The guarantee that matters: Noust's own check can be bypassed by whoever has the key, the
    constraint in the certificate cannot, because every client that trusts the root enforces it.
    """
    authority.init()
    directory = config / "ca"
    key = tmp_path / "leaf.key"
    request = tmp_path / "leaf.csr"
    leaf = tmp_path / "leaf.crt"
    extensions = tmp_path / "leaf.ext"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:www.bank.com\n"
    )
    assert (
        openssl("ecparam", "-genkey", "-name", "prime256v1", "-noout", "-out", str(key)).returncode
        == 0
    )
    assert (
        openssl(
            "req", "-new", "-key", str(key), "-subj", "/CN=www.bank.com", "-out", str(request)
        ).returncode
        == 0
    )
    signed = openssl(
        "x509", "-req", "-in", str(request), "-CA", str(directory / "ca.crt"),
        "-CAkey", str(directory / "ca.key"), "-CAcreateserial", "-days", "30",
        "-extfile", str(extensions), "-out", str(leaf),
    )  # fmt: skip
    assert signed.returncode == 0, signed.stderr

    result = verify(directory / "ca.crt", leaf)

    assert result.returncode != 0
    assert "permitted subtree violation" in result.stdout + result.stderr


@needs_openssl
@real
def test_what_the_operator_permits_is_added_to_the_defaults_or_replaces_them(
    store: NoustStore, config: Path
) -> None:
    runner = SubprocessRunner()
    both = InternalCA(
        uploads=CustomCertificates(CertificateStore(store), runner=runner), runner=runner
    )
    status = both.init(permit_dns=["corp.example.com"], permit_networks=["fd7a:115c:a1e0::/48"])
    assert "corp.example.com" in status.permitted_dns and "home.arpa" in status.permitted_dns
    assert "fd7a:115c:a1e0::/48" in status.permitted_networks
    both.remove(force=True)

    only = both.init(permit_dns=["corp.example.com"], only=True)

    assert only.permitted_dns == ("corp.example.com",)
    assert only.permitted_networks == ()
    with pytest.raises(ValidationError):
        both.issue("lan", ["wiki.lan"])  # a default, no longer permitted
    assert both.issue("corp", ["wiki.corp.example.com"]).name == "internal-corp"


# -- keeping it renewed ----------------------------------------------------------------------------


@needs_openssl
@real
def test_a_certificate_is_signed_again_for_the_same_names_when_it_is_due(
    authority: InternalCA, store: NoustStore
) -> None:
    authority.init()
    first = authority.issue("wiki", ["wiki.home.arpa", "192.168.1.10"], days=60)
    assert authority.renew_due() == []
    later = InternalCA(
        authority=authority.authority,
        uploads=authority.uploads,
        runner=SubprocessRunner(),
        now=datetime.now(timezone.utc) + timedelta(days=45),
    )
    assert [item.name for item in later.renew_due()] == ["internal-wiki"]

    renewed = later.renew("internal-wiki")

    assert renewed.name == "internal-wiki"
    assert set(renewed.names) == set(first.names)
    assert renewed.fingerprint != first.fingerprint
    info = authority.authority.inspect(fullchain_path("internal-wiki"))
    assert set(info.names) == {"wiki.home.arpa", "192.168.1.10"}  # the address was kept too
    assert 59 <= (info.not_after - info.not_before).days <= 60


@needs_openssl
@real
def test_a_certificate_the_authority_did_not_sign_is_never_renewed_by_it(
    authority: InternalCA,
) -> None:
    authority.init()
    # a certificate somebody uploaded under the same prefix is not ours
    from noust.core.certificate_store import CustomCertificate

    authority.uploads.records.save_custom(
        CustomCertificate(
            name="internal-lookalike",
            names=("x.lan",),
            subject="CN = x.lan",
            issuer="CN = x.lan",
            key_type="EC prime256v1",
            fingerprint="AA",
            not_before="2026-01-01T00:00:00+00:00",
            not_after="2030-01-01T00:00:00+00:00",
            uploaded_by="ana",
            uploaded_at="2026-01-01T00:00:00+00:00",
        )
    )

    with pytest.raises(CertificateError) as raised:
        authority.renew("internal-lookalike")

    assert "was not signed by the internal authority" in raised.value.message
    assert authority.issued() == []


@needs_openssl
@real
def test_the_authority_that_is_about_to_expire_is_a_warning_and_never_made_again(
    authority: InternalCA,
) -> None:
    short = authority.init(days=100)

    assert short.days_left <= 100
    assert any("expires in" in warning for warning in short.warnings)
    again = authority.status()
    assert again is not None and again.fingerprint == short.fingerprint
    # issuing still works while the root has time, and is cut to what it has left
    assert authority.issue("a", ["a.lan"], days=90).name == "internal-a"


@needs_openssl
@real
def test_a_leaf_never_outlives_the_root(authority: InternalCA) -> None:
    authority.init(days=40)

    authority.issue("a", ["a.lan"], days=90)

    info = authority.authority.inspect(fullchain_path("internal-a"))
    root = authority.authority.inspect(authority.authority.ca_path)
    assert info.not_after <= root.not_after


@needs_openssl
@real
def test_removing_the_authority_with_certificates_kept_needs_force(
    authority: InternalCA, config: Path
) -> None:
    authority.init()
    authority.issue("wiki", ["wiki.home.arpa"])

    with pytest.raises(ValidationError) as raised:
        authority.remove()

    assert "internal-wiki" in raised.value.message and raised.value.field == "force"
    assert (config / "ca" / "ca.key").exists()
    authority.remove(force=True)
    assert not (config / "ca").exists()
    assert authority.status() is None
    # certificates it signed stay as uploads: they are orphans, and the operator was told so
    assert authority.uploads.get("internal-wiki").uploaded_by == internal_ca.UPLOADED_BY


@needs_openssl
@real
def test_the_root_is_given_as_pem_and_as_der(authority: InternalCA, tmp_path: Path) -> None:
    status = authority.init()

    assert authority.root_pem().startswith("-----BEGIN CERTIFICATE-----")
    der = tmp_path / "ca.cer"
    der.write_bytes(authority.root_der())
    printed = openssl(
        "x509", "-inform", "der", "-in", str(der), "-noout", "-fingerprint", "-sha256"
    )
    assert status.fingerprint.upper() in printed.stdout.upper()


@needs_openssl
@real
def test_no_secret_is_on_a_command_line(authority: InternalCA) -> None:
    seen: list[list[str]] = []
    runner = SubprocessRunner()
    original = runner.run

    def record(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(list(argv))
        return original(argv, **kwargs)

    runner.run = record  # type: ignore[method-assign]
    mine = InternalCA(uploads=authority.uploads, runner=runner)
    mine.init()

    kept = mine.issue("wiki", ["wiki.home.arpa"])

    key = (fullchain_path(kept.name).parent / "privkey.pem").read_text().splitlines()[1]
    assert key not in " ".join(" ".join(argv) for argv in seen)
    assert all(argv[0] == "openssl" for argv in seen)


# -- the rules that need no process ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "canonical"),
    [
        ("lan", "lan"),
        (".home.arpa", "home.arpa"),
        ("*.Corp.Example.COM", "corp.example.com"),
        ("internal", "internal"),
    ],
)
def test_a_suffix_is_spelled_one_way(given: str, canonical: str) -> None:
    assert normal_suffix(given) == canonical


@pytest.mark.parametrize(
    "refused", ["com", "org", "uk", "", "has space", "a..b", "-x.lan", "a_b.lan", "x/y", "a\nb"]
)
def test_a_suffix_that_would_let_it_sign_for_a_public_domain_is_refused(refused: str) -> None:
    with pytest.raises(ValidationError) as raised:
        normal_suffix(refused)

    assert raised.value.field == "permit"


@pytest.mark.parametrize(
    "refused", ["0.0.0.0/0", "8.8.8.0/24", "::/0", "2001:db8::/32", "x", "10.0.0.0/33"]
)
def test_a_range_that_includes_the_public_internet_is_refused(refused: str) -> None:
    with pytest.raises(ValidationError) as raised:
        normal_network(refused)

    assert raised.value.field == "permit"


def test_a_private_range_and_a_single_address_are_accepted() -> None:
    assert str(normal_network("10.1.2.3")) == "10.1.2.3/32"
    assert str(normal_network("fd7a:115c:a1e0::/48")) == "fd7a:115c:a1e0::/48"
    assert str(normal_network("100.64.0.0/10")) == "100.64.0.0/10"


def test_a_name_is_permitted_by_a_suffix_or_a_range_and_not_by_a_lookalike() -> None:
    dns, networks = ("home.arpa", "lan"), ("10.0.0.0/8",)

    assert is_permitted("wiki.home.arpa", dns, networks)
    assert is_permitted("home.arpa", dns, networks)
    assert is_permitted("*.lan", dns, networks)
    assert is_permitted("10.4.4.4", dns, networks)
    assert not is_permitted("evilhome.arpa", dns, networks)
    assert not is_permitted("home.arpa.evil.com", dns, networks)
    assert not is_permitted("11.0.0.1", dns, networks)
    assert not is_permitted("::1", dns, networks)


def test_the_constraints_are_read_back_from_what_openssl_prints() -> None:
    printed = """\
X509v3 Name Constraints: critical
    Permitted:
      DNS:lan
      DNS:home.arpa
      IP:10.0.0.0/255.0.0.0
      IP:fc00::/fe00::
    Excluded:
      DNS:bad.lan
"""
    dns, networks = parse_name_constraints(printed)

    assert dns == ("lan", "home.arpa")
    assert networks == ("10.0.0.0/8", "fc00::/7")


@pytest.mark.parametrize(
    "names",
    [
        [],
        [""],
        ["10.0.0.1"],
        ["not a name"],
        ["a" * 300 + ".lan"],
        [f"h{n}.lan" for n in range(21)],
    ],
)
def test_names_that_cannot_be_on_a_certificate_are_refused(names: list[str]) -> None:
    with pytest.raises(ValidationError) as raised:
        InternalCA._names(names)

    assert raised.value.field == "names"


def test_names_are_lower_cased_deduplicated_and_an_address_is_spelled_one_way() -> None:
    assert InternalCA._names(["Wiki.LAN", "wiki.lan.", "10.0.0.5", "::1", "0:0:0:0:0:0:0:1"]) == [
        "wiki.lan",
        "10.0.0.5",
        "::1",
    ]


@pytest.mark.parametrize("label", ["", "...", "***"])
def test_a_label_with_nothing_to_keep_is_refused(authority: InternalCA, label: str) -> None:
    with pytest.raises(ValidationError) as raised:
        authority.issue(label, ["wiki.lan"])

    assert raised.value.field == "name"


def test_without_an_authority_nothing_is_signed_and_status_is_none(authority: InternalCA) -> None:
    assert authority.status() is None
    assert authority.exists() is False
    with pytest.raises(CertificateError) as raised:
        authority.issue("wiki", ["wiki.lan"])
    assert "noust cert ca init" in (raised.value.details or "")


@pytest.mark.parametrize("days", [0, 29, 7301])
def test_the_root_is_valid_for_30_days_to_20_years(authority: InternalCA, days: int) -> None:
    with pytest.raises(ValidationError) as raised:
        authority.init(days=days)

    assert raised.value.field == "days"


@pytest.mark.parametrize("name", ["x", "a;b", "a" * 65, "café/ca", "a\nb"])
def test_the_name_of_the_root_is_judged(authority: InternalCA, name: str) -> None:
    with pytest.raises(ValidationError) as raised:
        authority.init(name=name)

    assert raised.value.field == "name"
