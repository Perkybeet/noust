# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Noust knows of RDAP without asking anyone anything (item 173).

Which server answers for a top-level domain is IANA's bootstrap list, which ships with Noust as data
(``scripts/generate_rdap_bootstrap.py``). Which of a name's labels the registry recognises is its own
answer, so the registrable domain is found by climbing label by label. Here are the parts that need
no network: the table, the order of the names to ask about, the address of a question and the
expiry in an answer.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core import rdap, rdap_bootstrap_data
from noust.core.outbound import UnsafePath

ROOT = Path(__file__).resolve().parent.parent


class TestTheBundledTable:
    def test_it_says_who_answers_for_the_big_registries(self) -> None:
        assert rdap.service_for("com") == "https://rdap.verisign.com/com/v1/"
        assert rdap.service_for("uk") == "https://rdap.nominet.uk/uk/"

    def test_it_is_case_insensitive(self) -> None:
        assert rdap.service_for("COM") == rdap.service_for("com")

    def test_a_top_level_domain_it_does_not_know_has_no_server(self) -> None:
        assert rdap.service_for("no-such-tld") is None
        assert rdap.service_for("") is None
        assert rdap.service_for("localhost") is None

    def test_every_address_is_https_and_ends_in_a_slash(self) -> None:
        for base, tlds in rdap_bootstrap_data.SERVICES:
            assert base.startswith("https://") and base.endswith("/"), base
            assert tlds.strip() == tlds and tlds, base

    def test_a_top_level_domain_is_in_one_service_only(self) -> None:
        seen: dict[str, str] = {}
        for base, tlds in rdap_bootstrap_data.SERVICES:
            for tld in tlds.split():
                assert tld not in seen, (tld, base, seen.get(tld))
                seen[tld] = base

    def test_it_says_when_it_was_published(self) -> None:
        assert datetime.fromisoformat(rdap_bootstrap_data.PUBLICATION.replace("Z", "+00:00"))


class TestWhichNamesToAskAbout:
    def test_a_name_is_asked_whole_and_then_label_by_label_until_two_remain(self) -> None:
        assert rdap.candidates("shop.example.co.uk") == [
            "shop.example.co.uk",
            "example.co.uk",
            "co.uk",
        ]
        assert rdap.candidates("shop.example.com") == ["shop.example.com", "example.com"]
        assert rdap.candidates("example.com") == ["example.com"]

    def test_a_deep_name_is_not_climbed_without_end(self) -> None:
        names = rdap.candidates("a.b.c.d.e.example.com")

        assert len(names) <= rdap.MAX_ASKS
        assert names[0] == "a.b.c.d.e.example.com"
        assert names[-1] == "example.com" or len(names) == rdap.MAX_ASKS

    def test_a_wildcard_is_asked_about_as_the_name_it_covers(self) -> None:
        assert rdap.served_name("*.Example.COM.") == "example.com"
        assert rdap.candidates("*.shop.example.com") == ["shop.example.com", "example.com"]

    @pytest.mark.parametrize(
        "name",
        [
            "localhost",
            "intranet",
            "127.0.0.1",
            "10.0.0.5",
            "203.0.113.9",
            "::1",
            "2001:db8::1",
            "bücher.example",
            "shop..example.com",
            "-shop.example.com",
            "shop.example.com.",  # a trailing dot is removed, so this one is fine: see below
            "",
            "a" * 64 + ".example.com",
            ("a." * 130) + "com",
        ],
    )
    def test_what_is_not_a_domain_someone_registered_is_not_asked(self, name: str) -> None:
        if name == "shop.example.com.":
            assert rdap.is_checkable(name) is True
            return
        assert rdap.is_checkable(name) is False
        assert rdap.candidates(name) == []

    def test_an_ordinary_name_is_checkable(self) -> None:
        for name in (
            "example.com",
            "shop.example.co.uk",
            "xn--bcher-kva.example",
            "a-b.example.org",
        ):
            assert rdap.is_checkable(name), name


class TestTheQuestion:
    def test_the_address_is_the_registrys_service_and_the_name_as_one_segment(self) -> None:
        assert (
            rdap.rdap_url("https://rdap.verisign.com/com/v1/", "example.com")
            == "https://rdap.verisign.com/com/v1/domain/example.com"
        )

    def test_a_name_that_could_be_a_path_is_refused_not_sent(self) -> None:
        with pytest.raises(UnsafePath):
            rdap.rdap_url("https://rdap.verisign.com/com/v1/", "../example.com")
        with pytest.raises(UnsafePath):
            rdap.rdap_url("https://rdap.verisign.com/com/v1/", "")

    def test_the_registry_is_named_by_its_host(self) -> None:
        assert rdap.registry_name("https://rdap.verisign.com/com/v1/") == "rdap.verisign.com"


