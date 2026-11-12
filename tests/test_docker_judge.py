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
from noust.core.docker.judge import Can, can, judge, visible
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


class TestWhatARowOffersWithoutAskingTheRefusal:
    """The console draws a button only for what would be done, so it asks the same judgement first."""

    @staticmethod
    def what(owner: Owner, kind: ObjectKind, **kw: bool) -> Can:
        return can(owner, kind, "thing", restricted=False, sees_app=see_everything, **kw)

    def test_what_nobody_made_can_be_started_stopped_restarted_and_removed(self) -> None:
        offered = self.what(NOBODY, ObjectKind.CONTAINER)

        assert (offered.start, offered.stop, offered.restart, offered.remove) == (True,) * 4
        assert offered.via == ""

    @pytest.mark.parametrize("owner", [SHOP, ENGINE, CENTRAL, HELPER])
    def test_what_is_anothers_can_be_done_to_nothing_and_says_where_it_is_done(
        self, owner: Owner
    ) -> None:
        offered = self.what(owner, ObjectKind.CONTAINER)

        assert (offered.start, offered.stop, offered.restart, offered.remove) == (False,) * 4
        assert offered.via

    def test_the_door_is_the_sentence_the_refusal_would_have_carried(self) -> None:
        offered = self.what(SHOP, ObjectKind.CONTAINER)

        with pytest.raises(DockerOwnedError) as refused:
            judge(
                SHOP,
                Action.RESTART,
                ObjectKind.CONTAINER,
                "thing",
                restricted=False,
                sees_app=see_everything,
            )
        assert offered.via == refused.value.details

    @pytest.mark.parametrize("kind", [ObjectKind.IMAGE, ObjectKind.VOLUME, ObjectKind.NETWORK])
    def test_only_a_container_is_started_stopped_or_restarted(self, kind: ObjectKind) -> None:
        offered = self.what(NOBODY, kind)

        assert (offered.start, offered.stop, offered.restart) == (False, False, False)
        assert offered.remove is True

    def test_a_network_docker_makes_is_not_offered_for_removal(self) -> None:
        offered = self.what(NOBODY, ObjectKind.NETWORK, builtin=True)

        assert offered.remove is False and offered.via

    def test_what_a_team_may_not_see_is_offered_nothing_and_says_nothing_of_who_has_it(
        self,
    ) -> None:
        offered = can(
            BLOG,
            ObjectKind.CONTAINER,
            "thing",
            restricted=True,
            sees_app=see_only_the_shop,
        )

        assert not (offered.start or offered.stop or offered.restart or offered.remove)
        assert offered.via == ""

    def test_a_row_is_offered_exactly_what_its_action_would_be_let_through(self) -> None:
        for owner in (NOBODY, SHOP, ENGINE, CENTRAL, HELPER):
            for kind, action in ACTIONS:
                allowed = True
                try:
                    judge(owner, action, kind, "thing", restricted=False, sees_app=see_everything)
                except DockerOwnedError:
                    allowed = False
                offered = self.what(owner, kind)
                assert getattr(offered, action.value) is allowed, (owner, kind, action)

    def test_what_a_person_cannot_change_at_all_is_everything_off(self) -> None:
        assert not Can.none().remove and Can.none().via == ""


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
