# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The registry of the names from before 4.0 that are still accepted (``noust.core.legacy_names``).

Each one has to say what it is, why it is still met and until when: a name that is not in the
registry is a name nobody decided to keep.
"""

from __future__ import annotations

from noust.core import legacy_names


def test_every_entry_says_what_it_is_why_it_is_kept_and_until_when() -> None:
    """The reason and the date are what make a legacy name a decision and not an accident."""
    for entry in legacy_names.REGISTRY:
        assert entry.key and entry.old, entry
        assert entry.handling in {"read", "renamed", "kept", "ignored"}, entry
        assert len(entry.reason) > 25, f"{entry.key}: say why it is still met"
        assert entry.until, f"{entry.key}: say until when"


def test_the_registry_has_no_duplicates() -> None:
    keys = [entry.key for entry in legacy_names.REGISTRY]
    olds = [entry.old for entry in legacy_names.REGISTRY]
    assert len(keys) == len(set(keys))
    assert len(olds) == len(set(olds))


def test_every_constant_of_the_module_is_in_the_registry() -> None:
    """A constant nobody lists would be accepted forever without anyone having said so."""
    registered = {entry.old for entry in legacy_names.REGISTRY}
    registered |= {
        legacy_names.NGINX_OVERRIDE_FILES[1],
    }
    for name in legacy_names.__all__:
        value = getattr(legacy_names, name)
        if name in {"REGISTRY", "LegacyName", "NEXT_MAJOR"}:
            continue
        values = value if isinstance(value, tuple) else (value,)
        for item in values:
            assert item in registered, f"{name} = {item!r} is not in REGISTRY"


def test_what_is_written_is_never_the_old_name() -> None:
    """The registry's ``new`` column is what Noust writes, and it never carries the old word."""
    for entry in legacy_names.REGISTRY:
        assert "wasm" not in entry.new.lower(), entry
