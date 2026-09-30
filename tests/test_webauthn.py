"""
The WebAuthn verifier: CBOR, authenticator data, COSE keys, and the two ceremonies.

Signatures are real: a software authenticator (``tests/webauthn_authenticator``)
creates ES256, RS256 and EdDSA keys with ``cryptography`` and signs as a
security key does. Each check the verifier makes is shown to refuse what it
exists to refuse - another origin, another RP, no user verification, a reused
challenge, a counter that goes back, one flipped bit - because a verifier that
accepts everything passes every test that only feeds it good input.
"""

from __future__ import annotations

import builtins
import struct
from typing import Any

import pytest

pytest.importorskip("cryptography")

from hypothesis import given, settings
from hypothesis import strategies as st

from noust.core.accounts import webauthn
from noust.core.accounts.webauthn import (
    ALG_EDDSA,
    ALG_ES256,
    ALG_RS256,
    CborError,
    StoredCredential,
    WebAuthnError,
    WebAuthnUnavailable,
    b64url_decode,
    cbor_decode,
    cbor_decode_prefix,
    parse_authenticator_data,
    parse_cose_key,
    verify_assertion,
    verify_registration,
)
from tests.webauthn_authenticator import (
    FLAG_AT,
    FLAG_BS,
    FLAG_ED,
    FLAG_UP,
    FLAG_UV,
    SoftwareAuthenticator,
    auth_data,
    b64url,
    cbor_encode,
)

RP_ID = "noust.example.com"
ORIGIN = "https://noust.example.com"
CHALLENGE = b"c" * 32


def creation_options(challenge: bytes = CHALLENGE, user: bytes = b"u" * 32) -> dict[str, Any]:
    return {
        "rp": {"id": RP_ID, "name": "Noust"},
        "user": {"id": b64url(user)},
        "challenge": b64url(challenge),
    }


def request_options(challenge: bytes = CHALLENGE) -> dict[str, Any]:
    return {"rpId": RP_ID, "challenge": b64url(challenge)}


class Challenges:
    """A challenge source that accepts one value, once."""

    def __init__(self, value: bytes = CHALLENGE) -> None:
        self.value = value
        self.spent = False

    def __call__(self, presented: bytes) -> bool:
        if presented != self.value or self.spent:
            return False
        self.spent = True
        return True


def register(authenticator: SoftwareAuthenticator, **bend: Any) -> webauthn.VerifiedRegistration:
    response = authenticator.create(creation_options(), bend.pop("origin", ORIGIN), **bend)
    return verify_registration(response, rp_id=RP_ID, origins=(ORIGIN,), challenge_ok=Challenges())


def stored(authenticator: SoftwareAuthenticator) -> StoredCredential:
    result = register(authenticator)
    return StoredCredential(
        credential_id=result.credential_id,
        public_key=result.public_key,
        sign_count=result.sign_count,
        backup_eligible=result.backup_eligible,
        user_handle=b"u" * 32,
        rp_id=RP_ID,
    )


def authenticate(
    authenticator: SoftwareAuthenticator,
    credential: StoredCredential,
    *,
    origins: tuple[str, ...] = (ORIGIN,),
    challenges: Challenges | None = None,
    server_rp: str = RP_ID,
    **bend: Any,
) -> webauthn.VerifiedAssertion:
    response = authenticator.get(request_options(), bend.pop("origin", ORIGIN), **bend)
    return verify_assertion(
        response,
        rp_id=server_rp,
        origins=origins,
        challenge_ok=challenges or Challenges(),
        lookup=lambda raw: credential if raw == credential.credential_id else None,
    )


def reason_of(call: Any) -> str:
    with pytest.raises(WebAuthnError) as caught:
        call()
    return caught.value.reason


