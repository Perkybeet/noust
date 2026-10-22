# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The verbs of ``noust deploy`` are in the generated reference and the manual.

``noust deploy`` is ``noust create`` under its old name, so the root group lists no ``deploy``
and resolves its verbs (``queue``, ``cancel``, the holds and freezes) by itself. A walker that only
asks the root for the commands it lists never reached them: the reference and the man page went
out without the commands an operator types most while a build runs.
"""

from __future__ import annotations

import click
import pytest

from noust.cli.app import DEPLOY_VERBS, cli, deploy_verbs, documented_commands
from scripts import generate_man, generate_reference


def test_the_verbs_the_root_dispatches_are_the_commands_of_the_group() -> None:
    """The tuple ``resolve_command`` reads and the group agree, in both directions."""
    assert set(DEPLOY_VERBS) == set(deploy_verbs().commands)


def test_the_root_documents_deploy_beside_the_commands_it_lists() -> None:
    context = click.Context(cli)

    names = [name for name, _command in documented_commands(cli, context)]

    assert "deploy" in names
    assert set(cli.list_commands(context)) <= set(names)
    assert names == sorted(names)


def test_documenting_deploy_does_not_make_it_a_listed_command() -> None:
    """``noust deploy -d ...`` stays ``create``: the listing is what ``--help`` prints."""
    context = click.Context(cli)

    assert "deploy" not in cli.list_commands(context)
    assert cli.get_command(context, "deploy") is cli.get_command(context, "create")


def test_a_group_below_the_root_documents_what_it_lists() -> None:
    group = cli.get_command(click.Context(cli), "app")
    assert isinstance(group, click.Group)
    context = click.Context(group)

    assert [name for name, _c in documented_commands(group, context)] == group.list_commands(
        context
    )


@pytest.mark.parametrize("verb", DEPLOY_VERBS)
def test_the_cli_reference_has_a_section_for_each_deploy_verb(verb: str) -> None:
    text = generate_reference.cli_page()

    assert f"### noust deploy {verb}\n" in text


def test_the_cli_reference_has_a_section_for_deploy_itself() -> None:
    text = generate_reference.cli_page()

    assert "### noust deploy\n" in text
    assert "(#noust-deploy-queue)" in text


@pytest.mark.parametrize("verb", DEPLOY_VERBS)
def test_the_manual_has_a_section_for_each_deploy_verb(verb: str) -> None:
    # roff spells a hyphen with an escape, so the heading is compared as the generator writes it.
    assert f".SS {generate_man.escape(f'noust deploy {verb}')}\n" in generate_man.build()


def test_the_reference_walk_reaches_the_deploy_verbs() -> None:
    paths = [path for path, _command in generate_reference.walk(cli, ("noust",))]

    assert ("noust", "deploy", "queue") in paths
    assert paths.count(("noust", "deploy")) == 1


def test_the_group_documented_is_the_group_that_runs() -> None:
    """What the reference shows for ``noust deploy`` is what the dispatch hands the verbs to."""
    assert dict(documented_commands(cli, click.Context(cli)))["deploy"] is deploy_verbs()
