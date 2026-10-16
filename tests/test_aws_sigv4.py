# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AWS Signature Version 4, against the worked example Amazon publishes for it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from noust.core import aws_sigv4

#: The documentation's own example credentials (they open nothing).
ACCESS_KEY = "AKIDEXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
MOMENT = datetime(2015, 8, 30, 12, 36, 0, tzinfo=timezone.utc)
URL = "https://iam.amazonaws.com/?Action=ListUsers&Version=2010-05-08"
HEADERS = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}


def sign(**overrides):
    arguments = {
        "method": "GET",
        "url": URL,
        "headers": HEADERS,
        "body": b"",
        "access_key": ACCESS_KEY,
        "secret_key": SECRET_KEY,
        "region": "us-east-1",
        "service": "iam",
        "moment": MOMENT,
    }
    arguments.update(overrides)
    return aws_sigv4.sign(**arguments)


class TestTheWorkedExample:
    def test_the_signature_is_the_one_amazon_documents(self) -> None:
        added = sign()

        assert added["X-Amz-Date"] == "20150830T123600Z"
        assert added["Authorization"] == (
            "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/iam/aws4_request, "
            "SignedHeaders=content-type;host;x-amz-date, "
            "Signature=5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7"
        )

    def test_the_derived_key_is_the_documented_one(self) -> None:
        key = aws_sigv4.signing_key(SECRET_KEY, "20150830", "us-east-1", "iam")

        assert key.hex() == "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"


class TestWhatChangesTheSignature:
    def test_the_body_is_signed(self) -> None:
        assert sign(body=b"{}")["Authorization"] != sign()["Authorization"]

    def test_the_moment_is_signed(self) -> None:
        later = sign(moment=MOMENT + timedelta(seconds=1))

        assert later["Authorization"] != sign()["Authorization"]

    def test_the_region_and_the_service_are_signed(self) -> None:
        base = sign()["Authorization"]

        assert sign(region="eu-west-1")["Authorization"] != base
        assert sign(service="ses")["Authorization"] != base

    def test_the_order_of_the_query_does_not_matter(self) -> None:
        reordered = sign(url="https://iam.amazonaws.com/?Version=2010-05-08&Action=ListUsers")

        assert reordered["Authorization"] == sign()["Authorization"]

    def test_header_names_and_spacing_are_normalised(self) -> None:
        loose = sign(headers={"content-type": "application/x-www-form-urlencoded;   charset=utf-8"})

        assert loose["Authorization"] == sign()["Authorization"]

    def test_a_moment_in_another_zone_is_the_same_instant(self) -> None:
        zone = timezone(timedelta(hours=2))

        assert sign(moment=MOMENT.astimezone(zone)) == sign()


class TestASessionToken:
    def test_it_is_signed_and_returned_as_a_header(self) -> None:
        added = sign(session_token="token-1")

        assert added["X-Amz-Security-Token"] == "token-1"
        assert "x-amz-security-token" in added["Authorization"]

    def test_without_one_no_header_is_added(self) -> None:
        assert "X-Amz-Security-Token" not in sign()


class TestThePath:
    def test_a_path_is_encoded_twice_for_the_canonical_request(self) -> None:
        # %21 on the wire is "!" once decoded; the canonical form encodes it again.
        assert aws_sigv4._canonical_path("/a%21b/c") == "/a%2521b/c"

    def test_an_empty_path_is_the_root(self) -> None:
        assert aws_sigv4._canonical_path("") == "/"

    def test_the_query_pairs_are_sorted_and_encoded(self) -> None:
        assert aws_sigv4._canonical_query("b=2&a=x y&a=1") == "a=1&a=x%20y&b=2"
