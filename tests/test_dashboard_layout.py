# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The document of a home panel: version 1, closed, small.

A panel is the console's own business (which widgets, where, at what size), but the server keeps it
and a person writes it, so it is judged at one place before it is stored: an unknown key, a widget
off the 12-column grid, a setting that is not a scalar or a document past its size is a
``ValidationError`` naming the field, never kept and never ignored. The same checks are attacked in
``tests/test_adversarial_inputs.py``.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.validators import dashboards


def widget(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "attention",
        "type": "needs-attention",
        "x": 0,
        "y": 0,
        "w": 12,
        "h": 3,
        "settings": {},
    }
    return {**base, **changes}


def layout(*widgets: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "widgets": list(widgets)}


def refused(document: Any) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        dashboards.check_layout(document)
    return caught.value


class TestTheDocument:
    def test_an_empty_panel_is_a_panel(self) -> None:
        assert dashboards.check_layout(layout()) == {"version": 1, "widgets": []}

    def test_a_valid_document_comes_back_as_it_was(self) -> None:
        document = layout(
            widget(),
            widget(
                id="cpu-1",
                type="metric-chart",
                x=0,
                y=3,
                w=6,
                h=4,
                settings={
                    "metric": "cpu",
                    "range": "24h",
                    "limit": 5,
                    "smooth": True,
                    "ratio": 0.5,
                },
            ),
        )

        assert dashboards.check_layout(copy.deepcopy(document)) == document

    def test_the_answer_is_a_copy_the_caller_cannot_use_to_change_what_was_judged(self) -> None:
        document = layout(widget(settings={"metric": "cpu"}))

        checked = dashboards.check_layout(document)
        document["widgets"][0]["settings"]["metric"] = "x" * 5000

        assert checked["widgets"][0]["settings"] == {"metric": "cpu"}

    @pytest.mark.parametrize("document", [None, [], "{}", 1, True])
    def test_it_is_an_object(self, document: Any) -> None:
        assert refused(document).field == "layout"

    @pytest.mark.parametrize("version", [0, 2, "1", 1.0, True, None])
    def test_only_version_1_is_understood(self, version: Any) -> None:
        error = refused({"version": version, "widgets": []})

        assert error.field == "layout.version"

    def test_a_missing_version_or_widgets_is_named(self) -> None:
        assert refused({"widgets": []}).field == "layout.version"
        assert refused({"version": 1}).field == "layout.widgets"

    def test_a_key_the_schema_does_not_have_is_refused_by_name(self) -> None:
        error = refused({"version": 1, "widgets": [], "theme": "dark"})

        assert error.field == "layout.theme"

    def test_widgets_is_a_list(self) -> None:
        assert refused({"version": 1, "widgets": {}}).field == "layout.widgets"
        assert refused({"version": 1, "widgets": "x"}).field == "layout.widgets"

    def test_at_most_forty_widgets(self) -> None:
        forty = [widget(id=f"w{i}", y=i, h=1) for i in range(40)]
        assert len(dashboards.check_layout(layout(*forty))["widgets"]) == 40

        error = refused(layout(*forty, widget(id="w40", y=40, h=1)))

        assert error.field == "layout.widgets"
        assert "40" in error.message

    def test_a_document_past_32_kb_is_refused_however_few_its_widgets(self) -> None:
        # Forty widgets of 800 characters of settings each stay under 2 KB apiece and over 32 KB.
        settings = {f"k{i}": "x" * 190 for i in range(4)}
        many = [widget(id=f"w{i}", y=i, h=1, settings=settings) for i in range(40)]
        assert len(json.dumps(many)) > dashboards.MAX_DOCUMENT_BYTES

        error = refused(layout(*many))

        assert error.field == "layout"
        assert "32" in error.message


