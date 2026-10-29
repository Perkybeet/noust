# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The question the delegation check asks and how it reads the answer (4.1, item 104).

The check trusts nothing a server sends: an answer that is not for the question asked, a name that
points forward or loops, a record that runs past the packet and a label of a kind nobody uses are
all refused with the same error, and no byte string makes the reader do anything else.
"""

from __future__ import annotations

import struct

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from noust.managers.authdns.dnsquery import DnsError, ask, encode_name
from tests.support.dns_packets import (
    FakeNetwork,
    Reply,
    a_record,
    build,
    ds_record,
    ns_record,
    soa_record,
)

SERVER = "192.0.2.1"
DIGEST = "ebe5d6007fdf87e3b70fbc2e1afb0cff87e66a0d8f70cae380d4352472e83665"


def network(reply: Reply | Exception, kind: str = "SOA") -> FakeNetwork:
    return FakeNetwork({(SERVER, "example.com", kind): reply})


class TestTheQuestion:
    def test_it_is_one_question_with_recursion_only_when_asked(self) -> None:
        net = network(Reply(authoritative=True, answers=[soa_record(7)]))

        ask(SERVER, "Example.COM.", "SOA", transport=net)
        ask(SERVER, "example.com", "SOA", recursion=True, transport=net)

        assert net.asked == [
            (SERVER, "example.com", "SOA", False),
            (SERVER, "example.com", "SOA", True),
        ]

    def test_asking_for_dnssec_adds_the_edns_record_with_the_do_bit(self) -> None:
        seen: list[bytes] = []

        def transport(server: str, port: int, packet: bytes, timeout: float) -> bytes:
            seen.append(packet)
            return build(packet, Reply())

        ask(SERVER, "example.com", "DS", dnssec=True, transport=transport)
        ask(SERVER, "example.com", "DS", transport=transport)

        with_opt, without = seen
        assert (
            struct.unpack(">H", with_opt[10:12])[0] == 1
            and struct.unpack(">H", without[10:12])[0] == 0
        )
        assert with_opt.endswith(b"\x00" + struct.pack(">HHIH", 41, 1232, 0x8000, 0))

    def test_a_name_that_cannot_be_asked_is_an_error_not_a_crash(self) -> None:
        for bad in ("a" * 64 + ".com", "a..com"):
            with pytest.raises(DnsError):
                ask(SERVER, bad, "A", transport=network(Reply()))
        with pytest.raises(DnsError):
            ask(SERVER, "example.com", "AXFR", transport=network(Reply()))


class TestTheAnswer:
    def test_the_flags_and_the_records_are_read(self) -> None:
        net = network(
            Reply(
                answers=[soa_record(1793266851)],
                authoritative=True,
                authority=[ns_record("ns1.example.net")],
            )
        )

        answer = ask(SERVER, "example.com", "SOA", transport=net)

        assert answer.authoritative is True and answer.recursion_available is False
        assert answer.rcode == 0 and answer.truncated is False
        (soa,) = answer.of_type("SOA")
        assert soa.name == "example.com"
        assert soa.data == "ns1.example.net hostmaster.example.com 1793266851"
        assert [r.data for r in answer.authority] == ["ns1.example.net"]

    def test_names_addresses_and_ds_records_are_read_in_the_form_noust_keeps_them(self) -> None:
        net = FakeNetwork(
            {
                (SERVER, "example.com", "NS"): Reply(
                    answers=[ns_record("ns1.example.net"), ns_record("NS2.example.net")]
                ),
                (SERVER, "example.com", "A"): Reply(answers=[a_record("192.0.2.9")]),
                (SERVER, "example.com", "AAAA"): Reply(answers=[a_record("2001:db8::9")]),
                (SERVER, "example.com", "DS"): Reply(answers=[ds_record(9431, 13, 2, DIGEST)]),
            }
        )

        assert [r.data for r in ask(SERVER, "example.com", "NS", transport=net).answers] == [
            "ns1.example.net",
            "ns2.example.net",
        ]
        assert ask(SERVER, "example.com", "A", transport=net).answers[0].data == "192.0.2.9"
        assert ask(SERVER, "example.com", "AAAA", transport=net).answers[0].data == "2001:db8::9"
        assert (
            ask(SERVER, "example.com", "DS", transport=net).answers[0].data == f"9431 13 2 {DIGEST}"
        )

    def test_a_refusal_has_its_code_and_no_ra_flag(self) -> None:
        net = FakeNetwork({(SERVER, "example.com", "A"): Reply(rcode=5)})

        answer = ask(SERVER, "example.com", "A", recursion=True, transport=net)

        assert (answer.rcode, answer.recursion_available, answer.answers) == (5, False, ())


class TestWhatIsNotAnAnswer:
    def question(self) -> bytes:
        captured: list[bytes] = []

        def transport(server: str, port: int, packet: bytes, timeout: float) -> bytes:
            captured.append(packet)
            return build(packet, Reply(answers=[soa_record(1)]))

        ask(SERVER, "example.com", "SOA", transport=transport)
        return captured[0]

    @pytest.mark.parametrize(
        "damage",
        [
            lambda good: b"\x00\x00" + good[2:],  # an answer to another question
            lambda good: good[:2] + b"\x00\x00" + good[4:],  # not flagged as a response
            lambda good: good[:20],  # cut in the question
            lambda good: good[:-3],  # cut in the last record
            lambda good: b"",
            lambda good: good[:12] + b"\xc0\x0c" + good[14:],  # a name that points at itself
            lambda good: good[:12] + b"\x80" + good[13:],  # a label of a kind nobody uses
        ],
    )
    def test_each_is_refused_with_the_same_error(self, damage: object) -> None:
        captured: list[bytes] = []
        ident_holder: list[bytes] = []

        def transport(server: str, port: int, packet: bytes, timeout: float) -> bytes:
            ident_holder.append(packet[:2])
            good = build(packet, Reply(answers=[soa_record(1)]))
            captured.append(good)
            return damage(good)  # type: ignore[operator,no-any-return]

        with pytest.raises(DnsError):
            ask(SERVER, "example.com", "SOA", transport=transport)

    def test_a_server_that_does_not_answer_is_a_server_that_did_not_answer(self) -> None:
        with pytest.raises(DnsError) as raised:
            ask(SERVER, "example.com", "SOA", transport=network(OSError("timed out")))

        assert "did not answer" in str(raised.value)

    def test_a_pointer_that_loops_does_not_hang_the_reader(self) -> None:
        def transport(server: str, port: int, packet: bytes, timeout: float) -> bytes:
            good = bytearray(build(packet, Reply(answers=[ns_record("ns1.example.net")])))
            # The data of the NS record becomes a pointer to the owner, which points at itself.
            return bytes(good[:12] + b"\xc0\x0c" + good[14:])

        with pytest.raises(DnsError):
            ask(SERVER, "example.com", "NS", transport=transport)

    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(noise=st.binary(max_size=200), cut=st.integers(0, 120))
    def test_no_bytes_make_it_do_anything_but_read_or_refuse(self, noise: bytes, cut: int) -> None:
        def transport(server: str, port: int, packet: bytes, timeout: float) -> bytes:
            good = build(packet, Reply(answers=[soa_record(1), ns_record("ns1.example.net")]))
            return good[: len(good) - cut] + noise if cut else packet[:2] + noise

        try:
            ask(SERVER, "example.com", "SOA", transport=transport)
        except DnsError:
            pass


def test_a_name_is_written_label_by_label() -> None:
    assert encode_name("a.example.com.") == b"\x01a\x07example\x03com\x00"
    assert encode_name(".") == b"\x00"
