# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An action on a Docker object is (owner, action, object), and it is judged in one place.

What belongs to an application is changed through the application: a container stopped from under
its health gate, an image removed from under its way back or a volume taken from under its data are
the damage a shortcut does, and the shortcut is what the notices of 2026 were about. So the gate
refuses them with the door to go through; what belongs to a database engine or to Noust itself is
refused the same way; what nobody made is the instance's, and is done only by whoever administers the
server. A person outside the teams that own an object is answered as if it did not exist.
"""

from __future__ import annotations

import pytest

from noust.core.docker.errors import DockerNotFoundError, DockerOwnedError
from noust.core.docker.judge import judge, visible
from noust.core.docker.models import NOBODY, Action, ObjectKind, Owner, OwnerKind

SHOP = Owner(OwnerKind.APP, "shop.example.com", "shop-example-com")
BLOG = Owner(OwnerKind.APP, "blog.example.com", "blog-example-com")
ENGINE = Owner(OwnerKind.DATABASE, "pg16", "noust-db-pg16")
CENTRAL = Owner(OwnerKind.NOUST, "central")
HELPER = Owner(OwnerKind.NOUST, "helper")

#: Every action that exists for an object.
ACTIONS = [
    (ObjectKind.CONTAINER, Action.START),
    (ObjectKind.CONTAINER, Action.STOP),
    (ObjectKind.CONTAINER, Action.RESTART),
    (ObjectKind.CONTAINER, Action.REMOVE),
    (ObjectKind.IMAGE, Action.REMOVE),
    (ObjectKind.VOLUME, Action.REMOVE),
    (ObjectKind.NETWORK, Action.REMOVE),
]


def see_only_the_shop(domain: str) -> bool:
    return domain == "shop.example.com"


def see_everything(domain: str) -> bool:
    return True


class TestWhoAdministersTheServer:
    @pytest.mark.parametrize(("kind", "action"), ACTIONS)
    def test_what_nobody_made_is_done_by_the_instance(
        self, kind: ObjectKind, action: Action
    ) -> None:
        judge(NOBODY, action, kind, "thing", restricted=False, sees_app=see_everything)

    @pytest.mark.parametrize(("kind", "action"), ACTIONS)
    def test_a_team_member_is_told_it_does_not_exist(
        self, kind: ObjectKind, action: Action
    ) -> None:
        with pytest.raises(DockerNotFoundError):
            judge(NOBODY, action, kind, "thing", restricted=True, sees_app=see_only_the_shop)

    @pytest.mark.parametrize("owner", [ENGINE, CENTRAL, HELPER])
    def test_the_engines_and_nousts_own_are_the_instances_to_see(self, owner: Owner) -> None:
        for kind, action in ACTIONS:
            with pytest.raises(DockerNotFoundError):
                judge(owner, action, kind, "thing", restricted=True, sees_app=see_everything)

    def test_what_is_seen_is_what_the_team_sees_and_what_nobody_made_is_never(self) -> None:
        assert visible(SHOP, restricted=True, sees_app=see_only_the_shop) is True
        assert visible(BLOG, restricted=True, sees_app=see_only_the_shop) is False
        assert visible(NOBODY, restricted=True, sees_app=see_only_the_shop) is False
        assert visible(ENGINE, restricted=True, sees_app=see_only_the_shop) is False
        assert visible(NOBODY, restricted=False, sees_app=see_only_the_shop) is True
        assert visible(BLOG, restricted=False, sees_app=see_only_the_shop) is True


class TestWhatBelongsToAnApplication:
    @pytest.mark.parametrize(("kind", "action"), ACTIONS)
    def test_it_is_changed_through_the_application_and_the_error_says_where(
        self, kind: ObjectKind, action: Action
    ) -> None:
        with pytest.raises(DockerOwnedError) as refused:
            judge(SHOP, action, kind, "shop-web-1", restricted=False, sees_app=see_everything)

        assert refused.value.owner_kind == "app"
        assert refused.value.subject == "shop.example.com"
        assert "shop.example.com" in refused.value.message
        assert "shop.example.com" in refused.value.details
        assert "noust " in refused.value.details

    def test_the_door_of_each_action_is_the_applications_own(self) -> None:
        def door(kind: ObjectKind, action: Action) -> str:
            with pytest.raises(DockerOwnedError) as refused:
                judge(SHOP, action, kind, "x", restricted=False, sees_app=see_everything)
            return refused.value.details

        assert "noust start shop.example.com" in door(ObjectKind.CONTAINER, Action.START)
        assert "noust stop shop.example.com" in door(ObjectKind.CONTAINER, Action.STOP)
        assert "noust restart shop.example.com" in door(ObjectKind.CONTAINER, Action.RESTART)
        assert "noust update shop.example.com" in door(ObjectKind.CONTAINER, Action.REMOVE)
        assert "noust releases list shop.example.com" in door(ObjectKind.IMAGE, Action.REMOVE)
        assert "noust delete shop.example.com --remove-volumes" in door(
            ObjectKind.VOLUME, Action.REMOVE
        )
        assert "noust delete shop.example.com" in door(ObjectKind.NETWORK, Action.REMOVE)

    def test_a_member_of_the_team_is_sent_to_the_application_and_another_is_told_nothing(
        self,
    ) -> None:
        with pytest.raises(DockerOwnedError):
            judge(
                SHOP,
                Action.STOP,
                ObjectKind.CONTAINER,
                "shop-web-1",
                restricted=True,
                sees_app=see_only_the_shop,
            )
        with pytest.raises(DockerNotFoundError):
            judge(
                BLOG,
                Action.STOP,
                ObjectKind.CONTAINER,
                "blog-web-1",
                restricted=True,
                sees_app=see_only_the_shop,
            )

    def test_the_two_refusals_a_stranger_can_get_are_one_answer(self) -> None:
        """What a team cannot see is answered as what does not exist: same words, same status."""
        with pytest.raises(DockerNotFoundError) as hidden:
            judge(
                BLOG,
                Action.STOP,
                ObjectKind.CONTAINER,
                "abc",
                restricted=True,
                sees_app=see_only_the_shop,
            )
        with pytest.raises(DockerNotFoundError) as missing:
            judge(
                NOBODY,
                Action.STOP,
                ObjectKind.CONTAINER,
                "abc",
                restricted=True,
                sees_app=see_only_the_shop,
            )

        assert (hidden.value.message, hidden.value.details) == (
            missing.value.message,
            missing.value.details,
        )


class TestWhatBelongsToADatabaseEngineOrToNoust:
    def test_an_engine_is_changed_with_its_own_commands(self) -> None:
        def door(kind: ObjectKind, action: Action) -> str:
            with pytest.raises(DockerOwnedError) as refused:
                judge(ENGINE, action, kind, "x", restricted=False, sees_app=see_everything)
            return refused.value.details

        assert "noust db instance start pg16" in door(ObjectKind.CONTAINER, Action.START)
        assert "noust db instance stop pg16" in door(ObjectKind.CONTAINER, Action.STOP)
        assert "noust db instance stop pg16" in door(ObjectKind.CONTAINER, Action.RESTART)
        assert "noust db instance remove pg16" in door(ObjectKind.VOLUME, Action.REMOVE)

    def test_the_container_noust_runs_in_is_not_stopped_from_inside_itself(self) -> None:
        with pytest.raises(DockerOwnedError) as refused:
            judge(
                CENTRAL,
                Action.STOP,
                ObjectKind.CONTAINER,
                "noust",
                restricted=False,
                sees_app=see_everything,
            )

        assert refused.value.owner_kind == "noust"
        assert "stops Noust" in refused.value.message

    def test_what_noust_started_for_a_copy_ends_with_the_copy(self) -> None:
        with pytest.raises(DockerOwnedError) as refused:
            judge(
                HELPER,
                Action.REMOVE,
                ObjectKind.CONTAINER,
                "tar",
                restricted=False,
                sees_app=see_everything,
            )

        assert "copy" in refused.value.message


class TestDockersOwn:
    def test_a_network_docker_makes_itself_is_not_removed(self) -> None:
        with pytest.raises(DockerOwnedError) as refused:
            judge(
                NOBODY,
                Action.REMOVE,
                ObjectKind.NETWORK,
                "bridge",
                restricted=False,
                sees_app=see_everything,
                builtin=True,
            )

        assert "Docker's own" in refused.value.message


def test_every_command_a_refusal_names_is_one_that_exists() -> None:
    """A refusal that sends a person to a command that is not there has helped nobody."""
    import click

    from noust.cli.app import cli

    def exists(words: list[str]) -> bool:
        context = click.Context(cli)
        current: click.Command = cli
        for word in words:
            if not isinstance(current, click.Group):
                return True  # the rest are arguments
            found = current.get_command(context, word)
            if found is None:
                return False
            current = found
            context = click.Context(found, parent=context)
        return True

    owners = (SHOP, ENGINE)
    seen = 0
    for owner in owners:
        for kind, action in ACTIONS:
            with pytest.raises(DockerOwnedError) as refused:
                judge(owner, action, kind, "x", restricted=False, sees_app=see_everything)
            for line in refused.value.details.replace(",", " ").replace(".", " ").splitlines():
                parts = line.split("noust ")
                for part in parts[1:]:
                    words = [w for w in part.split() if not w.startswith("-")][:4]
                    assert exists(words), (words, refused.value.details)
                    seen += 1
    assert seen >= 10
