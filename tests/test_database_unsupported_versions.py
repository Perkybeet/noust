# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Versions whose upstream support has ended, offered as instances (4.1, item 103).

An operator with an application that needs MySQL 5.7 or PostgreSQL 13 can run it in a container
beside the host's engine. Noust offers those versions as it offers the others, pinned by digest,
and says what they cost: the date the project stopped fixing security problems, and a
confirmation (``unsupported_ok``) that nothing queues without. What is defended:

- **a version that is offered is pinned**, by a digest in the catalog's own table, for the
  processors the image is published for (MySQL 5.7 only for amd64);
- **an older version is never the default**, and never answers a prefix an operator used to
  type for the supported one (``8`` was MySQL 8.4 and stays so);
- **the date is the project's**, kept in ``eol.py``, and a version without one is not offered
  as unsupported;
- **the confirmation is asked for what is unsupported**, from the table or because its date has
  passed, and for an image of the operator's own that says it is such a version.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from noust.core.exceptions import ValidationError
from noust.managers.database.eol import END_OF_LIFE
from noust.managers.database.instance_catalog import (
    SPECS,
    offered,
    resolve_pin,
    spec_for,
    unsupported_notice,
)

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

#: What item 103 names, and what Noust adds because its image exists and its date is published.
LEGACY = {
    "mysql": ["5.7", "8.0"],
    "postgresql": ["12", "13", "14"],
    "mariadb": ["10.6"],
    "mongodb": ["6.0"],
}


def legacy_of(flavour: str) -> list[str]:
    return [pin.version for pin in SPECS[flavour].pins if pin.legacy]


class TestWhatIsOffered:
    @pytest.mark.parametrize(("flavour", "versions"), sorted(LEGACY.items()))
    def test_the_versions_without_support_are_in_the_catalog(
        self, flavour: str, versions: list[str]
    ) -> None:
        assert legacy_of(flavour) == versions

    def test_every_one_is_pinned_by_a_digest_of_its_own(self) -> None:
        digests = [pin.digest for spec in SPECS.values() for pin in spec.pins]

        assert all(DIGEST.fullmatch(digest) for digest in digests)
        assert len(digests) == len(set(digests)), "two tags cannot be one image"

    def test_every_one_has_the_date_its_project_stopped_supporting_it(self) -> None:
        for flavour, versions in LEGACY.items():
            for version in versions:
                assert version in END_OF_LIFE[flavour], (flavour, version)

    def test_an_older_version_is_never_the_one_offered_when_none_is_asked_for(self) -> None:
        for spec in SPECS.values():
            assert not spec.default_pin().legacy, spec.flavour

    def test_the_oldest_come_first_so_that_a_list_reads_in_order(self) -> None:
        for spec in SPECS.values():
            flags = [pin.legacy for pin in spec.pins]
            assert flags == sorted(flags, reverse=True), spec.flavour

    def test_mysql_57_is_published_for_amd64_only_and_says_so(self) -> None:
        pin = next(pin for pin in SPECS["mysql"].pins if pin.version == "5.7")

        assert pin.platforms == ("amd64",)
        assert all(
            other.platforms is None for other in SPECS["mysql"].pins if other.version != "5.7"
        )

    def test_the_catalog_listing_says_which_are_unsupported_and_since_when(self) -> None:
        [entry] = [item for item in offered() if item["flavour"] == "mysql"]
        by_version = {item["version"]: item for item in entry["versions"]}

        assert by_version["5.7"]["unsupported"] is True
        assert by_version["5.7"]["end_of_life"] == "2023-10-31"
        assert by_version["5.7"]["support"] == "ended"
        assert by_version["5.7"]["platforms"] == ["amd64"]
        assert by_version["8.0"]["unsupported"] is True
        assert by_version["8.4"]["unsupported"] is False
        assert by_version["8.4"]["platforms"] == ["amd64", "arm64"]
        assert by_version["8.4"]["default"] is True


class TestAskingForAVersion:
    def test_a_prefix_still_means_the_supported_version_it_meant(self) -> None:
        assert resolve_pin(spec_for("mysql"), "8").version == "8.4"
        assert resolve_pin(spec_for("mariadb"), "10").version == "10.11"
        assert resolve_pin(spec_for("postgresql"), "17").version == "17"

    def test_the_exact_older_version_is_found(self) -> None:
        assert resolve_pin(spec_for("mysql"), "8.0").version == "8.0"
        assert resolve_pin(spec_for("mysql"), "5.7").legacy is True
        assert resolve_pin(spec_for("postgresql"), "13").legacy is True
        assert resolve_pin(spec_for("mariadb"), "10.6").version == "10.6"

    def test_a_prefix_that_only_an_older_version_has_finds_it(self) -> None:
        assert resolve_pin(spec_for("mysql"), "5").version == "5.7"

    def test_an_ambiguous_prefix_among_the_supported_is_still_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            resolve_pin(spec_for("mariadb"), "11")

        assert "ambiguous" in caught.value.message
        assert caught.value.field == "version"

    def test_a_version_nobody_offers_lists_the_older_ones_too(self) -> None:
        with pytest.raises(ValidationError) as caught:
            resolve_pin(spec_for("mysql"), "5.6")

        assert "5.7" in caught.value.details and "8.4" in caught.value.details


class TestWhatTheConfirmationIsAskedFor:
    def test_a_version_in_the_table_of_older_ones_needs_it(self) -> None:
        spec = spec_for("mysql")
        notice = unsupported_notice(spec, resolve_pin(spec, "5.7"))

        assert notice is not None
        assert (notice.status, notice.end_of_life) == ("ended", "2023-10-31")

    def test_one_that_is_still_supported_but_is_in_the_table_needs_it_and_says_when_it_ends(
        self,
    ) -> None:
        spec = spec_for("postgresql")
        notice = unsupported_notice(spec, resolve_pin(spec, "14"), today=date(2026, 10, 29))

        assert notice is not None
        assert notice.status == "ending_soon"
        assert notice.end_of_life == "2026-11-12"

    def test_the_current_versions_need_nothing(self) -> None:
        for spec in SPECS.values():
            assert unsupported_notice(spec, spec.default_pin(), today=date(2026, 10, 29)) is None

    def test_a_version_whose_date_passes_needs_it_without_being_in_the_table(self) -> None:
        spec = spec_for("postgresql")
        pin = resolve_pin(spec, "15")

        assert unsupported_notice(spec, pin, today=date(2027, 1, 1)) is None
        notice = unsupported_notice(spec, pin, today=date(2027, 11, 12))
        assert notice is not None and notice.status == "ended"

    def test_a_version_with_no_published_date_is_not_unsupported(self) -> None:
        spec = spec_for("redis")

        assert all(unsupported_notice(spec, pin) is None for pin in spec.pins)