class TestAWidget:
    def test_it_is_an_object_with_the_seven_keys(self) -> None:
        assert refused(layout("x")).field == "layout.widgets[0]"  # type: ignore[arg-type]
        missing = widget()
        del missing["settings"]
        assert refused(layout(missing)).field == "layout.widgets[0].settings"

    def test_a_key_the_schema_does_not_have_is_refused_by_name(self) -> None:
        assert refused(layout(widget(title="x"))).field == "layout.widgets[0].title"

    def test_the_second_widget_is_named_by_its_place(self) -> None:
        error = refused(layout(widget(), widget(id="b", w=0)))

        assert error.field == "layout.widgets[1].w"

    @pytest.mark.parametrize("name", ["a", "needs-attention", "a1", "a" + "b" * 40])
    def test_a_type_is_a_slug_of_at_most_41_characters(self, name: str) -> None:
        assert dashboards.check_layout(layout(widget(type=name)))["widgets"][0]["type"] == name

    @pytest.mark.parametrize(
        "name",
        ["", "A", "1a", "-a", "a_b", "a b", "a.b", "a/b", "a\n", "a" + "b" * 41, 5, None],
    )
    def test_a_type_that_is_not_one_is_refused(self, name: Any) -> None:
        assert refused(layout(widget(type=name))).field == "layout.widgets[0].type"

    @pytest.mark.parametrize("name", ["a", "w-1", "0", "a" * 40])
    def test_an_id_is_a_short_slug(self, name: str) -> None:
        assert dashboards.check_layout(layout(widget(id=name)))["widgets"][0]["id"] == name

    @pytest.mark.parametrize("name", ["", "A", "-a", "a_b", "a\n", "a" * 41, 5, None])
    def test_an_id_that_is_not_one_is_refused(self, name: Any) -> None:
        assert refused(layout(widget(id=name))).field == "layout.widgets[0].id"

    def test_two_widgets_cannot_share_an_id(self) -> None:
        error = refused(layout(widget(), widget(y=3)))

        assert error.field == "layout.widgets[1].id"

    @pytest.mark.parametrize(
        ("x", "w"), [(0, 1), (0, 12), (11, 1), (6, 6), (3, 9), (0, 4), (4, 4), (8, 4)]
    )
    def test_a_widget_inside_the_twelve_columns(self, x: int, w: int) -> None:
        assert dashboards.check_layout(layout(widget(x=x, w=w)))["widgets"][0]["w"] == w

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("x", -1),
            ("x", 12),
            ("w", 0),
            ("w", 13),
            ("y", -1),
            ("h", 0),
            ("h", 25),
            ("y", 1000),
            ("x", 1.5),
            ("x", "0"),
            ("x", True),
            ("w", None),
            ("h", 2.0),
        ],
    )
    def test_a_position_or_size_off_the_grid_is_refused(self, field: str, value: Any) -> None:
        assert refused(layout(widget(**{field: value}))).field == f"layout.widgets[0].{field}"

    def test_a_widget_that_starts_inside_and_ends_outside_the_grid_is_refused(self) -> None:
        error = refused(layout(widget(x=8, w=6)))

        assert error.field == "layout.widgets[0].w"
        assert "12 columns" in error.message


