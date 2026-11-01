# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Git URLs over https, http and git:// with a port.

A GitHub Enterprise, a Gitea or a GitLab of one's own often serves https on a port other than
443 (``https://git.example.com:8443/team/app.git``), and the GitHub integration's own host
setting has accepted ``ghe.example.com:8443`` since 4.0, so the source it writes
(``noust.core.github_host.source_for``) was an address the validator refused. The port is read in
one place (``parse_url_remote``, the sibling of ``parse_ssh_remote``) and every reader of a
source's host follows it: the allowed origins, the credential an https repository is cloned with,
the page of the forge's webhook, and the matching of a push to a repository.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.ens.origins import OriginNotAllowed, check_origin, describe_origin
from noust.core.exceptions import SourceError, ValidationError
from noust.core.repositories import repository_identity, repository_key, repository_port
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.deployers.source_change import _clean_target
from noust.integrations.forges import statuses
from noust.integrations.webhook import forge_of
from noust.managers.source_credentials import GitCredentialManager
from noust.managers.source_manager import (
    _classify_source,
    git_auth_fix,
    redact_git_text,
    split_url_credentials,
    validate_git_remote_url,
)
from noust.validators.origins import check_allowed_source
from noust.validators.source import (
    get_repo_name,
    github_repository,
    is_git_url,
    parse_git_url,
    parse_url_remote,
    url_authority_problem,
    validate_git_url,
    validate_source,
)
from noust.validators.ssh import get_host_from_git_url, is_ssh_url

#: Addresses that name a port, and each is a repository.
WITH_A_PORT = [
    "https://git.example.com:8443/team/app.git",
    "https://git.example.com:8443/team/app",
    "http://gitea.internal.example:3000/team/app.git",
    "git://git.example.com:9418/team/app.git",
    "https://git.example.com:8443/group/sub/app.git",
    "https://git.example.com:1/team/app",
    "https://git.example.com:65535/team/app",
    "https://git.example.com:443/team/app.git",
]

#: Addresses on the host the allowed-origins tests allow, each with a port.
ON_THE_ALLOWED_HOST = [
    "https://git.example.com:8443/team/app.git",
    "https://git.example.com:8443/team/app",
    "git://git.example.com:9418/team/app.git",
    "https://git.example.com:443/team/app.git",
]

#: Text after the colon that is not a port Noust accepts, with the reason it is judged so.
NOT_A_PORT = [
    "0",
    "65536",
    "99999",
    "100000",
    "",
    "abc",
    "80x",
    "08443",
    "00443",
    "+80",
    "-80",
    " 80",
    "80 ",
    # Digits of other scripts: int() reads them, a port is not written with them.
    "".join(chr(code) for code in (0xFF18, 0xFF10, 0xFF18, 0xFF10)),
    "".join(chr(code) for code in (0x0668, 0x0660)),
    "443:443",
    "0x1F90",
    "8443.0",
]


class TestAccepted:
    @pytest.mark.parametrize("url", WITH_A_PORT)
    def test_is_a_git_url(self, url: str) -> None:
        assert is_git_url(url) is True
        assert validate_git_url(url) == (True, "")
        assert validate_source(url) == ("git", url)

    @pytest.mark.parametrize("url", WITH_A_PORT)
    def test_git_will_clone_it(self, url: str) -> None:
        assert validate_git_remote_url(url) == url

    @pytest.mark.parametrize("url", WITH_A_PORT)
    def test_is_not_an_ssh_url(self, url: str) -> None:
        assert is_ssh_url(url) is False

    def test_a_trailing_slash_and_a_git_suffix_still_work_with_a_port(self) -> None:
        assert validate_source("https://git.example.com:8443/team/app/")[0] == "git"
        assert validate_source("https://git.example.com:8443/team/app.git/")[0] == "git"


class TestParsing:
    def test_the_reader_names_scheme_host_port_and_path(self) -> None:
        remote = parse_url_remote("https://git.example.com:8443/team/app.git")
        assert remote is not None
        assert (remote.scheme, remote.host, remote.port, remote.path) == (
            "https",
            "git.example.com",
            8443,
            "team/app",
        )
        assert remote.authority == "git.example.com:8443"

    def test_without_a_port_there_is_none(self) -> None:
        remote = parse_url_remote("https://git.example.com/team/app")
        assert remote is not None
        assert remote.port is None and remote.authority == "git.example.com"

    def test_git_protocol_is_read_the_same_way(self) -> None:
        remote = parse_url_remote("git://git.example.com:9418/team/app.git")
        assert remote is not None
        assert (remote.scheme, remote.host, remote.port) == ("git", "git.example.com", 9418)

    def test_a_nested_group_is_kept_whole_in_the_path(self) -> None:
        remote = parse_url_remote("http://git.example.com:3000/group/sub/app.git")
        assert remote is not None
        assert remote.path == "group/sub/app"

    @pytest.mark.parametrize(
        "text",
        [
            "git@git.example.com:team/app.git",
            "ssh://git@git.example.com:2222/team/app.git",
            "/srv/app",
            "github:you/app",
            "ftp://git.example.com:21/team/app.git",
            "not a url",
            "",
        ],
    )
    def test_anything_but_http_https_and_git_is_not_one(self, text: str) -> None:
        assert parse_url_remote(text) is None

    def test_parse_git_url_carries_the_port_and_keeps_it_out_of_the_host(self) -> None:
        parsed = parse_git_url("https://git.example.com:8443/team/app.git")
        assert parsed["host"] == "git.example.com"
        assert parsed["port"] == 8443
        assert parsed["scheme"] == "https"
        assert (parsed["owner"], parsed["repo"], parsed["branch"]) == ("team", "app", None)

    def test_parse_git_url_with_a_port_and_a_branch(self) -> None:
        parsed = parse_git_url("git://git.example.com:9418/team/app.git#release")
        assert (parsed["host"], parsed["port"], parsed["branch"]) == (
            "git.example.com",
            9418,
            "release",
        )

    def test_parse_git_url_without_a_port_has_none(self) -> None:
        assert parse_git_url("https://git.example.com/team/app.git")["port"] is None

    def test_the_repository_name_does_not_come_from_the_port(self) -> None:
        assert get_repo_name("https://git.example.com:8443/team/shop.git") == "shop"
        assert get_repo_name("git://git.example.com:9418/team/shop") == "shop"

    def test_the_host_for_the_connection_test_is_the_name(self) -> None:
        assert get_host_from_git_url("https://git.example.com:8443/team/app.git") == (
            "git.example.com"
        )

    def test_the_port_does_not_make_another_repository(self) -> None:
        # A forge answers on its own address, and the same repository is also reached over ssh
        # on another port: what a webhook says is "owner/repo", never a port.
        a = repository_identity("https://git.example.com:8443/team/app.git")
        b = repository_identity("https://git.example.com/team/app")
        c = repository_identity("ssh://git@git.example.com:2222/team/app")
        assert a == b == c == "git.example.com/team/app"
        assert repository_key("git://git.example.com:9418/team/app.git") == (
            "git.example.com",
            "team/app",
        )


class TestRefused:
    @pytest.mark.parametrize("port", NOT_A_PORT)
    @pytest.mark.parametrize("scheme", ["https", "http", "git"])
    def test_a_text_that_is_not_a_port_is_not_a_repository(self, scheme: str, port: str) -> None:
        url = f"{scheme}://git.example.com:{port}/team/app.git"
        assert parse_url_remote(url) is None
        assert validate_git_url(url)[0] is False
        with pytest.raises(SourceError):
            validate_source(url)

    @pytest.mark.parametrize("port", NOT_A_PORT)
    def test_the_refusal_says_it_is_the_port(self, port: str) -> None:
        for url in (
            f"https://git.example.com:{port}/team/app.git",
            f"https://git.example.com:{port}/team/app",
        ):
            with pytest.raises(SourceError) as caught:
                validate_source(url)
            text = f"{caught.value.message} {caught.value.details}"
            assert "port" in text.lower(), text

    @pytest.mark.parametrize(
        "url",
        [
            "https://git.example.com:8443",
            "https://git.example.com:8443/",
            "https://:8443/team/app.git",
            "https://git.example.com::8443/team/app.git",
            "https://git.example.com:8443:/team/app.git",
            "https://[::1]:8443/team/app.git",
            "https://[2001:db8::1]/team/app.git",
            "https://user@git.example.com:8443/team/app.git",
            "https://user:token@git.example.com:8443/team/app.git",
            "https://git.example.com:8443@evil.example.com/team/app.git",
        ],
    )
    def test_nothing_else_around_a_port_is_a_source(self, url: str) -> None:
        # A credential is taken off the address before it is judged (see TestCredentials); the
        # validator itself never reads a user, and does not read an IPv6 literal either, with or
        # without a port.
        with pytest.raises(SourceError):
            validate_source(url)

    @pytest.mark.parametrize("port", ["abc", "0", "65536", "99999", "", "80:80"])
    def test_git_is_never_handed_one(self, port: str) -> None:
        with pytest.raises(SourceError):
            validate_git_remote_url(f"https://git.example.com:{port}/team/app.git")

    def test_a_port_does_not_change_what_kind_of_source_an_address_is(self) -> None:
        for url in ("https://files.example.com/app-1.0.tar.gz", "https://git.example.com/a/b.git"):
            with_port = url.replace(".example.com/", ".example.com:8443/")

            assert validate_source(with_port)[0] == validate_source(url)[0]


class TestCredentialsInTheAddress:
    TOKEN = "ghp_SuperSecretToken1234"

    def test_the_header_is_scoped_to_the_host_and_its_port(self) -> None:
        url, env = split_url_credentials(
            f"https://deployer:{self.TOKEN}@git.example.com:8443/team/app.git"
        )

        assert url == "https://git.example.com:8443/team/app.git"
        assert env["GIT_CONFIG_KEY_0"] == "http.https://git.example.com:8443/.extraHeader"
        basic = base64.b64encode(f"deployer:{self.TOKEN}".encode()).decode("ascii")
        assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {basic}"

    def test_a_source_with_a_credential_and_a_port_is_judged_without_the_credential(self) -> None:
        source = f"https://deployer:{self.TOKEN}@git.example.com:8443/team/app.git"

        assert _classify_source(source) == ("git", source)

    def test_a_host_written_as_the_user_is_still_the_host_after_the_at(self) -> None:
        # The credential of this address is the user "git.example.com" with the password 8443; the
        # host git is sent to is the one after the last @.
        source = "https://git.example.com:8443@evil.example.com/team/app.git"
        url, env = split_url_credentials(source)

        assert url == "https://evil.example.com/team/app.git"
        assert env["GIT_CONFIG_KEY_0"] == "http.https://evil.example.com/.extraHeader"

    def test_no_message_shows_the_token_of_an_address_with_a_port(self) -> None:
        shown = redact_git_text(
            f"fatal: unable to access 'https://deployer:{self.TOKEN}@git.example.com:8443/team/app.git'"
        )

        assert self.TOKEN not in shown
        assert "git.example.com:8443/team/app.git" in shown


class TestTheAllowedOrigins:
    # The port is not compared, and an entry cannot name one (the list says where code comes from
    # by host and path, as it did for ssh remotes with a port before this): a rule that names a host
    # applies to every port that host answers on.

    @pytest.mark.parametrize(
        "entry",
        [
            "git.example.com",
            "*.example.com",
            "git.example.com/team",
            "git.example.com/team/app",
            "https://git.example.com/team",
        ],
    )
    @pytest.mark.parametrize("url", ON_THE_ALLOWED_HOST)
    def test_a_rule_that_names_the_host_applies_with_a_port(self, entry: str, url: str) -> None:
        check_origin(url, [entry], kind="git")

    def test_the_origin_is_the_host_and_path_without_the_port(self) -> None:
        origin = describe_origin("https://git.example.com:8443/group/sub/app.git", "git")

        assert origin.host == "git.example.com"
        assert origin.path == ("group", "sub", "app")
        assert origin.display == "git.example.com/group/sub/app"

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example.org:8443/team/app.git",
            "https://git.example.com.evil.example.org:8443/team/app.git",
            "https://git.example.com:8443@evil.example.org/team/app.git",
            "https://evil.example.org:8443/git.example.com/app.git",
            "git://git.example.com.evil.example.org:9418/team/app.git",
        ],
    )
    def test_a_port_never_moves_a_source_into_a_host_that_is_not_allowed(self, url: str) -> None:
        with pytest.raises(OriginNotAllowed):
            check_origin(url, ["git.example.com"], kind="git")

    def test_an_organisation_rule_still_compares_whole_names_with_a_port(self) -> None:
        with pytest.raises(OriginNotAllowed):
            check_origin("https://git.example.com:8443/team-evil/app.git", ["git.example.com/team"])

    def test_the_command_a_refusal_suggests_never_names_a_port(self) -> None:
        with pytest.raises(OriginNotAllowed) as caught:
            check_origin("https://git.example.com:8443/team/app.git", ["github.com"], kind="git")

        details = caught.value.details or ""
        assert "git.example.com/team/app" in details
        assert "8443" not in details
        assert check_allowed_source("git.example.com/team/app") == "git.example.com/team/app"

    @pytest.mark.parametrize("entry", ["git.example.com:8443", "git.example.com:8443/team"])
    def test_an_entry_cannot_name_a_port(self, entry: str) -> None:
        with pytest.raises(ValidationError):
            check_allowed_source(entry)


