# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ENS inventory (G15, op.exp.1): owner, criticality and classification per application.

The fields are a table of their own (``app_inventory``), not columns of
``apps``, so the store's App records and a redeploy that rewrites them never
touch them; they go with their application; they export as JSON and CSV
without a credential that a source URL may carry.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.ens import inventory as inventory_module
from noust.core.ens.inventory import Inventory, InventoryError, export_csv, export_json
from noust.core.store import App, NoustStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    def fake(event: str, **kwargs: Any) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr(inventory_module, "record", fake)
    return events


def add_app(store: NoustStore, domain: str, source: str = "https://github.com/acme/shop.git"):
    return store.create_app(App(domain=domain, app_type="nodejs", source=source, app_path="/x"))


class TestTheInventory:
    def test_every_application_is_listed_even_without_fields(self, store) -> None:
        add_app(store, "shop.example.com")

        (entry,) = Inventory(store).list()

        assert entry.domain == "shop.example.com"
        assert (entry.owner, entry.criticality, entry.classification) == (None, None, None)
        assert entry.complete is False

    def test_setting_fields_records_them_and_who(self, store, recorded) -> None:
        add_app(store, "shop.example.com")

        entry = Inventory(store).set(
            "shop.example.com",
            owner="Ventas (maria@example.com)",
            criticality="high",
            classification="internal",
            actor="maria",
        )

        assert (entry.owner, entry.criticality, entry.classification) == (
            "Ventas (maria@example.com)",
            "high",
            "internal",
        )
        assert entry.updated_by == "maria" and entry.complete
        assert recorded[0][0] == "apps.inventory"
        assert recorded[0][1]["target"] == "app:shop.example.com"

    def test_unset_fields_keep_their_value(self, store, recorded) -> None:
        add_app(store, "shop.example.com")
        inventory = Inventory(store)
        inventory.set("shop.example.com", owner="Ventas", criticality="high", actor="a")

        entry = inventory.set("shop.example.com", classification="public", actor="b")

        assert (entry.owner, entry.criticality, entry.classification) == (
            "Ventas",
            "high",
            "public",
        )

    def test_an_empty_value_clears_a_field(self, store, recorded) -> None:
        add_app(store, "shop.example.com")
        inventory = Inventory(store)
        inventory.set("shop.example.com", owner="Ventas", actor="a")

        assert inventory.set("shop.example.com", owner="", actor="a").owner is None

    @pytest.mark.parametrize(
        ("field", "value"), [("criticality", "extreme"), ("classification", "secret")]
    )
    def test_an_unknown_level_is_refused(self, store, field: str, value: str) -> None:
        add_app(store, "shop.example.com")

        with pytest.raises(InventoryError):
            Inventory(store).set("shop.example.com", actor="a", **{field: value})

    def test_an_unknown_application_is_refused(self, store) -> None:
        with pytest.raises(InventoryError):
            Inventory(store).set("nope.example.com", owner="x", actor="a")

    def test_the_row_goes_with_its_application(self, store, recorded) -> None:
        add_app(store, "shop.example.com")
        Inventory(store).set("shop.example.com", owner="Ventas", actor="a")

        store.delete_app("shop.example.com")

        rows = store._get_connection().execute("SELECT COUNT(*) FROM app_inventory").fetchone()
        assert rows[0] == 0


class TestTheExport:
    def test_json_and_csv_carry_every_field_and_no_credential(self, store, recorded) -> None:
        add_app(store, "shop.example.com", "https://x-access-token:s3cr3t@github.com/acme/shop.git")
        Inventory(store).set("shop.example.com", owner="Ventas", criticality="medium", actor="a")
        entries = Inventory(store).list()

        as_json = json.loads(export_json(entries))
        rows = list(csv.DictReader(io.StringIO(export_csv(entries))))

        assert as_json["applications"][0]["owner"] == "Ventas"
        assert rows[0]["criticality"] == "medium"
        assert rows[0]["source"] == "github.com/acme/shop"
        assert "s3cr3t" not in export_json(entries) + export_csv(entries)