class TestCbor:
    def test_the_types_webauthn_uses_round_trip(self) -> None:
        value = {1: 2, 3: -7, -1: b"\x00\x01", "fmt": "none", "list": [True, False, None, 1000]}
        assert cbor_decode(cbor_encode(value)) == value

    def test_long_lengths_decode(self) -> None:
        blob = bytes(70000)
        assert cbor_decode(cbor_encode({"b": blob[:60000]}))["b"] == blob[:60000]

    @pytest.mark.parametrize(
        "raw",
        [
            b"\x5f\x41\x00\xff",  # indefinite-length bytes
            b"\x9f\x01\xff",  # indefinite-length array
            b"\xc1\x01",  # a tag
            b"\xf9\x3c\x00",  # a half float
            b"\xfb" + bytes(8),  # a double
            b"\xf7",  # undefined
            b"\xa2\x01\x01\x01\x02",  # a duplicate map key
            b"\xa1\x81\x01\x01",  # an array as a map key
            b"\x42\x00",  # bytes cut short
            b"\x5b" + b"\xff" * 8,  # a length no input can have
            b"\x9b" + b"\xff" * 8,  # an array count no input can have
            b"\x01\x02",  # trailing bytes
            b"\x63\xff\xfe\xfd",  # text that is not UTF-8
            b"",
        ],
    )
    def test_malformed_input_is_refused(self, raw: bytes) -> None:
        with pytest.raises(CborError):
            cbor_decode(raw)

    def test_nesting_is_bounded(self) -> None:
        deep = b"\x81" * 40 + b"\x01"
        with pytest.raises(CborError):
            cbor_decode(deep)

    def test_input_size_is_bounded(self) -> None:
        with pytest.raises(CborError):
            cbor_decode(cbor_encode(bytes(webauthn.MAX_CBOR_BYTES + 1)))

    def test_a_prefix_reports_where_it_ends(self) -> None:
        value, end = cbor_decode_prefix(cbor_encode({1: 2}) + b"rest")
        assert value == {1: 2}
        assert end == 3

    @settings(max_examples=400, deadline=None)
    @given(st.binary(max_size=256))
    def test_arbitrary_bytes_only_ever_raise_the_decoder_error(self, raw: bytes) -> None:
        try:
            cbor_decode(raw)
        except CborError:
            pass


class TestAuthenticatorData:
    def test_the_fields_are_read(self) -> None:
        data = parse_authenticator_data(auth_data(RP_ID, FLAG_UP | FLAG_UV, 7))
        assert data.user_present and data.user_verified
        assert not data.backup_eligible
        assert data.sign_count == 7
        assert data.credential_id is None

    def test_trailing_bytes_are_refused(self) -> None:
        with pytest.raises(WebAuthnError):
            parse_authenticator_data(auth_data(RP_ID, FLAG_UP, 0) + b"\x00")

    def test_extensions_are_parsed_when_flagged(self) -> None:
        raw = auth_data(RP_ID, FLAG_UP | FLAG_ED, 0, extensions={"credProps": {"rk": True}})
        assert parse_authenticator_data(raw).extensions == {"credProps": {"rk": True}}

    def test_backup_state_without_eligibility_is_refused(self) -> None:
        with pytest.raises(WebAuthnError):
            parse_authenticator_data(auth_data(RP_ID, FLAG_UP | FLAG_BS, 0))

    def test_attested_data_that_is_cut_short_is_refused(self) -> None:
        raw = auth_data(RP_ID, FLAG_UP | FLAG_AT, 0) + bytes(16) + struct.pack(">H", 64) + b"x"
        with pytest.raises(WebAuthnError):
            parse_authenticator_data(raw)

    def test_too_short_is_refused(self) -> None:
        with pytest.raises(WebAuthnError):
            parse_authenticator_data(b"\x00" * 36)


