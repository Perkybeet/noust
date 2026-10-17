# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Signing in through a SAML 2.0 identity provider (checklist I-20): configuring it, and what a
sign-in does with a response that is right and with every response that is not.

The identity provider is ``tests/saml_support.FakeIdp``: it answers the AuthnRequest it was sent,
signs with a real key, and can be told to sign wrongly, address the wrong server or say the wrong
time. The XML security itself is held to the standard in ``tests/test_saml_xml.py``; what these
tests say is that a response which fails any condition never becomes a ticket.
"""

# ruff: noqa: F811 - the fixtures imported from the manager tests are used by name

from __future__ import annotations

import base64
import datetime
from typing import Any

import pytest

from noust.core.accounts import AccountManager
from noust.core.accounts import saml_xml as sx
from noust.core.accounts.local_login import LocalLoginRefused
from noust.core.accounts.oidc_model import OidcError, OidcRefused
from noust.core.accounts.oidc_store import digest
from noust.core.accounts.saml import (
    ACS_PATH,
    METADATA_PATH,
    SamlManager,
    SamlRefused,
    decode_response,
    format_instant,
    parse_instant,
    read_idp_metadata,
    sp_metadata,
)
from noust.core.exceptions import ValidationError
from noust.core.teams import TeamStore
from tests.oidc_support import Clock
from tests.saml_support import (
    NS_SAML,
    FakeIdp,
    Scenario,
    make_identity,
    read_redirect,
)
from tests.test_oidc_manager import (  # noqa: F401 - the fixtures of the manager tests
    accounts,
    admin,
    cheap_hashes,
    clock,
    secure,
    store,
)

ORIGIN = "https://noust.acme.test"
ISSUER = "https://idp.acme.test/metadata"
SSO = "https://idp.acme.test/sso"
IP = "203.0.113.5"
ENTITY = f"{ORIGIN}{METADATA_PATH}/corp"
ACS = f"{ORIGIN}{ACS_PATH}"


@pytest.fixture
def saml(store: Any, accounts: AccountManager, clock: Clock) -> SamlManager:
    return SamlManager(store, accounts=accounts, clock=clock)


@pytest.fixture
def idp(clock: Clock) -> FakeIdp:
    key, _pem = make_identity()
    return FakeIdp(key, lambda: datetime.datetime.fromtimestamp(clock.now, datetime.timezone.utc))


def configure(saml: SamlManager, **changes: Any) -> Any:
    _key, pem = make_identity()
    settings = {
        "idp_entity_id": ISSUER,
        "sso_url": SSO,
        "certificates": [pem],
        "label": "Acme SSO",
        **changes,
    }
    return saml.configure("corp", settings)


def begin(saml: SamlManager) -> Any:
    return saml.begin("corp", origin=ORIGIN, client_ip=IP, next_path="/apps")


def finish(
    saml: SamlManager, begun: Any, response: str, relay: str, *, binding: Any = "same"
) -> Any:
    return saml.finish(
        response=response,
        relay_state=relay,
        binding=begun.binding if binding == "same" else binding,
        client_ip=IP,
    )


def run(saml: SamlManager, idp: FakeIdp, **answer: Any) -> Any:
    """One whole sign-in, from the click to the ticket."""
    begun = begin(saml)
    response, relay = idp.answer(begun.url, **answer)
    return finish(saml, begun, response, relay)


def refused(saml: SamlManager, idp: FakeIdp, **answer: Any) -> OidcError:
    with pytest.raises(OidcError) as caught:
        run(saml, idp, **answer)
    return caught.value


METADATA = """<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="https://idp.acme.test/metadata">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data>
      <ds:X509Certificate>{cert}</ds:X509Certificate>
    </ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
    <md:KeyDescriptor use="encryption"><ds:KeyInfo><ds:X509Data>
      <ds:X509Certificate>ZW5jcnlwdGlvbg==</ds:X509Certificate>
    </ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
        Location="https://idp.acme.test/post"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://idp.acme.test/sso"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>"""


def metadata_xml() -> str:
    _key, pem = make_identity()
    body = "".join(pem.strip().splitlines()[1:-1])
    return METADATA.format(cert=body)


