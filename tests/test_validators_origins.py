# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What one entry of ``security.allowed_sources`` may be, and what it stores as.

The entry is compared as text against where a source is, so an entry that cannot match (a
bracket kept from a list, a path, a user, a port) is refused where it is saved, with the shape that
works. The case that made this a rule: ``[github.com/Arenna-Labs`` and ``local]`` were saved from
one mistyped list, and every deployment of the organisation was refused from then on.
"""

from __future__ import annotations

import pytest

from noust.core.ens.origins import describe_origin, matches
from noust.core.exceptions import ValidationError
from noust.validators.origins import (
    KEYWORDS,
    check_allowed_source,
    parse_allowed_source,
)


class TestWhatIsAccepted:
    @pytest.mark.parametrize(
        ("typed", "stored"),
        [
            ("github.com", "github.com"),
            ("GitHub.com", "github.com"),
            ("github.com/Arenna-Labs", "github.com/Arenna-Labs"),
            ("github.com/acme/shop", "github.com/acme/shop"),
            ("github.com/acme/shop.git", "github.com/acme/shop"),
            ("https://github.com/acme/shop.git", "github.com/acme/shop"),
            ("https://github.com/acme/", "github.com/acme"),
            ("ssh://git.example.com/team", "git.example.com/team"),
            ("*.example.com", "*.example.com"),
            ("*.Example.com/team", "*.example.com/team"),
            ("gitlab.com/group/sub/repo", "gitlab.com/group/sub/repo"),
            ("github.com/acme/.github", "github.com/acme/.github"),
            ("gitea", "gitea"),
            ("10.0.0.5", "10.0.0.5"),
            ("10.0.0.5/team", "10.0.0.5/team"),
            ("  local  ", "local"),
            ("github.com/acme\n", "github.com/acme"),
            ("LOCAL", "local"),
            ("Recipes", "recipes"),
            ("push", "push"),
            ("upload", "upload"),
        ],
    )
    def test_an_entry_is_stored_in_one_form(self, typed: str, stored: str) -> None:
        assert check_allowed_source(typed) == stored

    @pytest.mark.parametrize("word", KEYWORDS)
    def test_the_words_have_no_host(self, word: str) -> None:
        parsed = parse_allowed_source(word)

        assert parsed.keyword == word and parsed.host == "" and parsed.path == ()

    def test_the_words_are_the_four_places_without_a_host(self) -> None:
        assert KEYWORDS == ("local", "push", "upload", "recipes")


class TestWhatIsRefusedAndWhy:
    @pytest.mark.parametrize(
        ("typed", "why"),
        [
            ("[github.com/Arenna-Labs", "brackets"),
            ("local]", "brackets"),
            ("[local]", "brackets"),
            ("git@github.com:acme/shop.git", "user or a token"),
            ("https://user:token@github.com/acme", "user or a token"),
            ("github.com:8443/acme", "port"),
            ("/srv/code/shop", "host name"),
            ("~/code", "no host, organisation or repository has"),
            ("github.com//acme", "organisation or repository"),
            ("github.com/acme/../evil", "organisation or repository"),
            ("github.com/acme/./shop", "organisation or repository"),
            ("github.com/ac me", "space"),
            ("github.com/acme;id", "no host, organisation or repository has"),
            ("github.com/acme?x=1", "no host, organisation or repository has"),
            ("github.com/acme,local", "no host, organisation or repository has"),
            ("*", "host name"),
            ("*.com", "wildcard needs a domain"),
            ("a*.example.com", "host name"),
            ("*.*.example.com", "host name"),
            ("ftp://github.com/acme", "scheme"),
            ("file:///etc", "scheme"),
            ("", "empty"),
            ("   ", "empty"),
            ("githúb.com", "ASCII"),
            ("github.com/acme/" + "a" * 300, "longer"),
            ("github.com/" + "/".join("a" * 3 for _ in range(12)), "more than"),
        ],
    )
    def test_an_entry_that_cannot_match_is_refused_with_the_shapes_that_do(
        self, typed: str, why: str | None
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            check_allowed_source(typed)

        error = caught.value
        assert error.field == "security.allowed_sources"
        assert "github.com/acme/shop" in (error.details or "")
        if why is not None:
            assert why in error.message

    @pytest.mark.parametrize("value", [None, 3, ["github.com"], b"github.com", object()])
    def test_an_entry_that_is_not_text_is_refused(self, value: object) -> None:
        with pytest.raises(ValidationError):
            check_allowed_source(value)

    def test_the_error_does_not_echo_a_long_entry(self) -> None:
        with pytest.raises(ValidationError) as caught:
            check_allowed_source("x" * 5000)

        assert len(caught.value.message) < 300


class TestTheEntryMeansWhatTheMatcherReads:
    """One entry, two readers: the validator that saves it and the matcher that applies it."""

    @pytest.mark.parametrize(
        ("entry", "source"),
        [
            ("github.com/Arenna-Labs", "https://github.com/arenna-labs/shop.git"),
            ("https://github.com/acme/shop.git", "git@github.com:acme/shop.git"),
            ("*.Example.com", "https://git.example.com/team/api.git"),
            ("github.com/acme/", "https://github.com/acme/shop"),
        ],
    )
    def test_what_is_stored_matches_what_was_typed(self, entry: str, source: str) -> None:
        assert matches(entry, describe_origin(source))
        assert matches(check_allowed_source(entry), describe_origin(source))

    @pytest.mark.parametrize(
        ("entry", "source"),
        [
            ("github.com/acme", "https://github.com/acme-evil/shop.git"),
            ("github.com/acme/shop.git", "https://github.com/acme/shop-fork"),
        ],
    )
    def test_storing_it_does_not_widen_it(self, entry: str, source: str) -> None:
        assert not matches(check_allowed_source(entry), describe_origin(source))