class TestWhereAnAddressIsWrittenAgain:
    def test_the_ssh_address_that_is_suggested_does_not_borrow_the_https_port(self) -> None:
        # The https port says nothing about where ssh listens; the placeholder says to look.
        fix = git_auth_fix("https://git.example.com:8443/team/app.git")

        assert "ssh://git@git.example.com:<ssh-port>/team/app.git" in fix
        assert "git@git.example.com:team/app.git" not in fix
        assert "8443" not in fix

    def test_without_a_port_the_ssh_address_is_still_the_scp_one(self) -> None:
        assert "git@git.example.com:team/app.git" in git_auth_fix(
            "https://git.example.com/team/app.git"
        )

    def test_the_webhook_page_is_at_the_address_git_is_cloned_from(self) -> None:
        info = forge_of("https://gitea.example.com:8443/team/app.git")

        assert (info.forge, info.host, info.repository) == (
            "gitea",
            "gitea.example.com",
            "team/app",
        )
        assert info.settings_url == (
            "https://gitea.example.com:8443/team/app/settings/hooks/gitea/new"
        )

    def test_a_forge_over_plain_http_on_a_port_keeps_its_scheme(self) -> None:
        info = forge_of("http://gitea.internal.example:3000/team/app")

        assert info.settings_url == (
            "http://gitea.internal.example:3000/team/app/settings/hooks/gitea/new"
        )

    def test_the_webhook_page_of_an_ssh_source_is_the_hosts_default_one(self) -> None:
        # The ssh port is not the web one.
        info = forge_of("ssh://git@gitea.example.com:2222/team/app.git")

        assert info.settings_url == "https://gitea.example.com/team/app/settings/hooks/gitea/new"

    def test_the_webhook_page_of_a_source_with_a_credential_and_a_port_has_neither(self) -> None:
        info = forge_of("https://ci:s3cret@gitea.example.com:8443/team/app.git#dev")

        assert info.settings_url == (
            "https://gitea.example.com:8443/team/app/settings/hooks/gitea/new"
        )
        assert "s3cret" not in json.dumps(info.__dict__)

    def test_a_host_the_operator_declares_is_found_by_its_name_not_its_port(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
        )
        Config.reset_instance()
        try:
            Config().set("integrations.forge_hosts", ["git.example.com=gitea"])
            info = forge_of("https://git.example.com:8443/team/app.git")
        finally:
            Config.reset_instance()

        assert (info.forge, info.host) == ("gitea", "git.example.com")
        assert info.settings_url == "https://git.example.com:8443/team/app/settings/hooks/gitea/new"

    def test_a_malformed_port_in_a_stored_source_is_not_a_crash(self) -> None:
        info = forge_of("https://gitea.example.com:abc/team/app.git")

        assert info.settings_url is None or ":abc" not in info.settings_url


