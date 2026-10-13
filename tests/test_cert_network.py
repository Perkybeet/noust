# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a certificate comes to be: which authority, which challenge, an upload, a wildcard.

The defaults are pinned in ``tests/test_cert_manager.py`` and ``tests/test_cli_cert.py``
(the command of a server that never chose is exactly what it was). Pinned here is what
a choice changes in the command, and what it must not: no credential in an argument, a
wildcard never ordered over HTTP, an uploaded certificate never ordered, renewed or
revoked, and a certificate that follows the names it is meant to cover.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.certificate_store import CertificateSettings, CertificateStore, CustomCertificate
from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.exceptions import CertificateError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers import cert_manager as cert_module
from noust.managers.acme import EAB_SECRET, AcmeSettings
from noust.managers.cert_manager import CertManager
from noust.managers.dns.accounts import secret_name

LISTING = (
    "Found the following certs:\n"
    "  Certificate Name: example.com\n"
    "    Domains: {domains}\n"
    "    Expiry Date: 2026-11-30 12:00:00+00:00 (VALID: 60 days)\n"
    "    Certificate Path: /etc/letsencrypt/live/example.com/fullchain.pem\n"
    "    Private Key Path: /etc/letsencrypt/live/example.com/privkey.pem\n"
)


class MemoryConfig:
    """A configuration that lives in memory: ``get``, ``set`` and ``write``."""

    def __init__(self, **values: Any) -> None:
        self.values: dict[str, Any] = dict(values)
        self.written = 0

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def write(self) -> None:
        self.written += 1


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(App(domain="example.com", app_path="/var/www/apps/example.com"))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def config() -> MemoryConfig:
    return MemoryConfig()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "state" / "secrets")