class TestTheSettingsOfAWidget:
    def test_scalars_are_kept(self) -> None:
        settings = {"a": "text", "b": 3, "c": 1.5, "d": False, "e": ""}

        checked = dashboards.check_layout(layout(widget(settings=settings)))

        assert checked["widgets"][0]["settings"] == settings

    @pytest.mark.parametrize(
        "value", [None, [], [1], {}, {"a": 1}, (1,), b"x", float("nan"), float("inf")]
    )
    def test_nothing_but_a_scalar_is_a_setting(self, value: Any) -> None:
        error = refused(layout(widget(settings={"metric": value})))

        assert error.field == "layout.widgets[0].settings.metric"

    def test_settings_is_an_object(self) -> None:
        for value in (None, [], "x", 1):
            assert refused(layout(widget(settings=value))).field == "layout.widgets[0].settings"

    @pytest.mark.parametrize(
        "name", ["", "1a", "a b", "a.b", "a\n", "a" * 41, "-a", "$ref", "__proto__"]
    )
    def test_a_name_of_a_setting_is_an_identifier(self, name: str) -> None:
        error = refused(layout(widget(settings={name: 1})))

        assert error.field == "layout.widgets[0].settings"

    @pytest.mark.parametrize("text", ["a\n", "a\x00", "a\x1b[31m", "a\u2028b", "a\u202eb", "\x7f"])
    def test_a_text_with_a_control_character_is_refused(self, text: str) -> None:
        error = refused(layout(widget(settings={"metric": text})))

        assert error.field == "layout.widgets[0].settings.metric"

    def test_a_text_past_200_characters_is_refused(self) -> None:
        assert dashboards.check_layout(layout(widget(settings={"a": "x" * 200})))
        error = refused(layout(widget(settings={"a": "x" * 201})))

        assert error.field == "layout.widgets[0].settings.a"

    def test_settings_past_2_kb_are_refused_in_bytes_not_in_characters(self) -> None:
        # Ten values of 200 two-byte characters are 2000 characters and 4000 bytes.
        settings = {f"k{i}": "é" * 200 for i in range(10)}

        error = refused(layout(widget(settings=settings)))

        assert error.field == "layout.widgets[0].settings"
        assert "2 KB" in error.message

    def test_settings_just_under_2_kb_are_kept(self) -> None:
        settings = {f"k{i}": "x" * 190 for i in range(10)}
        assert len(json.dumps(settings, separators=(",", ":"))) < 2048

        assert dashboards.check_layout(layout(widget(settings=settings)))

    def test_an_integer_too_big_for_a_browser_is_refused(self) -> None:
        # JSON numbers past 2**53 do not survive a round trip through JavaScript.
        error = refused(layout(widget(settings={"n": 2**60})))

        assert error.field == "layout.widgets[0].settings.n"


class TestTheIdentityOfAPanel:
    @pytest.mark.parametrize("name", ["main", "a", "0", "my-panel-2", "a" * 40])
    def test_an_id_is_a_short_slug(self, name: str) -> None:
        assert dashboards.check_dashboard_id(name) == name

    @pytest.mark.parametrize(
        "name",
        ["", "Main", "-x", "x_y", "x y", "x/y", "x.y", "x\n", "a" * 41, "order", None, 3],
    )
    def test_an_id_that_is_not_one_is_refused_and_order_is_reserved(self, name: Any) -> None:
        with pytest.raises(ValidationError) as caught:
            dashboards.check_dashboard_id(name)

        assert caught.value.field == "dashboard"

    def test_a_name_is_trimmed_and_between_one_and_sixty_characters(self) -> None:
        assert dashboards.check_dashboard_name("  Este servidor ") == "Este servidor"
        assert dashboards.check_dashboard_name("n" * 60) == "n" * 60
        for bad in ("", "   ", "n" * 61, "a\nb", "a\x00", "a\u202eb", None, 5):
            with pytest.raises(ValidationError) as caught:
                dashboards.check_dashboard_name(bad)
            assert caught.value.field == "name"

    @pytest.mark.parametrize("scope", ["server", "fleet"])
    def test_a_scope_is_the_server_or_the_fleet(self, scope: str) -> None:
        assert dashboards.check_dashboard_scope(scope) == scope

    @pytest.mark.parametrize("scope", ["", "Server", "node", "server\n", "all", None, 1])
    def test_nothing_else_is_a_scope(self, scope: Any) -> None:
        with pytest.raises(ValidationError) as caught:
            dashboards.check_dashboard_scope(scope)

        assert caught.value.field == "scope"


class TestTheTextThatIsStored:
    def test_it_is_the_same_text_for_the_same_document(self) -> None:
        one = dashboards.check_layout(layout(widget(settings={"b": 1, "a": 2})))
        other = dashboards.check_layout(layout(widget(settings={"a": 2, "b": 1})))

        assert dashboards.layout_text(one) == dashboards.layout_text(other)
        assert json.loads(dashboards.layout_text(one)) == one

    def test_it_is_compact(self) -> None:
        assert dashboards.layout_text({"version": 1, "widgets": []}) == '{"version":1,"widgets":[]}'
