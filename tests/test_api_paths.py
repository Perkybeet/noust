# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The path of a request to a platform's API stays on the endpoint its caller meant.

A client of GitHub, GitLab or Coolify fixes the host and builds the rest from names
somebody typed. ``..`` as a repository moved the request to another endpoint of the
same host, with the same credential (CodeQL ``py/partial-ssrf``). The names are judged
where they are spelled (:func:`noust.validators.source.github_repository_parts`) and the
path is judged again where every request passes (``GitHubClient.request``,
``RemoteClient``), so a caller that forgets one cannot send it.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import IntegrationError, ValidationError
from noust.deployers.importers.remote import RemoteClient
from noust.integrations.github.client import GitHubClient, repo_path
from noust.validators.source import github_repository_parts


class Sent:
    """urllib's opener that must not be reached, or records what was asked of it."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        self.urls.append(request.full_url)
        raise OSError("nothing is listening")


BAD_PATHS = [
    "/repos/octo/../../user",
    "/repos/octo/%2e%2e/user",
    "/repos/octo/hello\r\nHost: evil/branches",
    "//evil.example.com/repos",
    "repos/octo/hello",
    "/repos/octo\\hello",
]


class TestGitHubRepositoryParts:
    def test_an_owner_and_a_repository_are_returned_apart(self) -> None:
        assert github_repository_parts("octo-org/hello.world_2") == ("octo-org", "hello.world_2")

    @pytest.mark.parametrize(
        "full_name",
        [
            "octo/..",
            "octo/.",
            "octo/...",
            "octo/hello\n",
            "octo/hello\r\n",
            "octo/hello/branches",
            "octo",
            "/hello",
            "-octo/hello",
            "oc to/hello",
            "octo/hel lo",
            "octo/hello?x",
            "octo/hello#x",
            "github:octo/hello",
            "",
        ],
    )
    def test_what_is_not_a_repository_is_refused_with_the_field_that_was_wrong(
        self, full_name: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            github_repository_parts(full_name)

        assert caught.value.field == "repository"

    def test_the_path_of_a_repository_is_spelled_in_one_place(self) -> None:
        assert repo_path("octo/hello.world") == "/repos/octo/hello.world"

    def test_the_path_of_something_that_is_not_a_repository_is_not_spelled(self) -> None:
        with pytest.raises(ValidationError):
            repo_path("octo/..")


class TestTheGitHubClient:
    @pytest.mark.parametrize("path", BAD_PATHS)
    def test_a_path_that_leaves_its_endpoint_is_not_sent(self, path: str) -> None:
        opener = Sent()
        client = GitHubClient("https://api.example.test", opener=opener)

        with pytest.raises(IntegrationError, match="Refusing to request"):
            client.request("GET", path)

        assert opener.urls == []

    def test_a_plain_path_is_sent_to_the_host(self) -> None:
        opener = Sent()
        client = GitHubClient("https://api.example.test", opener=opener)

        with pytest.raises(IntegrationError, match="could not be reached"):
            client.request("GET", "/repos/octo/hello/branches", query={"page": 1})

        assert opener.urls == ["https://api.example.test/repos/octo/hello/branches?page=1"]


class TestTheRemoteClient:
    @pytest.mark.parametrize("path", BAD_PATHS)
    def test_a_path_that_leaves_its_endpoint_is_not_sent(self, path: str) -> None:
        opener = Sent()
        client = RemoteClient(
            "https://coolify.example.com", None, platform="Coolify", opener=opener
        )

        with pytest.raises(IntegrationError, match="Refusing to request"):
            client.get(path)

        assert opener.urls == []

    def test_a_plain_path_is_sent_to_the_host(self) -> None:
        opener = Sent()
        client = RemoteClient(
            "https://coolify.example.com", None, platform="Coolify", opener=opener
        )

        with pytest.raises(IntegrationError, match="could not be reached"):
            client.get("/api/v1/applications")

        assert opener.urls == ["https://coolify.example.com/api/v1/applications"]


class TestAnUploadIsNamedByItsIdentifierOnly:
    """The directory and the sidecar of an upload are built from the id, so the id is what is judged."""

    @pytest.mark.parametrize(
        "upload_id",
        ["..", "../etc", "a" * 31, "a" * 33, "A" * 32, ("a" * 32) + "\n", "x/" + "a" * 30, ""],
    )
    def test_the_tree_of_something_that_is_not_an_upload_is_not_named(self, upload_id: str) -> None:
        from noust.core.exceptions import SourceError
        from noust.validators.source import upload_tree

        with pytest.raises(SourceError):
            upload_tree(upload_id)

    def test_the_tree_of_an_upload_is_under_the_uploads_directory(self) -> None:
        from noust.core import paths
        from noust.validators.source import upload_tree

        assert upload_tree("0123456789abcdef" * 2) == paths.uploads_dir() / ("0123456789abcdef" * 2)

    def test_the_sidecar_of_something_that_is_not_an_upload_is_not_named(self) -> None:
        from noust.core.exceptions import SourceError
        from noust.managers.source_uploads import SourceUploads

        with pytest.raises(SourceError):
            SourceUploads._sidecar(object(), "../x")  # type: ignore[arg-type]


class TestAnInstanceIsNamedByItsNameOnly:
    """Every path of a database instance is built from its name in ``instance_layout.directory``."""

    @pytest.mark.parametrize("name", ["..", "../x", "a/b", "A", "x\n", "", "-x", "x" * 80])
    def test_the_directory_of_something_that_is_not_an_instance_is_not_named(
        self, name: str
    ) -> None:
        from noust.managers.database import instance_layout

        for builder in (
            instance_layout.directory,
            instance_layout.compose_path,
            instance_layout.env_path,
            instance_layout.config_path,
            instance_layout.tls_directory,
            instance_layout.init_directory,
        ):
            with pytest.raises(ValidationError):
                builder(name)

    def test_the_directory_of_an_instance_is_under_the_instances(self) -> None:
        from noust.managers.database import instance_layout

        assert instance_layout.directory("pg16") == instance_layout.instances_dir() / "pg16"
        assert instance_layout.compose_path("pg16").parent == instance_layout.directory("pg16")