class TestTheAnswer:
    def document(self, *events: dict[str, str]) -> dict[str, Any]:
        return {"objectClassName": "domain", "ldhName": "example.com", "events": list(events)}

    def test_the_expiration_event_is_the_expiry(self) -> None:
        document = self.document(
            {"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
            {"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"},
            {"eventAction": "last changed", "eventDate": "2026-08-14T07:01:38Z"},
        )

        assert rdap.expiration_from(document) == datetime(2027, 8, 13, 4, tzinfo=timezone.utc)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2027-08-13T04:00:00.000Z", datetime(2027, 8, 13, 4, tzinfo=timezone.utc)),
            ("2027-08-13T06:00:00+02:00", datetime(2027, 8, 13, 4, tzinfo=timezone.utc)),
            ("2027-08-13", datetime(2027, 8, 13, tzinfo=timezone.utc)),
        ],
    )
    def test_the_date_is_read_in_the_forms_registries_write_it(
        self, text: str, expected: datetime
    ) -> None:
        document = self.document({"eventAction": "expiration", "eventDate": text})

        assert rdap.expiration_from(document) == expected

    def test_the_action_is_read_without_regard_to_case(self) -> None:
        document = self.document({"eventAction": "Expiration", "eventDate": "2027-08-13T04:00:00Z"})

        assert rdap.expiration_from(document) is not None

    def test_a_registry_that_publishes_no_expiry_says_so_by_having_none(self) -> None:
        document = self.document(
            {"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"}
        )

        assert rdap.expiration_from(document) is None
        assert rdap.expiration_from({}) is None

    @pytest.mark.parametrize(
        "document",
        [
            None,
            [],
            "text",
            {"events": "no"},
            {"events": [None, 3, "x"]},
            {"events": [{"eventAction": "expiration"}]},
            {"events": [{"eventAction": "expiration", "eventDate": "soon"}]},
            {"events": [{"eventAction": "expiration", "eventDate": 5}]},
        ],
    )
    def test_an_answer_that_is_not_what_it_should_be_has_no_expiry_and_raises_nothing(
        self, document: Any
    ) -> None:
        assert rdap.expiration_from(document) is None

    def test_when_a_registry_says_it_twice_the_later_one_is_taken(self) -> None:
        document = self.document(
            {"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"},
            {"eventAction": "expiration", "eventDate": "2028-08-13T04:00:00Z"},
        )

        assert rdap.expiration_from(document) == datetime(2028, 8, 13, 4, tzinfo=timezone.utc)

    def test_an_answer_is_read_from_the_bytes_the_registry_sent(self) -> None:
        body = json.dumps(
            self.document({"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"})
        ).encode()

        assert rdap.parse_answer(body) is not None
        assert rdap.parse_answer(b"not json") is None
        assert rdap.parse_answer(b"[1, 2]") is None
        assert rdap.parse_answer(b"\xff\xfe") is None


class TestTheGenerator:
    """The script that writes the table, run on a small list, never on the network."""

    @pytest.fixture
    def generator(self) -> Any:
        spec = importlib.util.spec_from_file_location(
            "generate_rdap_bootstrap", ROOT / "scripts" / "generate_rdap_bootstrap.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["generate_rdap_bootstrap"] = module
        spec.loader.exec_module(module)
        yield module
        sys.modules.pop("generate_rdap_bootstrap", None)

    LIST: dict[str, Any] = {
        "publication": "2026-10-28T22:00:04Z",
        "services": [
            [["COM", "net"], ["https://rdap.verisign.com/com/v1/"]],
            [["kg"], ["http://rdap.cctld.kg/"]],
            [["uk"], ["http://old.example/uk/", "https://rdap.nominet.uk/uk"]],
            [["xn--p1ai"], ["https://rdap.example.net/rf/"]],
        ],
    }

    def test_it_writes_a_module_that_says_what_it_is_and_where_it_came_from(
        self, generator: Any
    ) -> None:
        text = generator.render(self.LIST)

        assert text.startswith("# Copyright (c) 2024-2026 Yago López Prado\n")
        assert "never edited by hand" in text
        assert 'PUBLICATION = "2026-10-28T22:00:04Z"' in text

    def test_the_module_it_writes_is_the_table(self, generator: Any, tmp_path: Path) -> None:
        path = tmp_path / "data.py"
        path.write_text(generator.render(self.LIST), encoding="utf-8")
        namespace: dict[str, Any] = {}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)  # noqa: S102

        assert dict(namespace["SERVICES"]) == {
            "https://rdap.verisign.com/com/v1/": "com net",
            "https://rdap.nominet.uk/uk/": "uk",
            "https://rdap.example.net/rf/": "xn--p1ai",
        }

    def test_a_service_with_no_https_address_is_left_out(self, generator: Any) -> None:
        text = generator.render(self.LIST)

        assert "rdap.cctld.kg" not in text
        assert "(1 of them)" in text

    def test_the_same_list_always_gives_the_same_file(self, generator: Any) -> None:
        shuffled = {**self.LIST, "services": list(reversed(self.LIST["services"]))}

        assert generator.render(self.LIST) == generator.render(shuffled)

    def test_a_long_list_of_top_level_domains_is_wrapped(self, generator: Any) -> None:
        many = {
            "publication": "x",
            "services": [[[f"tld{n:03d}" for n in range(200)], ["https://rdap.big.example/"]]],
        }

        text = generator.render(many)

        assert max(len(line) for line in text.splitlines()) <= 100
        assert text.count("tld199") == 1

    def test_the_committed_table_is_what_the_generator_writes_for_its_own_contents(
        self, generator: Any
    ) -> None:
        """Nothing was edited by hand: rendering what the module says gives the module back."""
        rebuilt = {
            "publication": rdap_bootstrap_data.PUBLICATION,
            "services": [[tlds.split(), [base]] for base, tlds in rdap_bootstrap_data.SERVICES],
        }
        committed = (ROOT / "src" / "noust" / "core" / "rdap_bootstrap_data.py").read_text(
            encoding="utf-8"
        )

        assert generator.render(rebuilt).split('"""\n', 2)[2] == committed.split('"""\n', 2)[2]