class TestInstants:
    def test_an_instant_round_trips(self) -> None:
        assert format_instant(parse_instant("2026-10-17T00:12:30Z")) == "2026-10-17T00:12:30Z"

    def test_fractions_of_a_second_are_accepted(self) -> None:
        assert parse_instant("2026-10-17T00:12:30.250Z") == parse_instant("2026-10-17T00:12:30Z")

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "yesterday",
            "2026-10-17T00:12:30",
            "2026-10-17T00:12:30+02:00",
            "2026-13-17T00:12:30Z",
        ],
    )
    def test_anything_else_is_refused(self, text: str) -> None:
        with pytest.raises(SamlRefused):
            parse_instant(text)


class TestMetadata:
    def test_the_identity_providers_metadata_gives_what_a_sign_in_needs(self) -> None:
        read = read_idp_metadata(metadata_xml().encode())

        assert read.entity_id == ISSUER
        assert read.sso_url == SSO
        # The encryption key the document also lists is not one a signature is checked with.
        assert len(read.certificates) == 1 and read.certificates[0].startswith("MII")

    def test_a_document_that_is_not_an_identity_provider_is_refused(self) -> None:
        document = (
            '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="x">'
            "<md:SPSSODescriptor/></md:EntityDescriptor>"
        )
        with pytest.raises(ValidationError, match="does not describe an identity provider"):
            read_idp_metadata(document.encode())

    def test_a_document_without_a_redirect_sign_in_is_refused(self) -> None:
        document = metadata_xml().replace("HTTP-Redirect", "SOAP")
        with pytest.raises(ValidationError, match="HTTP-Redirect"):
            read_idp_metadata(document.encode())

    def test_a_document_with_a_dtd_is_refused(self) -> None:
        document = '<!DOCTYPE x [<!ENTITY a "b">]>' + metadata_xml()
        with pytest.raises(ValidationError, match="DTD"):
            read_idp_metadata(document.encode())

    def test_ours_says_where_to_post_and_that_assertions_must_be_signed(
        self, saml: SamlManager
    ) -> None:
        provider = configure(saml)

        text = sp_metadata(provider, origin=ORIGIN)

        root = sx.root_of(sx.parse(text.encode()))
        assert root.getAttribute("entityID") == ENTITY
        assert f'Location="{ACS}"' in text
        assert 'WantAssertionsSigned="true"' in text and 'AuthnRequestsSigned="false"' in text

    def test_an_entity_id_the_operator_chose_is_the_one_presented(self, saml: SamlManager) -> None:
        provider = configure(saml, sp_entity_id="urn:acme:noust")

        assert 'entityID="urn:acme:noust"' in sp_metadata(provider, origin=ORIGIN)