class TestCoseKeys:
    def test_an_es256_key_parses(self) -> None:
        key = parse_cose_key(SoftwareAuthenticator(ALG_ES256).cose_key())
        assert key.alg == ALG_ES256

    def test_a_point_off_the_curve_is_refused(self) -> None:
        raw = cbor_encode({1: 2, 3: -7, -1: 1, -2: b"\x01" * 32, -3: b"\x02" * 32})
        with pytest.raises(WebAuthnError) as caught:
            webauthn.load_public_key(parse_cose_key(raw))
        assert caught.value.reason == "key"

    def test_a_weak_rsa_key_is_refused(self) -> None:
        weak = SoftwareAuthenticator(ALG_RS256, rsa_bits=1024)
        with pytest.raises(WebAuthnError) as caught:
            webauthn.load_public_key(parse_cose_key(weak.cose_key()))
        assert caught.value.reason == "key"

    def test_an_algorithm_not_offered_is_refused(self) -> None:
        with pytest.raises(WebAuthnError) as caught:
            parse_cose_key(SoftwareAuthenticator(ALG_RS256).cose_key(), allowed=(ALG_ES256,))
        assert caught.value.reason == "algorithm"

    def test_a_key_type_that_contradicts_the_algorithm_is_refused(self) -> None:
        raw = cbor_encode({1: 3, 3: -7, -1: 1, -2: b"\x01" * 32, -3: b"\x02" * 32})
        with pytest.raises(WebAuthnError):
            parse_cose_key(raw)


class TestRegistration:
    @pytest.mark.parametrize("alg", [ALG_ES256, ALG_RS256])
    def test_a_good_registration_is_accepted(self, alg: int) -> None:
        authenticator = SoftwareAuthenticator(alg)
        result = register(authenticator)
        assert result.credential_id == authenticator.credential_id
        assert result.alg == alg
        assert result.user_verified is True
        assert result.transports == ["internal"]

    def test_eddsa_is_accepted_where_the_library_has_it(self) -> None:
        if ALG_EDDSA not in webauthn.supported_algorithms():
            pytest.skip("this cryptography has no Ed25519")
        assert register(SoftwareAuthenticator(ALG_EDDSA)).alg == ALG_EDDSA

    def test_the_synced_flags_are_kept(self) -> None:
        result = register(SoftwareAuthenticator(backup_eligible=True, backup_state=True))
        assert result.backup_eligible and result.backup_state

    def test_another_origin_is_refused(self) -> None:
        assert (
            reason_of(lambda: register(SoftwareAuthenticator(), origin="https://evil.example"))
            == "origin"
        )

    def test_another_rp_is_refused(self) -> None:
        assert reason_of(lambda: register(SoftwareAuthenticator(), rp_id="evil.example")) == "rp_id"

    def test_a_get_ceremony_is_not_a_registration(self) -> None:
        assert reason_of(lambda: register(SoftwareAuthenticator(), kind="webauthn.get")) == "type"

    def test_another_challenge_is_refused(self) -> None:
        other = b64url(b"x" * 32)
        assert reason_of(lambda: register(SoftwareAuthenticator(), challenge=other)) == "challenge"

    def test_user_verification_is_required(self) -> None:
        assert (
            reason_of(lambda: register(SoftwareAuthenticator(), user_verified=False))
            == "user_verification"
        )

    def test_user_presence_is_required(self) -> None:
        assert (
            reason_of(lambda: register(SoftwareAuthenticator(), user_present=False))
            == "user_presence"
        )

    def test_a_cross_origin_ceremony_is_refused(self) -> None:
        assert reason_of(lambda: register(SoftwareAuthenticator(), cross_origin=True)) == "origin"

    def test_an_algorithm_not_offered_is_refused(self) -> None:
        authenticator = SoftwareAuthenticator(ALG_RS256)
        response = authenticator.create(creation_options(), ORIGIN)
        with pytest.raises(WebAuthnError) as caught:
            verify_registration(
                response,
                rp_id=RP_ID,
                origins=(ORIGIN,),
                challenge_ok=Challenges(),
                algorithms=(ALG_ES256,),
            )
        assert caught.value.reason == "algorithm"

    def test_a_weak_rsa_key_is_refused(self) -> None:
        assert reason_of(lambda: register(SoftwareAuthenticator(ALG_RS256, rsa_bits=1024))) == "key"

    def test_the_id_must_be_the_attested_one(self) -> None:
        authenticator = SoftwareAuthenticator()
        response = authenticator.create(creation_options(), ORIGIN)
        response["id"] = response["rawId"] = b64url(b"another id")
        with pytest.raises(WebAuthnError):
            verify_registration(response, rp_id=RP_ID, origins=(ORIGIN,), challenge_ok=Challenges())

    def test_other_attestation_formats_are_read_as_none(self) -> None:
        assert register(SoftwareAuthenticator(), fmt="packed").fmt == "packed"

    def test_the_challenge_is_spent_even_when_the_rest_fails(self) -> None:
        challenges = Challenges()
        response = SoftwareAuthenticator().create(creation_options(), ORIGIN, user_verified=False)
        with pytest.raises(WebAuthnError):
            verify_registration(response, rp_id=RP_ID, origins=(ORIGIN,), challenge_ok=challenges)
        assert challenges.spent

    @pytest.mark.parametrize(
        "broken",
        [
            {},
            {"type": "public-key"},
            {"id": "!!", "rawId": "!!", "type": "public-key", "response": {}},
            {"id": "AA", "rawId": "AA", "type": "password", "response": {}},
        ],
    )
    def test_malformed_responses_are_refused(self, broken: dict[str, Any]) -> None:
        with pytest.raises(WebAuthnError):
            verify_registration(broken, rp_id=RP_ID, origins=(ORIGIN,), challenge_ok=Challenges())


