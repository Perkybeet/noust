# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The LDAP client (checklist I-22): its bytes, its limits, and its conversations with a directory.

The byte-level vectors are worked out by hand from X.690 and RFC 4511, not produced by the code
under test, so that a mistake the encoder and the decoder share would still fail here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.accounts import ldap_client as ldap
from noust.core.accounts.ldap_client import LdapConnection, LdapError
from tests.ldap_support import FakeDirectory, entry, make_certificate

LOCAL = ("127.0.0.1",)


def local_guard(_host: str, _addresses: tuple[str, ...]) -> None:
    """A directory on this machine is what the tests talk to."""


def connection(directory: FakeDirectory, tls: str = "none", **kwargs: object) -> LdapConnection:
    return LdapConnection(
        "localhost",
        directory.port,
        tls,
        guard=local_guard,
        resolver=lambda _name: LOCAL,
        timeout=kwargs.pop("timeout", 3.0),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


PEOPLE = [
    entry(
        "uid=maria,ou=people,dc=acme,dc=test",
        uid="maria",
        mail="maria@acme.test",
        displayName="Maria Lopez",
        userPassword="correct horse",
        entryUUID="7f3a-uuid",
        memberOf=["cn=ops,ou=groups,dc=acme,dc=test", "cn=devs,ou=groups,dc=acme,dc=test"],
        objectClass=["inetOrgPerson", "person"],
    ),
    entry(
        "uid=olga,ou=people,dc=acme,dc=test",
        uid="olga",
        mail="olga@acme.test",
        userPassword="other secret",
        objectClass=["inetOrgPerson", "person"],
    ),
    entry(
        "cn=svc,ou=system,dc=acme,dc=test",
        cn="svc",
        userPassword="service password",
        objectClass="applicationProcess",
    ),
]


def hexed(text: str) -> str:
    """The hexadecimal of some text, for composing a vector from the parts it is made of."""
    return text.encode().hex()


class TestTheBytes:
    def test_a_simple_bind_is_the_message_the_standard_describes(self) -> None:
        # SEQUENCE { INTEGER 1, [APPLICATION 0] { INTEGER 3, "cn=admin", [0] "secret" } }
        assert ldap.encode_bind_request(1, "cn=admin", "secret").hex() == (
            "301a02010160150201030408" + hexed("cn=admin") + "8006" + hexed("secret")
        )

    def test_an_unbind(self) -> None:
        assert ldap.encode_unbind_request(2).hex() == "30050201024200"

    def test_starttls(self) -> None:
        assert ldap.encode_starttls_request(1).hex() == (
            "301d02010177188016" + hexed("1.3.6.1.4.1.1466.20037")
        )

    def test_a_search(self) -> None:
        message = ldap.encode_search_request(
            2, "dc=a", ldap.SCOPE_SUBTREE, "(uid=m)", ["mail"], size_limit=2, time_limit=10
        )
        assert message.hex() == (
            "302c"
            "020102"
            "6327"
            "0404" + hexed("dc=a") + "0a0102"
            "0a0100"
            "020102"
            "02010a"
            "010100"
            "a308"
            "0403" + hexed("uid") + "0401" + hexed("m") + "3006"
            "0404" + hexed("mail")
        )

    @pytest.mark.parametrize(
        ("size", "octets"),
        [
            (0, "00"),
            (127, "7f"),
            (128, "8180"),
            (255, "81ff"),
            (256, "820100"),
            (65535, "82ffff"),
            (65536, "83010000"),
        ],
    )
    def test_lengths(self, size: int, octets: str) -> None:
        assert ldap.encode_length(size).hex() == octets

    @pytest.mark.parametrize(
        ("value", "encoded"),
        [
            (0, "020100"),
            (127, "02017f"),
            (128, "02020080"),
            (256, "02020100"),
            (-1, "0201ff"),
            (-128, "020180"),
            (-129, "0202ff7f"),
            (3, "020103"),
        ],
    )
    def test_integers_are_minimal_twos_complement(self, value: int, encoded: str) -> None:
        assert ldap.encode_integer(value).hex() == encoded
        assert ldap.decode_integer(bytes.fromhex(encoded)[2:]) == value

    @pytest.mark.parametrize(
        ("text", "encoded"),
        [
            ("(uid=maria)", "a30c0403" + hexed("uid") + "0405" + hexed("maria")),
            ("(mail=*)", "8704" + hexed("mail")),
            (
                "(&(objectClass=person)(uid=maria))",
                "a025"
                "a315"
                "040b" + hexed("objectClass") + "0406" + hexed("person") + "a30c"
                "0403" + hexed("uid") + "0405" + hexed("maria"),
            ),
            ("(!(uid=maria))", "a20ea30c0403" + hexed("uid") + "0405" + hexed("maria")),
            ("(cn=ma*)", "a40a0402636e30048002" + hexed("ma")),
            ("(cn=*ria)", "a40b0402636e30058203" + hexed("ria")),
            (
                "(cn=m*r*a)",
                "a40f0402636e30098001" + hexed("m") + "8101" + hexed("r") + "8201" + hexed("a"),
            ),
            ("(cn=a\\2ab)", "a3090402636e0403612a62"),
            ("(|(a=1)(b=2))", "a110a306040161040131a306040162040132"),
            ("(a>=5)", "a506040161040135"),
        ],
    )
    def test_filters(self, text: str, encoded: str) -> None:
        assert ldap.encode_filter(text).hex() == encoded

    @pytest.mark.parametrize(
        "text",
        [
            "uid=maria",  # no parentheses
            "(uid=maria",  # unbalanced
            "(uid=maria))",  # one too many
            "(&)",  # an empty and
            "(!(a=1)(b=2))",  # a not of two
            "(cn:dn:=x)",  # an extensible match
            "(=value)",  # no attribute
            "(bad name=x)",  # a name with a space
            "(cn=a\\zzb)",  # an escape that is not hex
            "(cn=**)",  # nothing to match
            "(&" * 40 + "(a=1)" + ")" * 40,  # nested beyond the ceiling
        ],
    )
    def test_a_filter_that_is_not_one_is_refused(self, text: str) -> None:
        with pytest.raises(LdapError):
            ldap.encode_filter(text)


class TestEscaping:
    @pytest.mark.parametrize(
        ("typed", "escaped"),
        [
            ("maria", "maria"),
            ("*", "\\2a"),
            ("a)(uid=*", "a\\29\\28uid=\\2a"),
            ("x\\y", "x\\5cy"),
            ("nul\x00", "nul\\00"),
            ("María Lópéz", "María Lópéz"),
        ],
    )
    def test_filter_values(self, typed: str, escaped: str) -> None:
        assert ldap.escape_filter_value(typed) == escaped

    def test_what_a_person_types_stays_one_value(self) -> None:
        for attack in ("*", "*)(uid=*", "maria)(|(uid=*", "\\", "a*b"):
            filter_text = f"(&(objectClass=person)(uid={ldap.escape_filter_value(attack)}))"
            encoded = ldap.encode_filter(filter_text)
            # one and with two equality conditions, whatever was typed
            reader = ldap.Reader(encoded)
            tag, body = reader.read()
            inner = reader.sub(body)
            kinds = []
            while not inner.done:
                kinds.append(inner.read()[0])
            assert tag == 0xA0 and kinds == [0xA3, 0xA3]

    @pytest.mark.parametrize(
        ("typed", "escaped"),
        [
            ("maria", "maria"),
            ("a,b", "a\\,b"),
            ("a+b=c", "a\\+b\\=c"),
            ("#x", "\\#x"),
            (" x", "\\ x"),
            ("x ", "x\\ "),
            ('q"<>;\\', 'q\\"\\<\\>\\;\\\\'),
            ("n\x00", "n\\00"),
        ],
    )
    def test_dn_values(self, typed: str, escaped: str) -> None:
        assert ldap.escape_dn_value(typed) == escaped

    @pytest.mark.parametrize(
        ("dn", "value"),
        [
            ("cn=ops,ou=groups,dc=acme", "ops"),
            ("CN=Domain Admins,CN=Users,DC=acme,DC=test", "Domain Admins"),
            ("cn=a\\,b,ou=x", "a,b"),
            ("", ""),
            ("nothing", ""),
        ],
    )
    def test_the_first_component_of_a_name(self, dn: str, value: str) -> None:
        assert ldap.rdn_value(dn) == value


class TestAHostileDirectory:
    def test_a_message_longer_than_is_accepted_is_not_read(self) -> None:
        with pytest.raises(LdapError, match="longer"):
            ldap.Reader(b"\x04\x84\x40\x00\x00\x00").read()

    @pytest.mark.parametrize(
        "data",
        [b"", b"\x04", b"\x04\x05abc", b"\x04\x80", b"\x04\x85\x00\x00\x00\x00\x01", b"\x1f\x01x"],
    )
    def test_truncated_indefinite_and_odd_elements_are_refused(self, data: bytes) -> None:
        with pytest.raises(LdapError):
            ldap.Reader(data).read()

    def test_nesting_has_a_ceiling(self) -> None:
        data = b"\x04\x00"
        for _ in range(ldap.MAX_DEPTH + 2):
            data = ldap.tlv(0x30, data)
        reader = ldap.Reader(data)
        with pytest.raises(LdapError, match="nested"):
            for _ in range(ldap.MAX_DEPTH + 2):
                tag, value = reader.read()
                reader = reader.sub(value)

    def test_a_server_that_sends_a_huge_length_is_cut_off(self) -> None:
        with FakeDirectory(PEOPLE, misbehave="huge") as directory, connection(directory) as client:
            with pytest.raises(LdapError, match="longer than is accepted"):
                client.bind("", "")

    def test_a_server_that_does_not_speak_ldap_is_refused(self) -> None:
        with (
            FakeDirectory(PEOPLE, misbehave="garbage") as directory,
            connection(directory) as client,
        ):
            with pytest.raises(LdapError, match="not an LDAP message"):
                client.bind("", "")

    def test_a_server_that_does_not_answer_in_time_is_given_up_on(self) -> None:
        with (
            FakeDirectory(PEOPLE, delay=2.0) as directory,
            connection(directory, timeout=0.4) as client,
        ):
            with pytest.raises(LdapError, match="did not answer"):
                client.bind("", "")


class TestTalkingToADirectory:
    def test_a_bind_and_a_search(self) -> None:
        with FakeDirectory(PEOPLE) as directory, connection(directory) as client:
            client.bind("cn=svc,ou=system,dc=acme,dc=test", "service password")
            found = client.search(
                "ou=people,dc=acme,dc=test",
                "(&(objectClass=person)(uid=maria))",
                ["uid", "mail", "memberOf", "entryUUID"],
            )

        [maria] = found
        assert maria.dn == "uid=maria,ou=people,dc=acme,dc=test"
        assert maria.first("MAIL") == "maria@acme.test"
        assert maria.values("memberof") == [
            "cn=ops,ou=groups,dc=acme,dc=test",
            "cn=devs,ou=groups,dc=acme,dc=test",
        ]
        assert maria.identifier("entryUUID") == "7f3a-uuid"
        assert maria.first("missing") is None

    def test_a_wrong_password_is_the_directorys_49(self) -> None:
        with FakeDirectory(PEOPLE) as directory, connection(directory) as client:
            with pytest.raises(LdapError) as caught:
                client.bind("uid=maria,ou=people,dc=acme,dc=test", "not the password")

        assert caught.value.code == ldap.RESULT_INVALID_CREDENTIALS
        assert caught.value.diagnostic == "invalid credentials"

    def test_an_empty_password_never_leaves_the_machine(self) -> None:
        with FakeDirectory(PEOPLE) as directory, connection(directory) as client:
            with pytest.raises(LdapError, match="empty password"):
                client.bind("uid=maria,ou=people,dc=acme,dc=test", "")
            seen = list(directory.seen.binds)

        assert seen == []

    def test_an_anonymous_bind_is_asked_for_by_an_empty_name_only(self) -> None:
        with FakeDirectory(PEOPLE) as directory, connection(directory) as client:
            client.bind("", "")
            assert directory.seen.binds == [("", "")]

    def test_a_nul_character_is_not_sent(self) -> None:
        with FakeDirectory(PEOPLE) as directory, connection(directory) as client:
            with pytest.raises(LdapError, match="NUL"):
                client.bind("uid=maria\x00", "x")
            with pytest.raises(LdapError, match="NUL"):
                client.bind("uid=maria,ou=people,dc=acme,dc=test", "a\x00b")

    def test_a_search_returns_at_most_what_was_asked(self) -> None:
        with FakeDirectory(PEOPLE) as directory, connection(directory) as client:
            found = client.search("dc=acme,dc=test", "(objectClass=person)", ["uid"], size_limit=1)

        assert len(found) == 1

    def test_ambiguity_is_visible_by_asking_for_two(self) -> None:
        with FakeDirectory(PEOPLE) as directory, connection(directory) as client:
            found = client.search("dc=acme,dc=test", "(objectClass=person)", ["uid"], size_limit=2)

        assert len(found) == 2

    def test_what_a_person_typed_reaches_the_directory_as_one_value(self) -> None:
        typed = "*)(uid=*"
        filter_text = f"(&(objectClass=person)(uid={ldap.escape_filter_value(typed)}))"
        with FakeDirectory(PEOPLE) as directory, connection(directory) as client:
            found = client.search("dc=acme,dc=test", filter_text, ["uid"])
            sent = directory.seen.searches[-1][1]

        assert found == []
        assert sent.count("a3") == 2 and "a1" not in sent

    def test_a_binary_identifier_is_hexadecimal(self) -> None:
        guid = bytes(range(1, 17))
        with FakeDirectory([entry("cn=x,dc=a", cn="x", objectGUID=guid)]) as directory:
            with connection(directory) as client:
                [found] = client.search("dc=a", "(cn=x)", ["objectGUID"])

        assert found.identifier("objectGUID") == guid.hex()

    def test_substring_and_presence_filters_work_against_a_directory(self) -> None:
        with FakeDirectory(PEOPLE) as directory, connection(directory) as client:
            assert (
                len(client.search("dc=acme,dc=test", "(mail=*@acme.test)", ["uid"], size_limit=9))
                == 2
            )
            assert (
                len(
                    client.search(
                        "dc=acme,dc=test", "(&(mail=*)(!(uid=olga)))", ["uid"], size_limit=9
                    )
                )
                == 1
            )


class TestTransportSecurity:
    @pytest.fixture
    def certificate(self, tmp_path: Path) -> tuple[Path, Path]:
        return make_certificate(tmp_path)

    def test_ldaps_with_a_trusted_certificate(self, certificate: tuple[Path, Path]) -> None:
        with FakeDirectory(PEOPLE, mode="ldaps", certificate=certificate) as directory:
            with connection(directory, "ldaps", ca_file=str(certificate[0])) as client:
                client.bind("uid=maria,ou=people,dc=acme,dc=test", "correct horse")

    def test_starttls_with_a_trusted_certificate(self, certificate: tuple[Path, Path]) -> None:
        with FakeDirectory(PEOPLE, mode="starttls", certificate=certificate) as directory:
            with connection(directory, "starttls", ca_file=str(certificate[0])) as client:
                client.bind("uid=maria,ou=people,dc=acme,dc=test", "correct horse")
            assert directory.seen.starttls == 1

    def test_a_certificate_nobody_trusts_is_refused_before_any_password(
        self, certificate: tuple[Path, Path]
    ) -> None:
        with FakeDirectory(PEOPLE, mode="ldaps", certificate=certificate) as directory:
            with pytest.raises(LdapError, match="certificate"):
                connection(directory, "ldaps").connect()
            assert directory.seen.binds == []

    def test_a_certificate_for_another_name_is_refused(
        self, certificate: tuple[Path, Path]
    ) -> None:
        with FakeDirectory(PEOPLE, mode="ldaps", certificate=certificate) as directory:
            client = LdapConnection(
                "directory.elsewhere.test",
                directory.port,
                "ldaps",
                ca_file=str(certificate[0]),
                guard=local_guard,
                resolver=lambda _name: LOCAL,
                timeout=3.0,
            )
            with pytest.raises(LdapError, match="certificate"):
                client.connect()

    def test_a_server_that_refuses_starttls_gets_no_password(
        self, certificate: tuple[Path, Path]
    ) -> None:
        with FakeDirectory(
            PEOPLE, mode="starttls", certificate=certificate, refuse_starttls=True
        ) as directory:
            with pytest.raises(LdapError, match="refused StartTLS"):
                connection(directory, "starttls", ca_file=str(certificate[0])).connect()
            assert directory.seen.binds == []

    def test_plain_text_to_a_directory_over_the_network_is_not_possible(self) -> None:
        with pytest.raises(LdapError, match="must use TLS"):
            LdapConnection("ldap.acme.test", 389, "none")

    def test_an_unknown_mode_is_refused(self) -> None:
        with pytest.raises(LdapError, match="Unknown TLS mode"):
            LdapConnection("localhost", 389, "tls1")


class TestTheHostIsJudgedOnceAndUsedAsJudged:
    def test_a_directory_on_a_private_network_is_refused_unless_it_was_allowed(self) -> None:
        with FakeDirectory(PEOPLE) as directory:
            client = LdapConnection(
                "ldap.acme.test",
                directory.port,
                "ldaps",
                resolver=lambda _name: ("10.0.0.7",),
            )
            with pytest.raises(LdapError, match="private or internal") as caught:
                client.connect()

        assert "allow_private_hosts" in caught.value.details

    def test_the_metadata_address_is_never_reached(self) -> None:
        client = LdapConnection(
            "ldap.acme.test", 636, "ldaps", resolver=lambda _name: ("169.254.169.254",)
        )

        with pytest.raises(LdapError, match="private or internal"):
            client.connect()

    def test_a_name_with_one_internal_address_among_public_ones_is_refused(self) -> None:
        client = LdapConnection(
            "ldap.acme.test", 636, "ldaps", resolver=lambda _name: ("203.0.113.9", "192.168.1.5")
        )

        with pytest.raises(LdapError, match="private or internal"):
            client.connect()

    def test_a_name_that_does_not_resolve_is_said_so(self) -> None:
        def broken(_name: str) -> tuple[str, ...]:
            raise OSError("no such host")

        with pytest.raises(LdapError, match="Could not resolve"):
            LdapConnection("nowhere.test", 636, "ldaps", resolver=broken).connect()

    def test_the_connection_goes_to_the_address_that_was_judged(self) -> None:
        judged: list[tuple[str, ...]] = []

        def guard(_host: str, addresses: tuple[str, ...]) -> None:
            judged.append(addresses)

        with FakeDirectory(PEOPLE) as directory:
            client = LdapConnection(
                "localhost",
                directory.port,
                "none",
                guard=guard,
                resolver=lambda _name: LOCAL,
                timeout=3.0,
            )
            with client:
                client.bind("", "")

        assert judged == [LOCAL]
