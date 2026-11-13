# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a Noust can do, in one table: ``noust.central.features``.

Before the table, 146 calls of ``require_server_role`` each carried a label of their own and the
console decided by itself, in sixteen places, what to show a hub. The table is the one answer: a
function is a key, it says what it needs (the server role, or a fact of the machine) and the
guard, the session and the console all read it. These tests hold it closed: a call whose key is
not in the table fails here, and so does a key nobody guards.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import pytest

import noust
from noust import central
from noust.central import features
from noust.central.features import FEATURES, Need
from noust.central.machine import MachineFacts
from noust.core.config import Config
from noust.core.server_text import CATALOG

SOURCE = Path(noust.__file__).resolve().parent

#: A machine that has everything: an ordinary server.
EVERYTHING = MachineFacts(systemd=True, web_server=True, docker=True, root=True, journal=True)

#: The Noust central container on a NAS: no systemd, no web server, no Docker, no root, no journal.
NAS_CONTAINER = MachineFacts(
    systemd=False, web_server=False, docker=False, root=False, journal=False
)


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A configuration read from an empty file in the test's directory."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make this Noust a hub, the way the container image does."""
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    Config.reset_instance()


@cache
def guard_calls() -> tuple[tuple[str, int, ast.expr], ...]:
    """
    Find every call of ``require_server_role`` in the package, from the syntax tree.

    Returns:
        ``(module path, line, first argument)`` for each call; the guard's own definition is not
        a call.
    """
    found: list[tuple[str, int, ast.expr]] = []
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function = node.func
            name = (
                function.id
                if isinstance(function, ast.Name)
                else function.attr
                if isinstance(function, ast.Attribute)
                else None
            )
            if name == "require_server_role":
                found.append((str(path.relative_to(SOURCE)), node.lineno, node.args[0]))
    return tuple(found)


def literal_keys(arg: ast.expr) -> set[str]:
    """
    Args:
        arg: The first argument of a call of the guard.

    Returns:
        The keys it can be, when they are all written out: a string, or a choice between two (a
        manager that drives a container's database asks for another function than one that drives
        an installed engine); empty when the key is computed.
    """
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return {arg.value}
    if isinstance(arg, ast.IfExp):
        branches = literal_keys(arg.body) | literal_keys(arg.orelse)
        both = isinstance(arg.body, ast.Constant) and isinstance(arg.orelse, ast.Constant)
        return branches if both else set()
    return set()


def keys_in_use() -> set[str]:
    """
    Returns:
        The keys a guard is called with: the literals at the call sites and the ones the command
        line refuses a hub by.
    """
    used = {key for _, _, arg in guard_calls() for key in literal_keys(arg)}
    return used | set(central.HUB_REFUSED_COMMANDS.values())


class TestTheTableIsClosed:
    def test_a_key_is_a_name_the_console_can_use_as_a_property(self) -> None:
        for key in FEATURES:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key

    def test_a_feature_knows_its_own_key(self) -> None:
        assert all(feature.key == key for key, feature in FEATURES.items())

    def test_what_a_feature_needs_comes_from_a_closed_list(self) -> None:
        assert {need.value for need in Need} == {
            "role",
            "systemd",
            "web_server",
            "docker",
            "root",
            "journal",
        }
        for feature in FEATURES.values():
            assert feature.needs, (
                f"{feature.key} needs nothing: it would not be a function of the table"
            )
            assert len(set(feature.needs)) == len(feature.needs), feature.key
            assert all(isinstance(need, Need) for need in feature.needs), feature.key

    def test_a_label_is_a_noun_phrase_a_refusal_can_start_with(self) -> None:
        for feature in FEATURES.values():
            assert feature.label[:1].isupper(), feature.key
            assert not feature.label.endswith("."), feature.key

    def test_an_unknown_key_is_an_error_not_a_pass(self) -> None:
        with pytest.raises(KeyError, match="no_such_function"):
            features.feature("no_such_function")