class TestConfiguration:
    def test_a_provider_is_added_with_its_metadata(self, saml: SamlManager) -> None:
        provider = saml.configure("corp", {"idp_metadata": metadata_xml(), "label": "Acme SSO"})

        assert (provider.idp_entity_id, provider.sso_url) == (ISSUER, SSO)
        assert len(provider.certificates) == 1
        assert provider.protocol == "saml" and provider.issuer == ISSUER
        assert saml.public_providers() == [{"name": "corp", "label": "Acme SSO"}]

    def test_a_setting_beside_the_metadata_overrides_it(self, saml: SamlManager) -> None:
        provider = saml.configure(
            "corp", {"idp_metadata": metadata_xml(), "sso_url": "https://idp.acme.test/other"}
        )

        assert provider.sso_url == "https://idp.acme.test/other"

    def test_the_certificates_are_kept_in_pem_and_nothing_secret_is_stored(
        self, saml: SamlManager
    ) -> None:
        provider = configure(saml)

        assert provider.certificates[0].startswith("-----BEGIN CERTIFICATE-----")
        assert saml.provider("corp") == provider

    def test_a_new_provider_needs_all_three_halves(self, saml: SamlManager) -> None:
        with pytest.raises(ValidationError, match="entity id, sign-in URL and certificate"):
            saml.configure("corp", {"idp_entity_id": ISSUER})
        assert saml.providers() == []

    @pytest.mark.parametrize(
        ("setting", "value", "message"),
        [
            ("sso_url", "http://idp.acme.test/sso", "https"),
            ("sso_url", "https://user:pw@idp.acme.test/sso", "credentials"),
            ("idp_entity_id", "has space", "Invalid idp_entity_id"),
            ("certificates", ["not a certificate"], "Not a certificate"),
            ("clock_skew_seconds", "9999", "skew"),
            ("provisioning", "everyone", "provisioning"),
            ("default_role", "security", "cannot start"),
            ("subject_attribute", "has space", "Invalid subject_attribute"),
        ],
    )
    def test_what_is_wrong_is_refused_beside_its_setting(
        self, saml: SamlManager, setting: str, value: Any, message: str
    ) -> None:
        with pytest.raises(ValidationError, match=message):
            configure(saml, **{setting: value})

        assert saml.providers() == []

    def test_a_setting_that_does_not_exist_is_refused(self, saml: SamlManager) -> None:
        with pytest.raises(ValidationError, match="Unknown setting"):
            configure(saml, client_secret="x")

    def test_a_certificate_with_a_weak_key_is_refused(self, saml: SamlManager) -> None:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(65537, 1024)  # noqa: S505 - weak on purpose, to see it refused
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "weak")])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=30))
            .sign(key, hashes.SHA256())
        )
        pem = certificate.public_bytes(serialization.Encoding.PEM).decode()

        with pytest.raises(ValidationError, match="1024 bits"):
            configure(saml, certificates=[pem])

    def test_a_name_another_way_of_signing_in_has_is_refused(
        self, saml: SamlManager, store: Any, accounts: AccountManager
    ) -> None:
        from noust.core.accounts.oidc import OidcManager

        OidcManager(store, accounts=accounts).configure(
            "corp", {"issuer": "https://sso.acme.test", "client_id": "x"}
        )

        with pytest.raises(ValidationError, match="already a sign-in provider"):
            configure(saml)

    def test_the_name_is_refused_to_the_other_ways_too(
        self, saml: SamlManager, store: Any, accounts: AccountManager
    ) -> None:
        from noust.core.accounts.oidc import OidcManager

        configure(saml)

        with pytest.raises(ValidationError, match="already a sign-in provider"):
            OidcManager(store, accounts=accounts).configure(
                "corp", {"issuer": "https://sso.acme.test", "client_id": "x"}
            )

    def test_changing_one_setting_leaves_the_rest(self, saml: SamlManager) -> None:
        configure(saml, provisioning="auto", default_role="operator")

        provider = saml.configure("corp", {"label": "Acme single sign-on"})

        assert provider.label == "Acme single sign-on"
        assert (provider.provisioning, provider.default_role) == ("auto", "operator")

    def test_a_team_that_does_not_exist_is_refused(self, saml: SamlManager) -> None:
        with pytest.raises(ValidationError):
            configure(saml, default_team="ghost")

    def test_the_provider_is_removed(self, saml: SamlManager, admin: Any) -> None:
        configure(saml)

        removed = saml.remove("corp")

        assert removed.name == "corp" and saml.providers() == []
        with pytest.raises(ValidationError, match="No SAML provider"):
            saml.remove("corp")

    def test_a_linked_administrator_counts_as_a_way_in_while_the_provider_is_enabled(
        self, saml: SamlManager, admin: Any
    ) -> None:
        from noust.core.accounts.local_login import survivors

        configure(saml)
        saml.link("corp", "root-admin", "uuid-root")

        assert survivors(saml.accounts.store, exempt=[]) == ["root-admin"]
        assert survivors(saml.accounts.store, exempt=[], without_provider="corp") == []
        saml.configure("corp", {"enabled": False})
        assert survivors(saml.accounts.store, exempt=[]) == []

    def test_the_last_way_in_is_not_taken_away_while_local_sign_in_is_off(
        self, saml: SamlManager, admin: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.accounts import local_login

        configure(saml)
        saml.link("corp", "root-admin", "uuid-root")
        policy = local_login.LocalLoginPolicy(enabled=False, exempt=frozenset())
        monkeypatch.setattr(local_login, "load_policy", lambda: policy)

        with pytest.raises(LocalLoginRefused, match="nobody able to administer"):
            saml.remove("corp")
        with pytest.raises(LocalLoginRefused, match="nobody able to administer"):
            saml.configure("corp", {"enabled": False})
        assert saml.provider("corp") is not None

    def test_the_provider_is_checked_without_signing_anybody_in(self, saml: SamlManager) -> None:
        configure(saml)

        checks = saml.test("corp")

        assert [c.name for c in checks] == ["certificate", "sign-in address"]
        assert all(c.ok for c in checks), [c.detail for c in checks]
        assert "good for" in checks[0].detail

    def test_an_expired_certificate_is_reported(self, saml: SamlManager, clock: Clock) -> None:
        configure(saml)
        clock.advance(3 * 365 * 86400)

        checks = saml.test("corp")

        assert not checks[0].ok and "expired" in checks[0].detail


class TestSigningIn:
    @pytest.fixture(autouse=True)
    def ready(self, saml: SamlManager, admin: Any, accounts: AccountManager) -> None:
        self.saml = saml
        self.accounts = accounts

    def test_the_request_goes_to_the_provider_naming_this_server(self) -> None:
        configure(self.saml)

        begun = begin(self.saml)

        assert begun.url.startswith(SSO + "?SAMLRequest=")
        request, relay = read_redirect(begun.url)
        assert request.getAttribute("AssertionConsumerServiceURL") == ACS
        assert request.getAttribute("Destination") == SSO
        assert request.getAttribute("ID").startswith("_")
        assert sx.text_of(sx.children(request, NS_SAML, "Issuer")[0]) == ENTITY
        assert relay and begun.binding

    def test_a_provider_that_is_off_or_unknown_cannot_be_started(self) -> None:
        configure(self.saml, enabled=False)

        with pytest.raises(OidcError) as off:
            begin(self.saml)
        assert off.value.public == "unavailable"
        with pytest.raises(OidcError) as unknown:
            self.saml.begin("nope", origin=ORIGIN, client_ip=IP)
        assert unknown.value.reason == "unknown_provider"

    def test_a_person_the_provider_vouches_for_is_admitted_and_their_account_made(
        self, idp: FakeIdp
    ) -> None:
        configure(self.saml, provisioning="auto", default_role="operator")

        answer = run(self.saml, idp)

        account = self.accounts.require("maria")
        assert answer.account.id == account.id and answer.ticket
        assert (account.role, account.person_ref) == ("operator", "maria@acme.test")
        assert account.display_name == "Maria Lopez"
        assert answer.next_path == "/apps"
        identity = self.saml.admission.data.find_identity(ISSUER, "uuid-maria", "saml")
        assert identity is not None and identity.provider == "corp"

    @pytest.mark.parametrize("sign_what", ["assertion", "response", "both"])
    def test_a_signature_on_the_assertion_or_the_response_is_enough(
        self, idp: FakeIdp, sign_what: str
    ) -> None:
        configure(self.saml, provisioning="auto")

        assert run(self.saml, idp, sign_what=sign_what).account.username == "maria"

    def test_a_provider_signing_with_ecdsa_is_accepted(self, clock: Clock) -> None:
        key, pem = make_identity("ec", "idp-ec.acme.test")
        configure(self.saml, certificates=[pem], provisioning="auto")
        ec_idp = FakeIdp(
            key, lambda: datetime.datetime.fromtimestamp(clock.now, datetime.timezone.utc)
        )

        assert run(self.saml, ec_idp).account.username == "maria"

    def test_a_certificate_in_a_rolled_over_pair_is_enough(self, clock: Clock) -> None:
        old_key, old_pem = make_identity("rsa", "old.acme.test")
        _new_key, new_pem = make_identity("rsa", "new.acme.test")
        configure(self.saml, certificates=[new_pem, old_pem], provisioning="auto")
        old_idp = FakeIdp(
            old_key, lambda: datetime.datetime.fromtimestamp(clock.now, datetime.timezone.utc)
        )

        assert run(self.saml, old_idp).account.username == "maria"

    def test_the_second_factor_is_still_owed(self, idp: FakeIdp) -> None:
        configure(self.saml, provisioning="auto")

        answer = run(self.saml, idp)
        ticket = self.saml.admission.data.get_ticket(digest(answer.ticket))

        assert ticket is not None and ticket.amr == (
            "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport",
        )
        assert ticket.provider == "corp"

    def test_the_email_is_read_from_a_nameid_that_declares_itself_one(self, idp: FakeIdp) -> None:
        configure(self.saml, provisioning="auto", email_attribute="")

        answer = run(
            self.saml,
            idp,
            name_id="Maria.Lopez@acme.test",
            name_id_format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            attributes={"displayName": ["Maria Lopez"]},
        )

        assert answer.account.person_ref == "maria.lopez@acme.test"

    def test_a_persistent_nameid_is_never_taken_for_an_address(self, idp: FakeIdp) -> None:
        configure(self.saml, provisioning="auto", email_attribute="")

        answer = run(
            self.saml,
            idp,
            name_id="maria@acme.test",
            attributes={"displayName": ["Maria Lopez"]},
        )

        assert answer.account.person_ref is None

    def test_an_attribute_can_identify_the_person_instead_of_the_nameid(self, idp: FakeIdp) -> None:
        configure(self.saml, provisioning="auto", subject_attribute="employeeNumber")

        run(self.saml, idp, attributes={"employeeNumber": ["E-1042"], "email": ["maria@acme.test"]})

        assert self.saml.admission.data.find_identity(ISSUER, "E-1042", "saml") is not None

    def test_a_transient_nameid_does_not_identify_anyone(self, idp: FakeIdp) -> None:
        configure(self.saml, provisioning="auto")

        exc = refused(
            self.saml,
            idp,
            name_id="_one-time-value",
            name_id_format="urn:oasis:names:tc:SAML:2.0:nameid-format:transient",
        )

        assert isinstance(exc, OidcRefused) and exc.reason == "transient_subject"
        assert self.accounts.find("maria") is None

    def test_a_transient_nameid_is_fine_when_an_attribute_identifies_the_person(
        self, idp: FakeIdp
    ) -> None:
        configure(self.saml, provisioning="auto", subject_attribute="employeeNumber")

        run(
            self.saml,
            idp,
            name_id="_one-time-value",
            name_id_format="urn:oasis:names:tc:SAML:2.0:nameid-format:transient",
            attributes={"employeeNumber": ["E-1042"], "email": ["maria@acme.test"]},
        )

        assert self.saml.admission.data.find_identity(ISSUER, "E-1042", "saml") is not None

    def test_an_assertion_that_does_not_identify_anyone_is_refused(self, idp: FakeIdp) -> None:
        configure(self.saml, provisioning="auto", subject_attribute="employeeNumber")

        exc = refused(self.saml, idp)

        assert isinstance(exc, OidcRefused) and exc.reason == "no_subject"

    def test_a_person_who_has_no_account_is_refused_when_provisioning_is_off(
        self, idp: FakeIdp
    ) -> None:
        configure(self.saml)

        exc = refused(self.saml, idp)

        assert isinstance(exc, OidcRefused) and exc.reason == "no_account"
        assert self.accounts.find("maria") is None

    def test_an_address_outside_the_allowed_domains_is_refused(self, idp: FakeIdp) -> None:
        configure(self.saml, provisioning="auto", allowed_domains=["other.test"])

        assert refused(self.saml, idp).reason == "domain_not_allowed"

    def test_a_required_authentication_class_is_checked(self, idp: FakeIdp) -> None:
        mfa = "urn:oasis:names:tc:SAML:2.0:ac:classes:TimeSyncToken"
        configure(self.saml, provisioning="auto", require_amr=[mfa])

        assert refused(self.saml, idp).reason == "amr_missing"
        assert run(self.saml, idp, authn_context=mfa).account.username == "maria"

    def test_an_email_links_an_account_that_has_a_second_factor_when_trusted(
        self, idp: FakeIdp
    ) -> None:
        configure(self.saml, link_by_email=True)
        secure(self.accounts, "maria", person_ref="maria@acme.test")

        answer = run(self.saml, idp)

        assert answer.account.username == "maria"
        assert self.saml.admission.data.find_identity(ISSUER, "uuid-maria", "saml") is not None

    def test_an_explicit_link_needs_no_email(self, idp: FakeIdp) -> None:
        configure(self.saml)
        secure(self.accounts, "maria")

        identity = self.saml.link("corp", "maria", "uuid-maria", actor="root-admin")

        assert (identity.issuer, identity.subject, identity.protocol) == (
            ISSUER,
            "uuid-maria",
            "saml",
        )
        assert run(self.saml, idp).account.username == "maria"

    def test_a_subject_that_is_not_one_cannot_be_linked(self) -> None:
        configure(self.saml)
        secure(self.accounts, "maria")

        with pytest.raises(ValidationError, match="Invalid subject"):
            self.saml.link("corp", "maria", "  ")

    def test_groups_give_roles_on_every_sign_in(self, idp: FakeIdp) -> None:
        configure(
            self.saml,
            provisioning="auto",
            group_sync=True,
            group_mappings=[
                {"group": "ops", "role": "operator"},
                {"group": "devs", "role": "viewer"},
            ],
        )

        run(self.saml, idp)
        assert self.accounts.require("maria").role == "operator"

        run(self.saml, idp, attributes={"email": ["maria@acme.test"], "groups": ["devs"]})
        assert self.accounts.require("maria").role == "viewer"

    def test_groups_give_team_memberships(self, idp: FakeIdp) -> None:
        TeamStore(self.accounts.store).create_team("Acme", slug="acme")
        configure(
            self.saml,
            provisioning="auto",
            group_sync=True,
            group_mappings=[{"group": "ops", "team": "acme", "team_role": "operator"}],
        )

        run(self.saml, idp)

        teams = {
            team.slug: role
            for team, role in TeamStore(self.accounts.store).teams_of(
                self.accounts.require("maria").id
            )
        }
        assert teams["acme"] == "operator"

    def test_the_groups_attribute_can_be_named(self, idp: FakeIdp) -> None:
        configure(
            self.saml,
            provisioning="auto",
            group_sync=True,
            groups_attribute="http://schemas.xmlsoap.org/claims/Group",
            group_mappings=[{"group": "ops", "role": "operator"}],
        )

        run(
            self.saml,
            idp,
            attributes={
                "email": ["maria@acme.test"],
                "http://schemas.xmlsoap.org/claims/Group": ["ops"],
            },
        )

        assert self.accounts.require("maria").role == "operator"


class TestResponsesThatAreNotRight:
    """Each of these is a response a real attacker or a misconfigured provider could send."""

    @pytest.fixture(autouse=True)
    def ready(self, saml: SamlManager, admin: Any, accounts: AccountManager) -> None:
        self.saml = saml
        self.accounts = accounts
        configure(saml, provisioning="auto")

    def never_signed_in(self, error: OidcError, reason: str = "saml_invalid") -> None:
        assert error.reason == reason and error.public in ("failed", "denied")
        assert self.accounts.find("maria") is None

    def test_a_response_nobody_signed(self, idp: FakeIdp) -> None:
        exc = refused(self.saml, idp, sign_what="nothing")

        assert "Neither the response nor the assertion is signed" in exc.message
        self.never_signed_in(exc)

    def test_a_response_signed_by_a_key_that_is_not_the_providers(self, idp: FakeIdp) -> None:
        stranger, _pem = make_identity("rsa", "stranger.example")

        exc = refused(self.saml, idp, key=stranger)

        assert "No configured signing certificate verifies" in exc.message
        self.never_signed_in(exc)

    def test_a_signed_assertion_changed_afterwards(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)
        document = base64.b64decode(response).decode().replace("uuid-maria", "uuid-root")

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, base64.b64encode(document.encode()).decode(), relay)

        assert "does not match its digest" in caught.value.message
        self.never_signed_in(caught.value)

    def test_a_second_unsigned_assertion_beside_the_signed_one(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)
        evil = Scenario(name_id="uuid-root", assertion_id="ID_evil").assertion_xml(declare=False)
        document = base64.b64decode(response).decode()
        document = document.replace("<saml:Assertion", evil + "<saml:Assertion", 1)

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, base64.b64encode(document.encode()).decode(), relay)

        assert "exactly one assertion" in caught.value.message
        self.never_signed_in(caught.value)

    def test_an_unsigned_assertion_hidden_where_the_signed_one_is_wrapped(
        self, idp: FakeIdp
    ) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)
        evil = Scenario(name_id="uuid-root", assertion_id="ID_evil").assertion_xml(declare=False)
        document = base64.b64decode(response).decode()
        # The signed assertion moved inside an Extensions element, the evil one put in its place.
        document = document.replace(
            "<saml:Assertion", "<samlp:Extensions><saml:Assertion", 1
        ).replace("</saml:Assertion>", "</saml:Assertion></samlp:Extensions>" + evil, 1)

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, base64.b64encode(document.encode()).decode(), relay)

        assert caught.value.reason == "saml_invalid"
        self.never_signed_in(caught.value)

    def test_two_elements_sharing_the_signed_ones_id(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)
        evil = Scenario(name_id="uuid-root", assertion_id="ID_assertion_1").assertion_xml(
            declare=False
        )
        assert 'ID="ID_assertion_1"' in base64.b64decode(response).decode()
        document = base64.b64decode(response).decode()
        document = document.replace("<saml:Assertion", evil + "<saml:Assertion", 1)

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, base64.b64encode(document.encode()).decode(), relay)

        assert "ID" in caught.value.message
        self.never_signed_in(caught.value)

    def test_an_encrypted_assertion(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        request, relay = read_redirect(begun.url)
        scenario = idp.scenario(request)
        encrypted = (
            "<saml:EncryptedAssertion><xenc:EncryptedData "
            'xmlns:xenc="http://www.w3.org/2001/04/xmlenc#"/></saml:EncryptedAssertion>'
        )
        from tests.saml_support import encode_response

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, encode_response(scenario.response_xml(encrypted)), relay)

        assert "encrypted" in caught.value.message
        self.never_signed_in(caught.value)

    def test_a_response_with_a_dtd(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)
        document = '<!DOCTYPE r [<!ENTITY x "y">]>' + base64.b64decode(response).decode()

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, base64.b64encode(document.encode()).decode(), relay)

        assert "DTD" in caught.value.message
        self.never_signed_in(caught.value)

    def test_a_response_that_is_not_base64(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, "!!not base64!!", "whatever")

        assert caught.value.reason in ("unknown_state", "saml_invalid")
        with pytest.raises(SamlRefused):
            decode_response("!!not base64!!")

    def test_an_answer_to_a_request_that_was_never_made(self, idp: FakeIdp) -> None:
        exc = refused(self.saml, idp, in_response_to="_somebody_elses")

        assert "answers another request" in exc.message
        self.never_signed_in(exc)

    def test_an_answer_for_another_server(self, idp: FakeIdp) -> None:
        exc = refused(self.saml, idp, audience="https://other.example/metadata")

        assert "another audience" in exc.message
        self.never_signed_in(exc)

    def test_an_answer_addressed_to_another_consumer(self, idp: FakeIdp) -> None:
        exc = refused(self.saml, idp, destination="https://evil.example/api/auth/saml/acs")

        assert "addressed to another consumer" in exc.message
        self.never_signed_in(exc)

    def test_a_confirmation_for_another_recipient(self, idp: FakeIdp) -> None:
        exc = refused(self.saml, idp, recipient="https://evil.example/api/auth/saml/acs")

        assert "No bearer confirmation" in exc.message
        self.never_signed_in(exc)

    def test_another_issuer_than_the_provider(self, idp: FakeIdp) -> None:
        exc = refused(self.saml, idp, issuer="https://other-idp.example/metadata")

        assert "another issuer" in exc.message
        self.never_signed_in(exc)

    def test_an_expired_assertion(self, idp: FakeIdp) -> None:
        exc = refused(self.saml, idp, not_on_or_after=-300, confirmation_until=-300)

        assert "expired" in exc.message
        self.never_signed_in(exc)

    def test_an_assertion_that_is_not_valid_yet(self, idp: FakeIdp) -> None:
        exc = refused(self.saml, idp, not_before=300)

        assert "not valid yet" in exc.message
        self.never_signed_in(exc)

    def test_a_clock_a_little_off_is_tolerated_and_a_lot_off_is_not(self, idp: FakeIdp) -> None:
        run(self.saml, idp, not_before=30)
        self.accounts.disable("maria", "again")

        configure(self.saml, clock_skew_seconds=0)
        exc = refused(self.saml, idp, not_before=30)

        assert "not valid yet" in exc.message

    def test_an_assertion_without_conditions_or_an_audience(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        request, relay = read_redirect(begun.url)
        scenario = idp.scenario(request)
        from tests.saml_support import encode_response, sign

        assertion = scenario.assertion_xml()
        start = assertion.index("<saml:Conditions")
        end = assertion.index("</saml:Conditions>") + len("</saml:Conditions>")
        bare = assertion[:start] + assertion[end:]
        signed = sign(bare, scenario.assertion_id, idp.key)
        document = scenario.response_xml(signed.replace(f' xmlns:saml="{NS_SAML}"', "", 1))

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, encode_response(document), relay)

        assert "no conditions" in caught.value.message
        self.never_signed_in(caught.value)

    def test_a_condition_this_server_does_not_know(self, idp: FakeIdp) -> None:
        exc = refused(self.saml, idp, extra_conditions="<saml:Mystery/>")

        assert "condition this server does not know" in exc.message
        self.never_signed_in(exc)

    def test_a_one_time_use_condition_is_known(self, idp: FakeIdp) -> None:
        answer = run(self.saml, idp, extra_conditions="<saml:OneTimeUse/>")

        assert answer.account.username == "maria"

    def test_a_status_that_is_not_success(self, idp: FakeIdp) -> None:
        exc = refused(
            self.saml,
            idp,
            status="urn:oasis:names:tc:SAML:2.0:status:AuthnFailed",
            sign_what="response",
        )

        assert exc.reason == "idp_status" and exc.public == "denied"
        assert "AuthnFailed" in exc.message
        assert self.accounts.find("maria") is None

    def test_an_assertion_without_a_name(self, idp: FakeIdp) -> None:
        exc = refused(self.saml, idp, name_id="")

        assert exc.reason == "saml_invalid"
        self.never_signed_in(exc)


