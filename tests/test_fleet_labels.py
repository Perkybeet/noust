"""
Node labels: what they may look like, how they select, and that they go with the node.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.fleet.labels import MAX_LABELS, NodeLabels, matches, parse_label, parse_selector
from tests.fleet_support import build_fleet


@pytest.fixture
def fleet(tmp_path: Path):
    built = build_fleet(tmp_path)
    built.added("web-2")
    built.added("web-3")
    yield built
    NoustStore.reset_instance()


class TestParsing:
    def test_a_pair(self):
        assert parse_label("env=prod") == ("env", "prod")
        assert parse_label("team.owner=ops_1") == ("team.owner", "ops_1")
        assert parse_label("canary=") == ("canary", "")

    @pytest.mark.parametrize("text", ["env", "Env=prod", "-env=prod", "env=pr od", "env=a/b", "=x"])
    def test_what_is_not_a_label(self, text):
        with pytest.raises(ValidationError):
            parse_label(text)

    def test_a_selector_ands_its_pairs(self):
        assert parse_selector(["env=prod,role=web", "zone=eu"]) == {
            "env": "prod",
            "role": "web",
            "zone": "eu",
        }
        assert matches({"env": "prod", "role": "web"}, {"env": "prod"})
        assert not matches({"env": "prod"}, {"env": "prod", "role": "web"})

    def test_a_key_asked_two_values_is_refused(self):
        with pytest.raises(ValidationError, match="both"):
            parse_selector(["env=prod", "env=dev"])


class TestNodeLabels:
    def test_set_remove_and_replace(self, fleet):
        labels = NodeLabels(fleet.store)

        assert labels.change("web-2", set_labels={"env": "prod", "role": "web"}) == {
            "env": "prod",
            "role": "web",
        }
        assert labels.change("web-2", set_labels={"env": "dev"}, remove=["role"]) == {"env": "dev"}
        assert labels.change("web-2", set_labels={"zone": "eu"}, replace=True) == {"zone": "eu"}
        assert labels.of("web-2") == {"zone": "eu"}
        assert labels.all() == {"web-2": {"zone": "eu"}}

    def test_select_keeps_the_order_and_never_selects_everything_by_default(self, fleet):
        labels = NodeLabels(fleet.store)
        labels.change("web-3", set_labels={"env": "prod"})
        labels.change("web-2", set_labels={"env": "prod"})

        assert labels.select({"env": "prod"}, ["web-2", "web-3"]) == ["web-2", "web-3"]
        assert labels.select({}, ["web-2", "web-3"]) == []

    def test_an_unknown_node_is_refused(self, fleet):
        with pytest.raises(ValidationError, match="No node named"):
            NodeLabels(fleet.store).change("nope", set_labels={"env": "prod"})

    def test_there_is_a_limit(self, fleet):
        many = {f"k{index}": "v" for index in range(MAX_LABELS + 1)}
        with pytest.raises(ValidationError, match="at most"):
            NodeLabels(fleet.store).change("web-2", set_labels=many)

    def test_they_go_with_the_node(self, fleet):
        NodeLabels(fleet.store).change("web-2", set_labels={"env": "prod"})

        fleet.manager.remove("web-2", revoke=False)

        assert NodeLabels(fleet.store).all() == {}
