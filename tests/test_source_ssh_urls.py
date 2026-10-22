# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Git URLs over SSH with any user and any port (checklist A-10).

``git@host:path`` was the only SSH form Noust accepted. A self-hosted forge
(Gitea, Gogs, a company GitLab) is reached as ``gitea@host:path`` or on another
port with ``ssh://git@host:2222/path``, and each of them is one repository to
every place that reads a source: the validator, the deploy keys, the webhook
matching and the connection test.
"""

from __future__ import annotations

import pytest

from noust.core.exceptions import SourceError
from noust.core.repositories import repository_identity
from noust.managers.source_manager import validate_git_remote_url
from noust.validators.source import (
    get_repo_name,
    is_git_url,
    parse_git_url,
    parse_ssh_remote,
    validate_git_url,
    validate_source,
)
from noust.validators.ssh import get_host_from_git_url, is_ssh_url

SCP_FORMS = [
    "git@github.com:user/repo.git",
    "gitea@git.example.com:team/app.git",
    "forgejo@code.example.org:team/app",
    "deploy.bot@git.corp.example:group/sub/app.git",
    "svc+ci@git.example.com:~/repo.git",
]

SSH_FORMS = [
    "ssh://git@git.example.com/team/app.git",
    "ssh://git@git.example.com:2222/team/app.git",
    "ssh://gitea@git.example.com:2222/team/app",
    "ssh://git.example.com/team/app.git",
    "ssh://deploy@10.0.0.5:22/srv/git/app.git",
]


class TestAccepted:
    @pytest.mark.parametrize("url", SCP_FORMS + SSH_FORMS)
    def test_is_a_git_url(self, url: str) -> None:
        assert is_git_url(url) is True
        assert validate_git_url(url) == (True, "")
        assert validate_source(url) == ("git", url)

    @pytest.mark.parametrize("url", SCP_FORMS + SSH_FORMS)
    def test_is_an_ssh_url(self, url: str) -> None:
        assert is_ssh_url(url) is True

    @pytest.mark.parametrize("url", SCP_FORMS + SSH_FORMS)
    def test_git_will_clone_it(self, url: str) -> None:
        assert validate_git_remote_url(url) == url

    @pytest.mark.parametrize(
        "url", ["https://github.com/user/repo.git", "git://github.com/u/r.git", "/srv/app"]
    )
    def test_other_forms_are_not_ssh(self, url: str) -> None:
        assert is_ssh_url(url) is False


class TestParsing:
    def test_scp_form_names_user_and_no_port(self) -> None:
        remote = parse_ssh_remote("gitea@git.example.com:team/app.git")
        assert remote is not None
        assert (remote.user, remote.host, remote.port, remote.path) == (
            "gitea",
            "git.example.com",
            None,
            "team/app",
        )

    def test_ssh_url_names_user_and_port(self) -> None:
        remote = parse_ssh_remote("ssh://git@git.example.com:2222/team/app.git")
        assert remote is not None
        assert (remote.user, remote.host, remote.port, remote.path) == (
            "git",
            "git.example.com",
            2222,
            "team/app",
        )

    def test_ssh_url_without_user(self) -> None:
        remote = parse_ssh_remote("ssh://git.example.com/team/app.git")
        assert remote is not None
        assert remote.user is None and remote.port is None

    def test_parse_git_url_carries_user_port_and_scheme(self) -> None:
        parsed = parse_git_url("ssh://gitea@git.example.com:2222/team/app.git#release")
        assert parsed["host"] == "git.example.com"
        assert parsed["owner"] == "team"
        assert parsed["repo"] == "app"
        assert parsed["branch"] == "release"
        assert parsed["user"] == "gitea"
        assert parsed["port"] == 2222
        assert parsed["scheme"] == "ssh"

    def test_a_nested_group_keeps_the_last_part_as_the_repository(self) -> None:
        parsed = parse_git_url("git@gitlab.example.com:group/sub/app.git")
        assert parsed["owner"] == "group/sub"
        assert parsed["repo"] == "app"

    def test_the_repository_name_comes_from_any_ssh_form(self) -> None:
        assert get_repo_name("gitea@git.example.com:team/shop.git") == "shop"
        assert get_repo_name("ssh://git@git.example.com:2222/team/shop") == "shop"

    def test_host_for_the_connection_test(self) -> None:
        assert get_host_from_git_url("gitea@git.example.com:team/app.git") == "git.example.com"
        assert get_host_from_git_url("ssh://git@git.example.com:2222/a/b") == "git.example.com"

    def test_all_spellings_are_one_repository(self) -> None:
        a = repository_identity("gitea@git.example.com:team/app.git")
        b = repository_identity("ssh://gitea@git.example.com:2222/team/app")
        c = repository_identity("https://git.example.com/team/app")
        assert a == b == c == "git.example.com/team/app"


class TestRefused:
    @pytest.mark.parametrize(
        "url",
        [
            "ssh://git@git.example.com:0/team/app.git",
            "ssh://git@git.example.com:70000/team/app.git",
            "ssh://git@git.example.com:22x/team/app.git",
            "ssh://-oProxyCommand=x@git.example.com/team/app.git",
            "-oProxyCommand=x@git.example.com:team/app.git",
            "ssh://git@-oProxyCommand=x/team/app.git",
            "git@git.example.com:team/app.git;rm -rf /",
            "ssh://git@git.example.com:2222",
            "ssh://git@git.example.com:2222/",
        ],
    )
    def test_is_not_a_git_url(self, url: str) -> None:
        assert parse_ssh_remote(url) is None
        with pytest.raises(SourceError):
            validate_source(url)

    def test_a_host_and_a_port_alone_are_not_a_repository(self) -> None:
        # Without a user, host:path is also "localhost:8080": too loose to read as a repository.
        assert is_git_url("localhost:8080") is False
        assert parse_ssh_remote("localhost:8080") is None

    def test_a_host_that_starts_with_a_dash_never_reaches_ssh(self) -> None:
        with pytest.raises(SourceError):
            validate_git_remote_url("ssh://git@-oProxyCommand=touch%20x/team/app.git")


class TestHostKeyOnAnotherPort:
    def test_the_fix_names_the_host_the_way_known_hosts_pins_it(self) -> None:
        from noust.core.runner import CommandResult
        from noust.managers.source_manager import host_key_error

        result = CommandResult(
            argv=("git", "clone"), exit_code=128, stderr="Host key verification failed."
        )
        on_port = host_key_error("ssh://git@git.example.com:2222/team/app.git", result)
        assert "[git.example.com]:2222" in (on_port.details or "")
        default = host_key_error("git@git.example.com:team/app.git", result)
        assert "[git.example.com]" not in (default.details or "")
        assert "git.example.com" in (default.details or "")