class TestEveryGuardAsksTheTable:
    def test_every_call_names_its_feature_with_a_literal(self) -> None:
        """A key computed at the call site could not be checked against the table."""
        computed = [
            (path, line)
            for path, line, arg in guard_calls()
            if not literal_keys(arg)
            # The command line's own door: it passes the key its table of commands names.
            and path != "central/__init__.py"
        ]
        assert computed == []

    def test_every_key_a_guard_is_called_with_is_in_the_table(self) -> None:
        unknown = keys_in_use() - set(FEATURES)
        assert not unknown, (
            f"require_server_role is called with keys the table does not have: {sorted(unknown)}. "
            "Add the function to noust/central/features.py with what it needs."
        )

    def test_a_guarded_function_has_a_guard_and_an_unguarded_one_does_not(self) -> None:
        used = keys_in_use()
        dead = sorted(
            key for key, feature in FEATURES.items() if feature.guarded and key not in used
        )
        assert not dead, (
            f"Nothing calls require_server_role for {dead}: remove them from the table, or mark "
            "them guarded=False when only the console decides by them."
        )
        wrongly_unguarded = sorted(
            key for key, feature in FEATURES.items() if not feature.guarded and key in used
        )
        assert not wrongly_unguarded, (
            f"{wrongly_unguarded} are guarded in the code: they are guarded=True."
        )

    def test_every_command_a_hub_refuses_names_a_feature(self) -> None:
        for command, key in central.HUB_REFUSED_COMMANDS.items():
            assert key in FEATURES, command

    def test_a_function_a_hub_refuses_needs_the_server_role(self) -> None:
        """What a hub refuses is what the table says needs the role: one reading of both."""
        for command, key in central.HUB_REFUSED_COMMANDS.items():
            assert Need.ROLE in FEATURES[key].needs, command


class TestWhatTheMachineNeedsIsWhatTheFunctionNeeds:
    """The keys are by what a function needs, not by "hub or not": Docker is not nginx."""

    def test_a_container_application_needs_docker_and_a_native_one_a_web_server(self) -> None:
        assert Need.DOCKER in FEATURES["container_deploy"].needs
        assert Need.WEB_SERVER not in FEATURES["container_deploy"].needs
        assert Need.WEB_SERVER in FEATURES["native_deploy"].needs
        assert Need.DOCKER not in FEATURES["native_deploy"].needs

    def test_an_engine_in_a_container_and_a_docker_volume_copy_need_only_docker_of_the_machine(
        self,
    ) -> None:
        for key in ("database_instances", "volume_backups"):
            assert set(FEATURES[key].needs) == {Need.ROLE, Need.DOCKER}, key

    def test_what_a_machine_decides_alone_does_not_ask_for_the_role(self) -> None:
        for key in ("system_clock", "power", "system_logs", "root_shell"):
            assert Need.ROLE not in FEATURES[key].needs, key
            assert not FEATURES[key].guarded, key

    def test_nothing_that_the_machine_decides_alone_is_refused_to_a_hub_by_the_guard(
        self, hub: None
    ) -> None:
        """A hub's own machine keeps its clock, its logs and its terminal."""
        for feature in FEATURES.values():
            if Need.ROLE not in feature.needs:
                central.require_server_role(feature.key)


