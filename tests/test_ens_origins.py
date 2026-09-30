# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where code may be deployed from (ENS G18, op.exp.5 and mp.sw.1).

An empty list allows any origin, as every earlier version did. A list names
hosts, organisations or repositories, and a source outside it is refused
where every source is classified, before git or a download runs.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.ens import origins
from noust.core.ens.origins import OriginNotAllowed, check_origin, describe_origin, matches
from noust.core.exceptions import SourceError


class TestMatching:
    @pytest.mark.parametrize(
        ("pattern", "source"),
        [
            ("github.com/acme", "https://github.com/acme/shop.git"),
            ("github.com/acme", "git@github.com:acme/shop.git"),
            ("github.com/Acme", "https://github.com/acme/shop"),
            ("github.com/acme/shop", "https://github.com/acme/shop.git#main"),
            ("git.example.com", "https://git.example.com/team/api.git"),
            ("*.example.com", "https://git.example.com/team/api.git"),
            ("https://github.com/acme", "https://github.com/acme/shop.git"),
            ("releases.example.com", "https://releases.example.com/app-1.0.tar.gz"),
            ("local", "/srv/code/shop"),
        ],
    )
    def test_an_allowed_origin_matches(self, pattern: str, source: str) -> None:
        assert matches(pattern, describe_origin(source))

    @pytest.mark.parametrize(
        ("pattern", "source"),
        [
            ("github.com/acme", "https://github.com/acme-evil/shop.git"),
            ("github.com/acme", "https://github.com/other/acme.git"),
            ("github.com/acme/shop", "https://github.com/acme/shop-fork.git"),
            ("git.example.com", "https://git.example.com.evil.net/team/api.git"),
            ("*.example.com", "https://example.com.evil.net/x.git"),
            ("github.com", "https://gitlab.com/acme/shop.git"),
            ("github.com", "/srv/code/shop"),
            ("local", "https://github.com/acme/shop.git"),
        ],
    )
    def test_anything_else_does_not(self, pattern: str, source: str) -> None:
        assert not matches(pattern, describe_origin(source))

    def test_credentials_in_a_url_do_not_change_the_origin(self) -> None:
        origin = describe_origin("https://x-access-token:secret@github.com/acme/shop.git")

        assert origin.host == "github.com"
        assert origin.path == ("acme", "shop")
        assert "secret" not in origin.display


class TestTheGuard:
    def test_an_empty_list_allows_everything(self) -> None:
        check_origin("https://gitlab.com/anyone/anything.git", [])

    def test_a_source_outside_the_list_is_refused_with_what_to_do(self) -> None:
        with pytest.raises(OriginNotAllowed) as caught:
            check_origin("https://gitlab.com/anyone/anything.git", ["github.com/acme"])

        assert isinstance(caught.value, SourceError)
        assert "gitlab.com/anyone/anything" in caught.value.message
        assert "security.allowed_sources" in (caught.value.details or "")

    def test_a_refusal_is_recorded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            origins, "_record_refusal", lambda display, allowed: recorded.append((display, allowed))
        )

        with pytest.raises(OriginNotAllowed):
            check_origin("https://gitlab.com/x/y.git", ["github.com/acme"])

        assert recorded and recorded[0][0] == "gitlab.com/x/y"

    def test_the_list_is_read_from_the_security_section(self) -> None:
        class Config:
            def get(self, key: str, default: Any = None) -> Any:
                return ["github.com/acme", " ", 3] if key == "security.allowed_sources" else default

        assert origins.allowed_sources(Config()) == ["github.com/acme", "3"]

    def test_the_source_chokepoint_applies_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.managers import source_manager

        monkeypatch.setattr(origins, "allowed_sources", lambda config=None: ["github.com/acme"])

        with pytest.raises(OriginNotAllowed):
            source_manager._validate_source_keeping_credentials("https://gitlab.com/x/y.git")
        kind, normalized = source_manager._validate_source_keeping_credentials(
            "https://github.com/acme/shop.git"
        )
        assert kind == "git" and normalized.endswith("acme/shop.git")