@pytest.fixture
def certs(
    tmp_path: Path,
    runner: FakeRunner,
    store: NoustStore,
    config: MemoryConfig,
    secrets: SecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> CertManager:
    monkeypatch.setattr(cert_module, "find_noust_executable", lambda: "/usr/bin/noust")
    manager = CertManager()
    manager.LETSENCRYPT_DIR = tmp_path / "letsencrypt"
    manager.LIVE_DIR = manager.LETSENCRYPT_DIR / "live"
    manager._acme = AcmeSettings(config=config, secrets=secrets)  # type: ignore[arg-type]
    return manager


def put_lineage(manager: CertManager, name: str) -> None:
    live = manager.LIVE_DIR / name
    live.mkdir(parents=True, exist_ok=True)
    for file in ("fullchain.pem", "privkey.pem", "cert.pem", "chain.pem"):
        (live / file).write_text("-----BEGIN CERTIFICATE-----\n")


def orders(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[:2] == ("certbot", "certonly")]


def native_account(store: NoustStore, name: str = "cf") -> None:
    DnsStore(store).save_account(DnsAccount(name=name, kind="cloudflare"))


def use_dns(store: NoustStore, domain: str = "example.com", account: str = "cf") -> None:
    CertificateStore(store).set_settings(
        CertificateSettings(domain=domain, challenge="dns-01", dns_provider=account)
    )


# -- the authority -----------------------------------------------------------------


def test_staging_goes_to_the_staging_directory_and_nothing_else_changes(
    certs: CertManager, runner: FakeRunner, config: MemoryConfig
) -> None:
    config.values["ssl.ca"] = "letsencrypt-staging"

    certs.obtain("example.com", email="ops@example.com", standalone=True)

    (order,) = orders(runner)
    assert (
        order[order.index("--server") + 1]
        == "https://acme-staging-v02.api.letsencrypt.org/directory"
    )
    assert "--config" not in order


def test_an_authority_that_asks_for_an_external_account_gets_it_in_a_file_not_an_argument(
    certs: CertManager,
    runner: FakeRunner,
    config: MemoryConfig,
    secrets: SecretStore,
) -> None:
    config.values.update({"ssl.ca": "zerossl", "ssl.eab_kid": "kid-1234"})
    certs.acme.update(eab_hmac_key="s3cr3t-hmac-key")

    certs.obtain("example.com", standalone=True)

    (order,) = orders(runner)
    assert order[order.index("--server") + 1] == "https://acme.zerossl.com/v2/DV90"
    path = Path(order[order.index("--config") + 1])
    assert path == secrets.path(EAB_SECRET)
    assert path.read_text() == "eab-kid = kid-1234\neab-hmac-key = s3cr3t-hmac-key\n"
    assert not any("s3cr3t-hmac-key" in part for call in runner.calls for part in call)


def test_the_key_id_changing_rewrites_the_file_with_the_key_already_kept(
    certs: CertManager, config: MemoryConfig, secrets: SecretStore
) -> None:
    config.values.update({"ssl.ca": "zerossl", "ssl.eab_kid": "old-kid"})
    certs.acme.update(eab_hmac_key="the-hmac-key")

    certs.acme.update(eab_kid="new-kid")

    assert (
        secrets.path(EAB_SECRET).read_text() == "eab-kid = new-kid\neab-hmac-key = the-hmac-key\n"
    )
    assert certs.acme.current().eab_configured is True
    assert "the-hmac-key" not in str(certs.acme.current().to_dict())


def test_an_authority_that_needs_an_account_and_has_none_says_what_to_set(
    certs: CertManager, runner: FakeRunner, config: MemoryConfig
) -> None:
    config.values["ssl.ca"] = "google"

    with pytest.raises(ValidationError) as raised:
        certs.obtain("example.com", standalone=True)

    assert "noust cert acme" in raised.value.details
    assert orders(runner) == []


def test_a_custom_authority_needs_its_directory(certs: CertManager, config: MemoryConfig) -> None:
    with pytest.raises(ValidationError) as raised:
        certs.acme.update(ca="custom")
    assert raised.value.field == "acme_server"

    certs.acme.update(ca="custom", acme_server="https://acme.corp.example/directory")
    assert certs.acme.server_url() == "https://acme.corp.example/directory"


def test_registering_the_account_proves_the_external_account_up_front(
    certs: CertManager, runner: FakeRunner, config: MemoryConfig
) -> None:
    config.values.update({"ssl.ca": "zerossl", "ssl.eab_kid": "kid"})
    certs.acme.update(eab_hmac_key="hmac")

    assert certs.register_account(email="ops@example.com") is True

    (call,) = [c for c in runner.calls if c[:2] == ("certbot", "register")]
    assert "--email" in call and "--server" in call and "--config" in call


def test_an_account_that_already_exists_is_not_a_failure_and_a_refusal_is(
    certs: CertManager, runner: FakeRunner, config: MemoryConfig
) -> None:
    runner.script(
        ["certbot", "register"],
        stderr="There is an existing account; registration of a duplicate account is unsupported",
        exit_code=1,
    )
    assert certs.register_account() is True

    runner.script(["certbot", "register"], stderr="The server rejected the key id", exit_code=1)
    with pytest.raises(CertificateError) as raised:
        certs.register_account()
    assert "rejected the key id" in (raised.value.output or "")


# -- DNS-01 -----------------------------------------------------------------------


def test_dns_is_answered_by_noust_itself_for_cloudflare_with_no_credential_in_the_command(
    certs: CertManager, runner: FakeRunner, store: NoustStore
) -> None:
    native_account(store)
    use_dns(store)

    certs.obtain("example.com", additional_domains=["*.example.com"])

    (order,) = orders(runner)
    assert order[order.index("-d") + 1] == "example.com" and "*.example.com" in order
    assert "--manual" in order
    assert order[order.index("--preferred-challenges") + 1] == "dns"
    auth = order[order.index("--manual-auth-hook") + 1]
    assert auth == (
        "/usr/bin/noust dns challenge auth --account cf --reason 'certificate challenge'"
    )
    cleanup = order[order.index("--manual-cleanup-hook") + 1]
    assert cleanup.startswith("/usr/bin/noust dns challenge cleanup --account cf")
    assert "--webroot" not in order and "--nginx" not in order and "--standalone" not in order


def test_a_plugin_account_is_answered_by_certbots_plugin_with_its_credentials_file(
    certs: CertManager,
    runner: FakeRunner,
    store: NoustStore,
    secrets: SecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    DnsStore(store).save_account(DnsAccount(name="do", kind="digitalocean"))
    secrets.write(secret_name("do"), "dns_digitalocean_token = abc\n")
    monkeypatch.setattr("noust.managers.dns.accounts.SecretStore", lambda: secrets)
    use_dns(store, account="do")

    certs.obtain("example.com")

    (order,) = orders(runner)
    assert order[order.index("--authenticator") + 1] == "dns-digitalocean"
    assert order[order.index("--dns-digitalocean-credentials") + 1] == str(
        secrets.path(secret_name("do"))
    )


def test_a_wildcard_is_never_ordered_over_http(certs: CertManager, runner: FakeRunner) -> None:
    with pytest.raises(CertificateError) as raised:
        certs.obtain("example.com", additional_domains=["*.example.com"], standalone=True)

    assert "noust cert use example.com --acme --dns NAME" in raised.value.details
    assert orders(runner) == []


def test_a_wildcard_cannot_be_the_name_of_a_lineage(certs: CertManager) -> None:
    with pytest.raises(CertificateError):
        certs.obtain("*.example.com", standalone=True)


def test_an_expansion_keeps_the_challenge_the_certificate_was_ordered_with(
    certs: CertManager, runner: FakeRunner, store: NoustStore
) -> None:
    native_account(store)
    use_dns(store)
    put_lineage(certs, "example.com")
    runner.script(
        ["certbot", "certificates"], stdout=LISTING.format(domains="example.com *.example.com")
    )
    store.add_domain("example.com", "shop.example.com", "alias")

    certs.obtain("example.com")

    (order,) = orders(runner)
    assert "--manual" in order and "--expand" in order
    assert "*.example.com" in order


# -- one label under a wildcard -------------------------------------------------------


def test_a_name_under_an_existing_wildcard_is_given_that_certificate_instead_of_an_order(
    certs: CertManager, runner: FakeRunner, store: NoustStore
) -> None:
    store.create_app(App(domain="pr-3.example.com", app_path="/x"))
    put_lineage(certs, "example.com")
    runner.script(
        ["certbot", "certificates"], stdout=LISTING.format(domains="example.com *.example.com")
    )

    assert certs.obtain("pr-3.example.com", standalone=True) is True

    assert orders(runner) == []
    settings = CertificateStore(store).get_settings("pr-3.example.com")
    assert (settings.source, settings.name) == ("lineage", "example.com")
    paths = certs.served_paths("pr-3.example.com")
    assert paths.fullchain == certs.LIVE_DIR / "example.com" / "fullchain.pem"
    assert certs.cert_exists("pr-3.example.com") is True


def test_the_wildcard_is_not_reused_when_it_does_not_cover_the_name_or_when_turned_off(
    certs: CertManager, runner: FakeRunner, store: NoustStore, config: MemoryConfig
) -> None:
    store.create_app(App(domain="a.b.example.com", app_path="/x"))
    put_lineage(certs, "example.com")
    runner.script(
        ["certbot", "certificates"], stdout=LISTING.format(domains="example.com *.example.com")
    )

    certs.obtain("a.b.example.com", standalone=True)  # two labels: not covered
    assert len(orders(runner)) == 1

    runner.calls.clear()
    store.create_app(App(domain="pr-4.example.com", app_path="/x"))
    config.values["ssl.reuse_wildcard"] = False
    certs.config = type("Cfg", (), {"ssl_email": "", "get": lambda self, k, d=None: d})()  # type: ignore[assignment]
    certs._acme = AcmeSettings(config=config, secrets=certs.acme.secrets)  # type: ignore[arg-type]
    certs.obtain("pr-4.example.com", standalone=True)
    assert len(orders(runner)) == 1


def test_a_lineage_that_is_gone_is_said_not_silently_reordered(
    certs: CertManager, runner: FakeRunner, store: NoustStore
) -> None:
    CertificateStore(store).set_settings(
        CertificateSettings(domain="example.com", source="lineage", name="gone.example.org")
    )

    with pytest.raises(CertificateError) as raised:
        certs.obtain("example.com", standalone=True)

    assert "is not on disk" in raised.value.message
    assert orders(runner) == []


# -- uploaded certificates -----------------------------------------------------------


def upload(store: NoustStore, certs: CertManager, *names: str, name: str = "origin") -> None:
    directory = certs.LETSENCRYPT_DIR.parent / "uploaded"
    CertificateStore(store).save_custom(
        CustomCertificate(
            name=name,
            names=names,
            subject="CN = origin",
            issuer="CN = Cloudflare Origin CA",
            key_type="RSA 2048",
            fingerprint="AA:BB",
            not_before="2026-01-01T00:00:00+00:00",
            not_after="2031-01-01T00:00:00+00:00",
            uploaded_by="test",
            uploaded_at="2026-10-13T00:00:00+00:00",
        )
    )
    CertificateStore(store).set_settings(
        CertificateSettings(domain="example.com", source="custom", name=name)
    )
    assert directory is not None


@pytest.fixture
def uploaded_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Noust's certificates directory, in the test's own tree, holding the upload's files."""
    from noust.core import paths

    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "etc-noust")
    directory = tmp_path / "etc-noust" / "certs" / "origin"
    directory.mkdir(parents=True)
    (directory / "fullchain.pem").write_text("x")
    (directory / "privkey.pem").write_text("x")
    return directory


def test_an_uploaded_certificate_is_served_and_nothing_is_ordered(
    certs: CertManager, runner: FakeRunner, store: NoustStore, uploaded_files: Path
) -> None:
    upload(store, certs, "example.com", "*.example.com")

    assert certs.obtain("example.com") is True

    assert orders(runner) == []
    assert certs.served_paths("example.com").privkey == uploaded_files / "privkey.pem"
    assert certs.served_paths("example.com").fullchain == uploaded_files / "fullchain.pem"
    assert certs.cert_exists("example.com") is True


def test_an_uploaded_certificate_that_does_not_cover_a_name_says_which(
    certs: CertManager, store: NoustStore, uploaded_files: Path
) -> None:
    upload(store, certs, "example.com")
    store.add_domain("example.com", "shop.example.com", "alias")

    with pytest.raises(CertificateError) as raised:
        certs.obtain("example.com")

    assert "shop.example.com" in raised.value.message


def test_uploads_are_listed_with_the_certificates_and_expire_with_them(
    certs: CertManager, runner: FakeRunner, store: NoustStore
) -> None:
    runner.script(["certbot", "certificates"], stdout=LISTING.format(domains="example.com"))
    upload(store, certs, "other.example.org", name="origin")

    names = {(c.name, c.source) for c in certs.list_certificates()}

    assert names == {("example.com", "letsencrypt"), ("origin", "custom")}
    origin = certs.get_cert_info("example.com")
    assert origin is not None and origin.name == "origin"
    assert origin.expiry == "2031-01-01"


def test_an_uploaded_certificate_is_neither_renewed_nor_revoked_and_deleting_forgets_it(
    certs: CertManager, runner: FakeRunner, store: NoustStore
) -> None:
    upload(store, certs, "example.com")

    with pytest.raises(CertificateError) as renewing:
        certs.renew("example.com")
    with pytest.raises(CertificateError) as revoking:
        certs.revoke("example.com")

    assert "not renewed" in renewing.value.message
    assert "not served with a certificate Noust ordered" in revoking.value.message
    assert certs.delete("example.com") is True
    assert CertificateStore(store).get_settings("example.com").source == "acme"
    assert CertificateStore(store).get_custom("origin") is not None  # the upload stays
    assert not [c for c in runner.calls if c[:2] in (("certbot", "delete"), ("certbot", "revoke"))]


# -- ordering again with fewer names ----------------------------------------------------


def test_a_certificate_with_a_name_too_many_is_ordered_again_with_exactly_the_rest(
    certs: CertManager, runner: FakeRunner
) -> None:
    put_lineage(certs, "example.com")
    runner.script(
        ["certbot", "certificates"], stdout=LISTING.format(domains="example.com shop.example.com")
    )

    assert certs.reissue("example.com", ["example.com"], nginx=True) is True

    (order,) = orders(runner)
    assert [order[i + 1] for i, a in enumerate(order) if a == "-d"] == ["example.com"]
    assert "--expand" not in order
    assert order[:4] == ("certbot", "certonly", "--cert-name", "example.com")


def test_nothing_is_ordered_when_the_certificate_already_covers_exactly_the_names(
    certs: CertManager, runner: FakeRunner
) -> None:
    put_lineage(certs, "example.com")
    runner.script(
        ["certbot", "certificates"], stdout=LISTING.format(domains="example.com www.example.com")
    )

    assert certs.reissue("example.com", ["www.example.com", "example.com"]) is False
    assert orders(runner) == []


def test_reissuing_never_touches_an_upload_or_a_missing_lineage(
    certs: CertManager, runner: FakeRunner, store: NoustStore
) -> None:
    assert certs.reissue("example.com", ["example.com"]) is False  # no lineage yet
    upload(store, certs, "example.com")
    assert certs.reissue("example.com", ["example.com"]) is False
    assert orders(runner) == []


def test_reissuing_must_keep_the_lineage_name_and_reports_certbots_words(
    certs: CertManager, runner: FakeRunner
) -> None:
    put_lineage(certs, "example.com")
    runner.script(
        ["certbot", "certificates"], stdout=LISTING.format(domains="example.com old.example.com")
    )
    with pytest.raises(CertificateError):
        certs.reissue("example.com", ["other.example.com"])

    runner.script(
        ["certbot", "certonly"], stderr="too many certificates already issued", exit_code=1
    )
    with pytest.raises(CertificateError) as raised:
        certs.reissue("example.com", ["example.com"])

    assert "without old.example.com" in raised.value.message
    assert "too many certificates" in (raised.value.output or "")


# -- the renewal loads the certificate ----------------------------------------------------


def test_renewal_is_set_up_with_the_script_that_reloads_the_web_server(
    certs: CertManager, runner: FakeRunner
) -> None:
    assert certs.setup_auto_renewal() is True

    hook = certs.LETSENCRYPT_DIR / "renewal-hooks" / "deploy" / "noust-reload"
    text = hook.read_text()
    assert text.startswith("#!/bin/sh\n# Generated by Noust")
    assert "nginx -t" in text and "systemctl reload" in text
    assert hook.stat().st_mode & 0o777 == 0o755


def test_a_hook_the_operator_wrote_is_left_alone(certs: CertManager) -> None:
    hook = certs.LETSENCRYPT_DIR / "renewal-hooks" / "deploy" / "noust-reload"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\necho mine\n")

    certs.setup_auto_renewal()

    assert hook.read_text() == "#!/bin/sh\necho mine\n"