class TestAssertion:
    @pytest.mark.parametrize("alg", [ALG_ES256, ALG_RS256])
    def test_a_good_assertion_is_accepted(self, alg: int) -> None:
        authenticator = SoftwareAuthenticator(alg)
        credential = stored(authenticator)
        result = authenticate(authenticator, credential)
        assert result.sign_count == 1
        assert result.user_handle == b"u" * 32

    def test_eddsa_assertions_verify_where_the_library_has_it(self) -> None:
        if ALG_EDDSA not in webauthn.supported_algorithms():
            pytest.skip("this cryptography has no Ed25519")
        authenticator = SoftwareAuthenticator(ALG_EDDSA)
        assert authenticate(authenticator, stored(authenticator)).sign_count == 1

    def test_a_flipped_bit_in_the_signature_is_refused(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        assert (
            reason_of(lambda: authenticate(authenticator, credential, tamper_signature=True))
            == "signature"
        )

    def test_changed_client_data_is_refused(self) -> None:
        authenticator = SoftwareAuthenticator(ALG_RS256)
        credential = stored(authenticator)
        assert (
            reason_of(lambda: authenticate(authenticator, credential, tamper_client_data=True))
            == "signature"
        )

    def test_another_key_cannot_sign_for_the_credential(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        impostor = SoftwareAuthenticator(credential_id=authenticator.credential_id)
        impostor.rp_id = RP_ID
        impostor.user_handle = b"u" * 32
        assert reason_of(lambda: authenticate(impostor, credential)) == "signature"

    def test_another_origin_is_refused(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        assert (
            reason_of(
                lambda: authenticate(authenticator, credential, origin="https://evil.example")
            )
            == "origin"
        )

    def test_another_rp_is_refused(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        assert (
            reason_of(lambda: authenticate(authenticator, credential, rp_id="evil.example"))
            == "rp_id"
        )

    def test_a_credential_registered_under_another_rp_is_unknown_here(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        moved = StoredCredential(**{**credential.__dict__, "rp_id": "localhost"})
        assert reason_of(lambda: authenticate(authenticator, moved)) == "unknown_credential"

    def test_a_create_ceremony_is_not_an_assertion(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        assert (
            reason_of(lambda: authenticate(authenticator, credential, kind="webauthn.create"))
            == "type"
        )

    def test_user_verification_is_required(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        assert (
            reason_of(lambda: authenticate(authenticator, credential, user_verified=False))
            == "user_verification"
        )

    def test_user_presence_is_required(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        assert (
            reason_of(lambda: authenticate(authenticator, credential, user_present=False))
            == "user_presence"
        )

    def test_a_reused_challenge_is_refused(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        challenges = Challenges()
        authenticate(authenticator, credential, challenges=challenges)
        assert (
            reason_of(lambda: authenticate(authenticator, credential, challenges=challenges))
            == "challenge"
        )

    def test_an_unknown_credential_is_named_as_such(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        stranger = SoftwareAuthenticator()
        stranger.rp_id = RP_ID
        assert reason_of(lambda: authenticate(stranger, credential)) == "unknown_credential"

    def test_another_user_handle_is_refused(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        assert (
            reason_of(lambda: authenticate(authenticator, credential, user_handle=b"v" * 32))
            == "user_handle"
        )

    def test_a_discoverable_sign_in_needs_the_user_handle(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        assert (
            reason_of(lambda: authenticate(authenticator, credential, user_handle=None))
            == "user_handle"
        )

    def test_a_counter_that_goes_back_is_a_possible_clone(self) -> None:
        authenticator = SoftwareAuthenticator(sign_count=10)
        credential = stored(authenticator)
        with pytest.raises(WebAuthnError) as caught:
            authenticate(authenticator, credential, sign_count=5)
        assert caught.value.reason == "counter"
        assert caught.value.credential_id == authenticator.credential_id

    def test_a_counter_that_stays_the_same_is_refused_once_it_counts(self) -> None:
        authenticator = SoftwareAuthenticator(sign_count=3)
        credential = stored(authenticator)
        assert reason_of(lambda: authenticate(authenticator, credential, sign_count=3)) == "counter"

    def test_a_synced_passkey_may_always_report_zero(self) -> None:
        authenticator = SoftwareAuthenticator(backup_eligible=True, count_step=0)
        credential = stored(authenticator)
        assert authenticate(authenticator, credential).sign_count == 0
        assert authenticate(authenticator, credential).sign_count == 0

    def test_backup_eligibility_cannot_change(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        authenticator.backup_eligible = True
        assert reason_of(lambda: authenticate(authenticator, credential)) == "backup_eligibility"

    def test_attested_data_in_an_assertion_is_refused(self) -> None:
        authenticator = SoftwareAuthenticator()
        credential = stored(authenticator)
        byte = authenticator.flags() | FLAG_AT
        assert reason_of(lambda: authenticate(authenticator, credential, flags=byte)) in {
            "malformed",
            "attested_data",
        }


class TestEncoding:
    def test_padding_and_the_standard_alphabet_are_refused(self) -> None:
        assert b64url_decode("AQID") == b"\x01\x02\x03"
        with pytest.raises(WebAuthnError):
            b64url_decode("AQ+/")
        with pytest.raises(WebAuthnError):
            b64url_decode("A")


class TestLibrary:
    def test_a_missing_library_says_what_to_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_import = builtins.__import__

        def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("cryptography"):
                raise ModuleNotFoundError("No module named 'cryptography'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)
        webauthn.supported_algorithms.cache_clear()
        try:
            with pytest.raises(WebAuthnUnavailable) as caught:
                webauthn.load_crypto()
            assert "python3-cryptography" in caught.value.details
        finally:
            monkeypatch.undo()
            webauthn.supported_algorithms.cache_clear()

    def test_es256_and_rs256_are_always_offered_first(self) -> None:
        algorithms = webauthn.supported_algorithms()
        assert algorithms[0] == ALG_ES256
        assert ALG_RS256 in algorithms