class TestTheSignInItself:
    @pytest.fixture(autouse=True)
    def ready(self, saml: SamlManager, admin: Any, accounts: AccountManager) -> None:
        self.saml = saml
        self.accounts = accounts
        configure(saml, provisioning="auto")

    def test_an_answer_cannot_be_used_twice(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)
        finish(self.saml, begun, response, relay)

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, response, relay)

        assert caught.value.reason == "unknown_state" and caught.value.public == "expired"

    def test_an_answer_that_came_back_to_another_browser(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, response, relay, binding="someone-elses-cookie")
        with pytest.raises(OidcError) as missing:
            finish(self.saml, begun, response, relay, binding=None)

        assert caught.value.reason == "binding" and missing.value.reason == "binding"
        assert self.accounts.find("maria") is None

    def test_a_wrong_browser_does_not_spend_the_sign_in(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)
        with pytest.raises(OidcError):
            finish(self.saml, begun, response, relay, binding="someone-elses-cookie")

        assert finish(self.saml, begun, response, relay).account.username == "maria"

    def test_an_answer_that_waited_too_long(self, idp: FakeIdp, clock: Clock) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)
        clock.advance(3600)

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, response, relay)

        assert caught.value.reason == "unknown_state"

    def test_a_relay_state_nobody_issued(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        response, _relay = idp.answer(begun.url)

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, response, "forged")

        assert caught.value.reason == "unknown_state"

    def test_a_provider_switched_off_mid_sign_in(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)
        self.saml.configure("corp", {"enabled": False})

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, response, relay)

        assert caught.value.reason == "provider_disabled" and caught.value.public == "unavailable"

    def test_the_same_assertion_is_remembered_until_it_could_no_longer_be_valid(
        self, clock: Clock
    ) -> None:
        data = self.saml.data

        assert data.remember_assertion("abc", clock.now + 100) is True
        assert data.remember_assertion("abc", clock.now + 100) is False
        clock.advance(200)
        assert data.remember_assertion("abc", clock.now + 100) is True

    def test_the_assertion_is_kept_only_as_a_digest(self, idp: FakeIdp, store: Any) -> None:
        run(self.saml, idp)

        rows = (
            store._get_connection().execute("SELECT assertion_hash FROM saml_assertions").fetchall()
        )

        assert len(rows) == 1
        assert len(rows[0][0]) == 64 and "ID_assertion_1" not in rows[0][0]

    def test_a_presented_assertion_cannot_be_replayed_into_another_sign_in(
        self, idp: FakeIdp
    ) -> None:
        first = begin(self.saml)
        response, relay = idp.answer(first.url)
        finish(self.saml, first, response, relay)
        second = begin(self.saml)

        # The same bytes, with the new sign-in's relay state: the request id inside is the old one.
        with pytest.raises(OidcError) as caught:
            self.saml.finish(
                response=response,
                relay_state=read_redirect(second.url)[1],
                binding=second.binding,
                client_ip=IP,
            )

        assert "answers another request" in caught.value.message

    def test_removing_the_provider_cancels_the_sign_ins_in_flight(self, idp: FakeIdp) -> None:
        begun = begin(self.saml)
        response, relay = idp.answer(begun.url)
        self.saml.remove("corp")

        with pytest.raises(OidcError) as caught:
            finish(self.saml, begun, response, relay)

        assert caught.value.reason == "unknown_state"
