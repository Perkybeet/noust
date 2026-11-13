# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The providers the settings offer by name: few, with an address read from each one's documentation
and no claim beyond what that documentation says (item 171).
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from noust.core.assistant.presets import LOCATIONS, OWN_SIDE, PRESETS, preset
from noust.core.exceptions import ValidationError


def test_there_are_few_and_none_twice() -> None:
    ids = [p.id for p in PRESETS]
    assert 1 <= len(ids) <= 8
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("item", PRESETS, ids=lambda p: p.id)
def test_each_one_is_an_address_up_to_its_version_with_a_place_and_the_page_it_was_read_on(
    item: object,
) -> None:
    parts = urlsplit(item.base_url)  # type: ignore[attr-defined]
    assert parts.scheme in ("http", "https")
    assert parts.hostname
    assert not parts.query and not parts.fragment and not parts.username
    assert parts.path.endswith("/v1")
    assert item.location in LOCATIONS  # type: ignore[attr-defined]
    assert urlsplit(item.docs).scheme == "https"  # type: ignore[attr-defined]


@pytest.mark.parametrize("item", PRESETS, ids=lambda p: p.id)
def test_a_provider_on_this_machine_is_plain_http_to_localhost_and_any_other_is_https(
    item: object,
) -> None:
    parts = urlsplit(item.base_url)  # type: ignore[attr-defined]
    if item.location in OWN_SIDE:  # type: ignore[attr-defined]
        assert parts.scheme == "http"
        assert parts.hostname == "localhost"
    else:
        assert parts.scheme == "https"


def test_a_place_the_documentation_does_not_name_is_not_claimed() -> None:
    # "eu" is only for a service whose own page says so; anything else is "unknown".
    claimed = {p.id for p in PRESETS if p.location == "eu"}
    assert claimed <= {"mistral"}


def test_a_preset_is_found_by_its_id_and_an_unknown_one_says_which_there_are() -> None:
    assert preset("ollama").base_url == "http://localhost:11434/v1"
    with pytest.raises(ValidationError) as caught:
        preset("nope")
    assert "ollama" in caught.value.details