class TestWhereAForgeIsWrittenBack:
    def test_the_api_of_a_forge_on_a_port_is_asked_on_that_port(self) -> None:
        repo = statuses.repo_of("https://gitea.example.com:8443/team/app.git")

        assert repo is not None
        assert (repo.kind, repo.authority, repo.repository) == (
            "gitea",
            "gitea.example.com:8443",
            "team/app",
        )

    def test_a_port_that_is_not_one_is_not_a_crash_and_writes_nothing_back(self) -> None:
        assert statuses.repo_of("https://gitea.example.com:abc/team/app.git") is None


class TestChangingTheSource:
    def test_a_new_source_may_name_a_port(self) -> None:
        url = "https://git.example.com:8443/team/app.git"

        assert _clean_target(url) == ("git", url)

    def test_it_may_not_carry_a_credential_with_one(self) -> None:
        with pytest.raises(ValidationError):
            _clean_target("https://ci:s3cret@git.example.com:8443/team/app.git")

    def test_a_port_that_is_not_one_is_refused_as_such(self) -> None:
        with pytest.raises(SourceError) as caught:
            _clean_target("https://git.example.com:70000/team/app.git")

        assert "port" in caught.value.message


class TestWhichRepositoryAPushIs:
    #: How a clone URL spells the host of a GitHub Enterprise served on 8443.
    ON_8443 = ("ghe.example.com:8443", "ghe.example.com")

    def test_a_source_on_the_hosts_port_is_its_repository(self) -> None:
        assert (
            github_repository("https://ghe.example.com:8443/team/app.git", hosts=self.ON_8443)
            == "team/app"
        )

    def test_a_payload_that_names_the_host_without_its_port_is_too(self) -> None:
        assert (
            github_repository("https://ghe.example.com/team/app.git", hosts=self.ON_8443)
            == "team/app"
        )

    def test_another_port_of_the_same_host_is_another_service(self) -> None:
        assert (
            github_repository("https://ghe.example.com:9999/team/app.git", hosts=self.ON_8443)
            is None
        )
        assert (
            github_repository(
                "https://ghe.example.com:8443/team/app.git", hosts=("ghe.example.com",)
            )
            is None
        )

    def test_the_default_port_written_out_is_the_same_service_as_none(self) -> None:
        assert (
            github_repository(
                "https://ghe.example.com:443/team/app.git", hosts=("ghe.example.com",)
            )
            == "team/app"
        )

    def test_an_ssh_port_is_not_the_https_port(self) -> None:
        assert (
            github_repository("ssh://git@ghe.example.com:2222/team/app.git", hosts=self.ON_8443)
            == "team/app"
        )
        assert (
            github_repository(
                "ssh://git@ghe.example.com:2222/team/app.git", hosts=("ghe.example.com",)
            )
            == "team/app"
        )

    @pytest.mark.parametrize("port", ["abc", "99999", "-1", "80:80"])
    def test_a_port_that_is_not_one_matches_nothing_and_does_not_crash(self, port: str) -> None:
        assert (
            github_repository(f"https://ghe.example.com:{port}/team/app.git", hosts=self.ON_8443)
            is None
        )

    def test_github_com_is_not_a_host_on_a_port(self) -> None:
        assert github_repository("https://github.com:8443/team/app.git") is None