class TestWhatIsAvailable:
    def test_a_server_with_everything_can_do_everything(self) -> None:
        for key in FEATURES:
            answer = features.availability(key, role="server", facts=EVERYTHING)
            assert answer.available, key
            assert answer.reason is None, key

    def test_a_hub_cannot_do_what_needs_the_server_role_even_on_a_machine_that_could(self) -> None:
        answer = features.availability("native_deploy", role="hub", facts=EVERYTHING)

        assert not answer.available
        assert answer.reason is not None
        assert answer.reason.code == "features.hub_role"
        assert answer.reason.params == {
            "feature": "Applications",
            "command": "noust config set central.role server",
        }

    def test_a_hub_keeps_what_its_own_machine_can_do(self) -> None:
        assert features.availability("system_clock", role="hub", facts=EVERYTHING).available
        assert features.availability("system_logs", role="hub", facts=EVERYTHING).available

    @pytest.mark.parametrize(
        ("key", "missing", "code"),
        [
            ("services", "systemd", "features.needs_systemd"),
            ("sites", "web_server", "features.needs_web_server"),
            ("software", "root", "features.needs_root"),
            ("system_logs", "journal", "features.needs_journal"),
            ("system_clock", "systemd", "features.needs_systemd"),
        ],
    )
    def test_a_missing_fact_is_said_with_its_own_sentence(
        self, key: str, missing: str, code: str
    ) -> None:
        facts = MachineFacts(
            **{
                **dict.fromkeys(("systemd", "web_server", "docker", "root", "journal"), True),
                missing: False,
            }
        )

        answer = features.availability(key, role="server", facts=facts)

        assert not answer.available
        assert answer.reason is not None
        assert answer.reason.code == code
        assert answer.reason.params == {}

    def test_docker_is_missing_in_the_words_of_the_door_to_docker(self) -> None:
        from noust.core.server_text import say

        problem = say("docker.engine.missing", command="noust setup init")
        facts = MachineFacts(
            systemd=True,
            web_server=True,
            docker=False,
            root=True,
            journal=True,
            docker_problem=problem,
        )

        answer = features.availability("container_deploy", role="server", facts=facts)

        assert not answer.available
        assert answer.reason == problem

    def test_docker_missing_without_the_doors_words_still_says_why(self) -> None:
        facts = MachineFacts(systemd=True, web_server=True, docker=False, root=True, journal=True)

        answer = features.availability("volume_backups", role="server", facts=facts)

        assert not answer.available
        assert answer.reason is not None
        assert answer.reason.code == "docker.engine.missing"

    def test_the_role_is_the_first_thing_said_then_the_machine(self) -> None:
        answer = features.availability("services", role="hub", facts=NAS_CONTAINER)
        assert answer.reason is not None
        assert answer.reason.code == "features.hub_role"

        answer = features.availability("services", role="server", facts=NAS_CONTAINER)
        assert answer.reason is not None
        assert answer.reason.code == "features.needs_systemd"

    def test_a_nas_container_can_do_nothing_that_needs_the_machine(self) -> None:
        for key, feature in FEATURES.items():
            if set(feature.needs) - {Need.ROLE}:
                assert not features.availability(
                    key, role="server", facts=NAS_CONTAINER
                ).available, key


class TestWhatIsPublished:
    def test_every_function_of_the_table_is_published_with_a_yes_or_a_reason(self) -> None:
        published = features.published(role="hub", facts=EVERYTHING)

        assert set(published) == set(FEATURES)
        for key, entry in published.items():
            assert set(entry) == {"available", "reason"}, key
            assert (entry["reason"] is None) == entry["available"], key

    def test_a_reason_is_a_sentence_with_a_code_and_its_english_beside_it(self) -> None:
        reason = features.published(role="hub", facts=EVERYTHING)["services"]["reason"]

        assert reason == {
            "code": "features.hub_role",
            "params": {
                "feature": "Services",
                "command": "noust config set central.role server",
            },
            "text": "Services: not available on this central, which is a hub",
            "hint": (
                "A hub (central.role = hub) manages other servers and deploys nothing itself. "
                "Pick the server in the console, or run the command against a node. On a machine "
                "that should also deploy, set 'noust config set central.role server'; a "
                "container cannot, it has no nginx or systemd."
            ),
        }

    def test_every_code_it_can_say_is_in_the_catalog(self) -> None:
        for role in ("server", "hub"):
            for facts in (EVERYTHING, NAS_CONTAINER):
                for entry in features.published(role=role, facts=facts).values():
                    if entry["reason"] is not None:
                        assert entry["reason"]["code"] in CATALOG


class TestTheGuardReadsTheTable:
    def test_a_hub_refuses_in_the_words_it_always_used(self, hub: None) -> None:
        with pytest.raises(central.RoleError) as caught:
            central.require_server_role("native_deploy")

        assert caught.value.message == "Applications: not available on this central, which is a hub"
        assert caught.value.details == (
            "A hub (central.role = hub) manages other servers and deploys nothing itself. "
            "Pick the server in the console, or run the command against a node. "
            "On a machine that should also deploy, set 'noust config set central.role "
            "server'; a container cannot, it has no nginx or systemd."
        )

    def test_a_typo_is_found_on_a_server_too(self) -> None:
        with pytest.raises(KeyError, match="aplications"):
            central.require_server_role("aplications")