class TestACredentialIsForOneOrigin:
    """
    The credential kept for a repository is found by its host and path, and a port is another
    service: the one on 443 and the one a tenant of the same machine runs on 6666 are not the
    same, and the token of the first must never be sent to the second.
    """

    DEFAULT_DOMAIN = "shop.example.com"
    PORTED_DOMAIN = "forge.example.com"
    ON_443 = "https://git.example.com/team/app.git"
    ON_8443 = "https://git.example.com:8443/team/app.git"
    TOKEN = "glpat-NotForOtherPorts"

    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[NoustStore]:
        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "state" / "noust.db")
        instance.create_app(
            App(domain=self.DEFAULT_DOMAIN, app_type="nodejs", source=self.ON_443, branch="main")
        )
        instance.create_app(
            App(domain=self.PORTED_DOMAIN, app_type="nodejs", source=self.ON_8443, branch="main")
        )
        try:
            yield instance
        finally:
            instance.close()
            NoustStore.reset_instance()

    @pytest.fixture
    def credentials(self, store: NoustStore, tmp_path: Path) -> GitCredentialManager:
        return GitCredentialManager(
            store=store, secrets=SecretStore(root=tmp_path / "state" / "secrets")
        )

    def test_the_port_of_a_source_is_what_the_credential_is_kept_for(self) -> None:
        assert repository_port(self.ON_443) is None
        assert repository_port("https://git.example.com:443/team/app") is None
        assert repository_port("http://git.example.com:80/team/app") is None
        assert repository_port(self.ON_8443) == 8443
        assert repository_port("https://git.example.com:80/team/app") == 80
        assert repository_port("git://git.example.com:9999/team/app.git") == 9999
        assert repository_port("git://git.example.com:9418/team/app.git") is None
        assert repository_port("git@git.example.com:team/app.git") is None
        assert repository_port("ssh://git@git.example.com:2222/team/app.git") is None
        assert repository_port("/srv/app") is None

    def test_a_credential_of_the_default_port_is_not_sent_to_another_port(
        self, credentials: GitCredentialManager
    ) -> None:
        credentials.put(self.DEFAULT_DOMAIN, "ci", self.TOKEN)

        assert credentials.credential_for(self.ON_443) == ("ci", self.TOKEN)
        assert credentials.credential_for("https://git.example.com:443/team/app.git") == (
            "ci",
            self.TOKEN,
        )
        assert credentials.credential_for(self.ON_8443) is None
        assert credentials.auth_for(self.ON_8443) == {}
        assert credentials.auth_for(self.ON_443)["GIT_CONFIG_KEY_0"] == (
            "http.https://git.example.com/.extraHeader"
        )

    def test_a_credential_of_a_port_is_not_sent_to_the_default_one_or_to_another_port(
        self, credentials: GitCredentialManager
    ) -> None:
        credentials.put(self.PORTED_DOMAIN, "ci", self.TOKEN)

        assert credentials.credential_for(self.ON_8443) == ("ci", self.TOKEN)
        assert credentials.credential_for(self.ON_443) is None
        assert credentials.credential_for("https://git.example.com:6666/team/app.git") is None
        assert credentials.auth_for(self.ON_8443)["GIT_CONFIG_KEY_0"] == (
            "http.https://git.example.com:8443/.extraHeader"
        )

    def test_the_view_says_it_is_configured_only_for_the_source_it_applies_to(
        self, credentials: GitCredentialManager
    ) -> None:
        credentials.put(self.DEFAULT_DOMAIN, "ci", self.TOKEN)

        assert credentials.view(self.DEFAULT_DOMAIN).configured is True
        # Same repository on another port: the stored one is not what its clone is given.
        assert credentials.view(self.PORTED_DOMAIN).configured is False

    def test_a_credential_stored_before_ports_were_possible_is_the_default_ports(
        self, credentials: GitCredentialManager
    ) -> None:
        credentials.secrets.write_json(
            "git-credentials/git.example.com/team/app", {"username": "old", "secret": self.TOKEN}
        )

        assert credentials.credential_for(self.ON_443) == ("old", self.TOKEN)
        assert credentials.credential_for(self.ON_8443) is None

    def test_the_audit_says_which_port(
        self, credentials: GitCredentialManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[dict[str, object]] = []
        monkeypatch.setattr(
            "noust.managers.source_credentials.audit.record",
            lambda _event, **kwargs: seen.append(kwargs),
        )

        credentials.put(self.PORTED_DOMAIN, "ci", self.TOKEN)

        assert seen[0]["details"] == {
            "repository": "git.example.com/team/app",
            "username": "ci",
            "port": 8443,
        }
        assert self.TOKEN not in str(seen)


class TestTheLastLookBeforeGit:
    @pytest.mark.parametrize(
        "url",
        [
            "https://git.example.com/team/app.git",
            "https://git.example.com:8443/team/app.git",
            "git://git.example.com:9418/team/app.git",
            "ssh://git@git.example.com:2222/team/app.git",
            "https://[::1]:8443/team/app.git",
        ],
    )
    def test_a_usable_host_and_port_have_no_problem(self, url: str) -> None:
        assert url_authority_problem(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://git.example.com:0/team/app.git",
            "https://git.example.com:65536/team/app.git",
            "https://git.example.com:abc/team/app.git",
            "https://git.example.com:/team/app.git",
            "https://git.example.com:80:80/team/app.git",
            "https://git.example.com:[/team/app.git",
            "ssh://git@git.example.com:0/team/app.git",
        ],
    )
    def test_anything_else_is_said_not_raised(self, url: str) -> None:
        assert "port" in (url_authority_problem(url) or "").lower()
