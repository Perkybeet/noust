# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bringing applications over from Coolify and Dokploy through their API (checklist A-17).

Both platforms keep an application's configuration in their own database, so
they are read with a token over HTTPS. Nothing is written to them, the token is
never an argument or stored, and what the platform says becomes the proposal
every other importer answers with. The servers are faked at urllib's seam with
payloads shaped like the ones their documented APIs return.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from noust.core.exceptions import IntegrationError, ValidationError
from noust.deployers.app_export import plan_import, proposal_document
from noust.deployers.importers import coolify, dokploy
from noust.deployers.importers.base import Proposal, is_plain_command
from noust.deployers.importers.remote import RemoteClient, check_base_url, read_token

TOKEN = "1|abcDEF123456789"


class FakeServer:
    """urllib's opener, answering from a table and remembering what it was asked."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        self.requests.append(request)
        path = request.full_url.split("://", 1)[1].split("/", 1)[1]
        key = "/" + path
        if key not in self.answers:
            raise HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]
        answer = self.answers[key]
        if isinstance(answer, HTTPError):
            raise answer
        body = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
        return _Response(body)


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]


# --------------------------------------------------------------------------- the client


class TestTheClient:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://coolify.example.com",
            "coolify.example.com",
            "https://",
            "https://user:pass@coolify.example.com",
            "http://coolify.example.com",
        ],
    )
    def test_refuses_an_address_that_is_not_a_safe_one(self, url: str) -> None:
        with pytest.raises(ValidationError) as caught:
            check_base_url(url, "Coolify")
        assert caught.value.field == "url"

    def test_accepts_https_and_plain_http_on_loopback_only(self) -> None:
        assert check_base_url("https://coolify.example.com/", "Coolify") == (
            "https://coolify.example.com"
        )
        assert check_base_url("http://127.0.0.1:8000", "Coolify") == "http://127.0.0.1:8000"
        assert check_base_url("http://localhost", "Coolify") == "http://localhost"

    @pytest.mark.parametrize("value", ["", "  ", "a b", "tok\nen", "to\x00ken"])
    def test_a_token_is_one_word(self, value: str) -> None:
        with pytest.raises(ValidationError):
            read_token(value, source="the test")

    def test_the_token_goes_in_the_header_the_platform_wants(self) -> None:
        server = FakeServer({"/api/v1/applications": []})
        coolify.connect("https://coolify.example.com", TOKEN, opener=server)
        client = coolify.connect("https://coolify.example.com", TOKEN, opener=server)

        client.get("/api/v1/applications")

        request = server.requests[0]
        assert request.get_header("Authorization") == f"Bearer {TOKEN}"
        assert request.get_method() == "GET"

    def test_dokploy_uses_its_own_header(self) -> None:
        server = FakeServer({"/api/project.all": []})
        dokploy.connect("https://dokploy.example.com", TOKEN, opener=server).get("/api/project.all")
        assert server.requests[0].get_header("X-api-key") == TOKEN

    def test_the_one_ssrf_guard_is_asked_before_anything_is_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core import notifier

        asked: list[tuple[str, str]] = []

        def refuse(url: str, purpose: str = "server") -> None:
            asked.append((url, purpose))
            raise ValueError("Refusing to contact 'internal': resolves to 10.0.0.5")

        monkeypatch.setattr(notifier, "require_public_destination", refuse)
        client = RemoteClient(
            "https://internal.example.com", ("Authorization", "Bearer x"), platform="Coolify"
        )

        with pytest.raises(IntegrationError) as caught:
            client.get("/api/v1/applications")

        assert asked == [("https://internal.example.com/api/v1/applications", "Coolify server")]
        assert "allow_private_hosts" in caught.value.details

    @pytest.mark.parametrize(
        ("status", "fragment"),
        [(401, "token was not accepted"), (403, "may not read"), (404, "no such resource")],
    )
    def test_a_refusal_says_what_to_do(self, status: int, fragment: str) -> None:
        url = "https://c.example.com/api/v1/applications"
        server = FakeServer(
            {"/api/v1/applications": HTTPError(url, status, "no", {}, io.BytesIO(b"{}"))}  # type: ignore[arg-type]
        )
        client = coolify.connect("https://c.example.com", TOKEN, opener=server)

        with pytest.raises(IntegrationError) as caught:
            client.get("/api/v1/applications")

        assert fragment in caught.value.details
        assert TOKEN not in caught.value.message + caught.value.details

    def test_a_redirect_is_not_followed(self) -> None:
        url = "https://c.example.com/api/v1/applications"
        server = FakeServer(
            {
                "/api/v1/applications": HTTPError(
                    url, 302, "redirected to http://evil", {}, io.BytesIO(b"")
                )
            }  # type: ignore[arg-type]
        )
        client = coolify.connect("https://c.example.com", TOKEN, opener=server)
        with pytest.raises(IntegrationError) as caught:
            client.get("/api/v1/applications")
        assert "redirected" in caught.value.details

    def test_something_that_is_not_json_is_not_that_platform(self) -> None:
        server = FakeServer({"/api/v1/applications": b"<html>login</html>"})
        client = coolify.connect("https://c.example.com", TOKEN, opener=server)
        with pytest.raises(IntegrationError) as caught:
            client.get("/api/v1/applications")
        assert "not JSON" in caught.value.message

    def test_a_path_that_leaves_the_api_is_refused(self) -> None:
        client = coolify.connect("https://c.example.com", TOKEN, opener=FakeServer({}))
        for path in ("//evil.example.com/x", "api/v1"):
            with pytest.raises(IntegrationError):
                client.get(path)


# --------------------------------------------------------------------------- Coolify

COOLIFY_APP = {
    "uuid": "abc123",
    "name": "shop",
    "fqdn": "https://shop.example.com,https://www.shop.example.com",
    "git_repository": "acme/shop",
    "git_branch": "main",
    "git_commit_sha": "HEAD",
    "build_pack": "nixpacks",
    "install_command": "pnpm install --frozen-lockfile",
    "build_command": "pnpm build",
    "start_command": "pnpm start",
    "base_directory": "/apps/web",
    "publish_directory": "/dist",
    "ports_exposes": "3000",
    "health_check_enabled": True,
    "health_check_path": "/healthz",
    "health_check_timeout": 7,
    "limits_memory": "512m",
    "limits_cpus": "1.5",
    "watch_paths": "apps/web/**\n!apps/web/docs/**\n",
    "environment_name": "production",
}

COOLIFY_ENVS = [
    {"key": "DATABASE_URL", "value": "postgres://user:pw@db/shop", "is_preview": False},
    {"key": "NODE_ENV", "value": "production", "is_preview": False},
    {"key": "STRIPE_SECRET", "value": None, "is_shown_once": True},
    {"key": "NODE_ENV", "value": "development", "is_preview": True},
    {"key": "BUILD_ONLY", "value": "1", "is_buildtime": True, "is_runtime": False},
]


def coolify_server(app: dict[str, Any] | None = None, **extra: Any) -> FakeServer:
    answers = {
        "/api/v1/applications": [{**COOLIFY_APP, **(app or {})}],
        f"/api/v1/applications/{COOLIFY_APP['uuid']}": {**COOLIFY_APP, **(app or {})},
        f"/api/v1/applications/{COOLIFY_APP['uuid']}/envs": COOLIFY_ENVS,
        f"/api/v1/applications/{COOLIFY_APP['uuid']}/storages": [{"mount_path": "/app/uploads"}],
        f"/api/v1/applications/{COOLIFY_APP['uuid']}/scheduled-tasks": [
            {
                "name": "nightly",
                "frequency": "daily",
                "command": "node cleanup.js",
                "enabled": True,
            },
            {"name": "odd", "frequency": "whenever", "command": "x"},
            {"name": "off", "frequency": "hourly", "command": "x", "enabled": False},
        ],
        **extra,
    }
    return FakeServer(answers)


def read_coolify(app: dict[str, Any] | None = None, **extra: Any) -> Proposal:
    server = coolify_server(app, **extra)
    client = coolify.connect("https://coolify.example.com", TOKEN, opener=server)
    return coolify.read_application(client, "abc123")


class TestCoolify:
    def test_the_applications_are_listed(self) -> None:
        client = coolify.connect("https://c.example.com", TOKEN, opener=coolify_server())

        (found,) = coolify.list_applications(client)

        assert (found.ref, found.name, found.build) == ("abc123", "shop", "nixpacks")
        assert found.domains == ("shop.example.com", "www.shop.example.com")
        assert found.source == "acme/shop"
        assert found.project == "production"

    def test_the_repository_and_where_the_project_lives(self) -> None:
        proposal = read_coolify()

        assert proposal.platform == "coolify"
        assert proposal.name == "shop"
        assert proposal.source == "github:acme/shop"
        assert proposal.branch == "main"
        assert proposal.base_directory == "apps/web"
        assert proposal.output_directory == "dist"
        assert proposal.watch_paths == ["apps/web/**", "!apps/web/docs/**"]
        assert any("GitHub App source" in warning for warning in proposal.warnings)

    def test_a_full_url_is_used_without_its_credentials(self) -> None:
        proposal = read_coolify(
            {"git_full_url": "https://oauth2:secret@git.example.com:8443/acme/shop.git"}
        )
        assert proposal.source == "https://git.example.com:8443/acme/shop.git"
        assert "secret" not in json.dumps(proposal.warnings)

    def test_domains_port_health_and_limits(self) -> None:
        proposal = read_coolify()

        assert proposal.domains == ["shop.example.com", "www.shop.example.com"]
        assert proposal.port == 3000
        assert (proposal.health_path, proposal.health_timeout) == ("/healthz", 7)
        assert proposal.memory_max_mb == 512
        assert proposal.cpu_quota_percent == 150

    def test_several_ports_pick_the_first_and_say_so(self) -> None:
        proposal = read_coolify({"ports_exposes": "8080, 9090"})
        assert proposal.port == 8080
        assert any("8080" in w and "9090" in w for w in proposal.warnings)

    def test_variables_keep_their_defaults_except_secrets_and_what_was_hidden(self) -> None:
        proposal = read_coolify()
        env = {variable.name: variable for variable in proposal.env}

        assert env["NODE_ENV"].value == "production"
        assert "BUILD_ONLY" in env
        # The preview copy is not a variable of the application.
        assert [v.name for v in proposal.env].count("NODE_ENV") == 1
        # A name that says secret never keeps a value the file had, and one the
        # token could not read must be given again.
        assert env["STRIPE_SECRET"].value is None and env["STRIPE_SECRET"].required
        assert env["STRIPE_SECRET"].secret
        assert any("no value the token could read" in w for w in proposal.warnings)
        assert any("BUILD_ONLY" in w and "build-time" in w for w in proposal.warnings)

    def test_scheduled_tasks_become_cron_jobs_where_they_can(self) -> None:
        proposal = read_coolify()

        assert [(job.name, job.schedule, job.command) for job in proposal.cron] == [
            ("nightly", "0 0 * * *", "node cleanup.js")
        ]
        assert any("'odd'" in w and "cron" in w for w in proposal.warnings)

    def test_persistent_storage_is_said_not_guessed(self) -> None:
        assert any("/app/uploads" in w for w in read_coolify().warnings)

    @pytest.mark.parametrize(
        ("frequency", "expected"),
        [
            ("daily", "0 0 * * *"),
            ("@hourly", "0 * * * *"),
            ("every_minute", "* * * * *"),
            ("*/5 * * * *", "*/5 * * * *"),
            ("0 3 * * mon", "0 3 * * mon"),
            ("every now and then", None),
            ("", None),
        ],
    )
    def test_task_frequencies(self, frequency: str, expected: str | None) -> None:
        assert coolify.cron_of(frequency) == expected

    def test_a_static_site(self) -> None:
        proposal = read_coolify({"build_pack": "static"})
        assert proposal.app_type == "static"

    def test_a_dockerfile_application_says_how_to_create_it(self) -> None:
        proposal = read_coolify(
            {"build_pack": "dockerfile", "dockerfile_location": "/docker/Dockerfile"}
        )
        assert proposal.app_type == "container"
        assert "--builder dockerfile" in (proposal.equivalent or "")
        assert "--dockerfile docker/Dockerfile" in (proposal.equivalent or "")

    def test_a_compose_application_says_how_to_create_it(self) -> None:
        proposal = read_coolify(
            {"build_pack": "dockercompose", "docker_compose_location": "/deploy/compose.yaml"}
        )
        assert proposal.app_type == "docker-compose"
        assert "--compose-file deploy/compose.yaml" in (proposal.equivalent or "")

    def test_an_image_application(self) -> None:
        proposal = read_coolify(
            {
                "build_pack": "image",
                "docker_registry_image_name": "ghcr.io/acme/shop",
                "docker_registry_image_tag": "1.4.0",
            }
        )
        assert proposal.image == "ghcr.io/acme/shop:1.4.0"
        assert proposal.equivalent == "noust create -d DOMAIN --image ghcr.io/acme/shop:1.4.0"

    def test_what_cannot_be_read_is_a_warning_not_a_failure(self) -> None:
        server = coolify_server()
        del server.answers["/api/v1/applications/abc123/envs"]
        del server.answers["/api/v1/applications/abc123/scheduled-tasks"]
        client = coolify.connect("https://c.example.com", TOKEN, opener=server)

        proposal = coolify.read_application(client, "abc123")

        assert proposal.env == []
        assert any("variables could not be read" in w for w in proposal.warnings)

    def test_the_commands_become_a_block_only_where_they_are_plain(self) -> None:
        proposal = read_coolify({"build_command": "pnpm install && pnpm build"})

        document = proposal.build_document()

        assert document is not None
        assert "base_directory: apps/web" in document
        assert "install: pnpm install --frozen-lockfile" in document
        assert "start: pnpm start" in document
        assert "&&" not in document
        assert any("build command" in w and "shell" in w for w in proposal.warnings)

    def test_nothing_is_written_to_coolify(self) -> None:
        server = coolify_server()
        client = coolify.connect("https://c.example.com", TOKEN, opener=server)
        coolify.list_applications(client)
        coolify.read_application(client, "abc123")
        assert {request.get_method() for request in server.requests} == {"GET"}


# --------------------------------------------------------------------------- Dokploy

DOKPLOY_APP = {
    "applicationId": "app_1",
    "name": "Shop",
    "appName": "shop-abc",
    "sourceType": "github",
    "owner": "acme",
    "repository": "shop",
    "branch": "main",
    "buildPath": "/apps/web",
    "buildType": "nixpacks",
    "env": 'NODE_ENV=production\n# comment\nSESSION_SECRET=abc123\nNAME="Quoted value"\n',
    "buildArgs": "FOO=bar",
    "memoryLimit": "536870912",
    "cpuLimit": "1500000000",
    "watchPaths": ["apps/web/**", "!apps/web/docs/**"],
    "enableSubmodules": True,
    "replicas": 3,
    "placementSwarm": {"Constraints": ["node.role==manager"]},
    "domains": [
        {"host": "shop.example.com", "port": 3000, "https": True, "domainType": "application"},
        {"host": "pr-4.shop.example.com", "port": 3000, "domainType": "preview"},
    ],
    "mounts": [{"type": "volume", "mountPath": "/data"}],
}


def dokploy_server(app: dict[str, Any] | None = None, *, nested: bool = True) -> FakeServer:
    full = {**DOKPLOY_APP, **(app or {})}
    summary = {
        k: v
        for k, v in full.items()
        if k
        in (
            "applicationId",
            "name",
            "appName",
            "sourceType",
            "owner",
            "repository",
            "buildType",
            "domains",
        )
    }
    project: dict[str, Any] = {"projectId": "p1", "name": "Storefront"}
    if nested:
        project["environments"] = [{"name": "production", "applications": [summary]}]
    else:
        project["applications"] = [summary]
    return FakeServer(
        {"/api/project.all": [project], "/api/application.one?applicationId=app_1": full}
    )


def read_dokploy(app: dict[str, Any] | None = None) -> Proposal:
    server = dokploy_server(app)
    server.answers["/api/application.one?applicationId=app_1"] = {**DOKPLOY_APP, **(app or {})}
    client = dokploy.connect("https://dokploy.example.com", TOKEN, opener=server)
    return dokploy.read_application(client, "app_1")


class TestDokploy:
    @pytest.mark.parametrize("nested", [True, False])
    def test_applications_are_listed_whichever_way_the_projects_nest_them(
        self, nested: bool
    ) -> None:
        client = dokploy.connect(
            "https://d.example.com", TOKEN, opener=dokploy_server(nested=nested)
        )

        (found,) = dokploy.list_applications(client)

        assert found.ref == "app_1" and found.name == "Shop"
        assert found.source == "https://github.com/acme/shop.git"
        assert found.domains == ("shop.example.com", "pr-4.shop.example.com")
        assert found.project.startswith("Storefront")

    def test_repository_branch_and_build_path(self) -> None:
        proposal = read_dokploy()

        assert proposal.platform == "dokploy"
        assert proposal.source == "https://github.com/acme/shop.git"
        assert proposal.branch == "main"
        assert proposal.base_directory == "apps/web"
        assert proposal.watch_paths == ["apps/web/**", "!apps/web/docs/**"]
        assert proposal.submodules is True

    def test_domains_leave_the_previews_out_and_the_first_port_is_the_ports(self) -> None:
        proposal = read_dokploy()
        assert proposal.domains == ["shop.example.com"]
        assert proposal.port == 3000

    def test_limits(self) -> None:
        proposal = read_dokploy()
        assert proposal.memory_max_mb == 512
        assert proposal.cpu_quota_percent == 150

    def test_variables_are_read_from_the_text_and_secrets_are_withheld(self) -> None:
        proposal = read_dokploy()
        env = {variable.name: variable for variable in proposal.env}

        assert env["NODE_ENV"].value == "production"
        assert env["NAME"].value == "Quoted value"
        assert env["SESSION_SECRET"].value is None and env["SESSION_SECRET"].required
        assert "comment" not in env

    def test_what_has_no_equivalent_is_said(self) -> None:
        warnings = " ".join(read_dokploy().warnings)
        assert "3 replicas" in warnings
        assert "Swarm placement" in warnings
        assert "build arguments" in warnings
        assert "nixpacks" in warnings
        assert "/data" in warnings

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                {
                    "sourceType": "gitlab",
                    "gitlabPathNamespace": "acme/sub/shop",
                    "gitlabBranch": "dev",
                },
                ("https://gitlab.com/acme/sub/shop.git", "dev"),
            ),
            (
                {
                    "sourceType": "bitbucket",
                    "bitbucketOwner": "acme",
                    "bitbucketRepositorySlug": "shop",
                    "bitbucketBranch": "trunk",
                },
                ("https://bitbucket.org/acme/shop.git", "trunk"),
            ),
            (
                {
                    "sourceType": "git",
                    "customGitUrl": "ssh://git@git.example.com:2222/acme/shop.git",
                    "customGitBranch": "prod",
                    "customGitBuildPath": "/web",
                },
                ("ssh://git@git.example.com:2222/acme/shop.git", "prod"),
            ),
        ],
    )
    def test_each_kind_of_source(self, source: dict[str, Any], expected: tuple[str, str]) -> None:
        proposal = read_dokploy(source)
        assert (proposal.source, proposal.branch) == expected

    def test_a_docker_image(self) -> None:
        proposal = read_dokploy(
            {"sourceType": "docker", "dockerImage": "nginx:1.27", "username": "me"}
        )
        assert proposal.image == "nginx:1.27"
        assert proposal.equivalent == "noust create -d DOMAIN --image nginx:1.27"
        assert any("registry login" in w for w in proposal.warnings)

    def test_an_upload_has_no_repository(self) -> None:
        proposal = read_dokploy({"sourceType": "drop"})
        assert proposal.source is None
        assert any("uploaded" in w for w in proposal.warnings)

    def test_a_gitea_source_asks_for_the_repository(self) -> None:
        proposal = read_dokploy({"sourceType": "gitea", "giteaBranch": "main"})
        assert proposal.source is None and proposal.branch == "main"
        assert any("Gitea" in w and "--source" in w for w in proposal.warnings)

    def test_a_static_site_and_a_dockerfile(self) -> None:
        static = read_dokploy({"buildType": "static", "publishDirectory": "/dist"})
        assert (static.app_type, static.output_directory) == ("static", "dist")
        docker = read_dokploy(
            {
                "buildType": "dockerfile",
                "dockerfile": "deploy/Dockerfile",
                "dockerContextPath": "web",
            }
        )
        assert docker.app_type == "container"
        assert "--dockerfile deploy/Dockerfile" in (docker.equivalent or "")
        assert "--context web" in (docker.equivalent or "")


# --------------------------------------------------------------------------- the document


class TestTheDocument:
    def test_a_coolify_application_becomes_an_import_plan(self, tmp_path: Path) -> None:
        from noust.core.store import NoustStore

        NoustStore.reset_instance()
        NoustStore(tmp_path / "noust.db")
        try:
            proposal = read_coolify()
            # A connection string with a password in it is a credential whatever the variable is
            # called: it is withheld, and a plan needs it given again (it names Coolify's database).
            given = {"STRIPE_SECRET": "sk_live_x", "DATABASE_URL": "postgres://u:p@db.example/shop"}
            document = proposal_document(proposal, domain="shop.example.com", source="", env=given)

            plan = plan_import(document, env=given)

            create = plan.create
            assert create.source == "github:acme/shop"
            assert create.branch == "main"
            assert create.port == 3000
            assert create.memory_max_mb == 512 and create.cpu_quota_percent == 150
            assert create.health_path == "/healthz" and create.health_timeout == 7
            assert create.build_document is not None
            assert "pnpm build" in create.build_document
            assert create.env_vars["STRIPE_SECRET"] == "sk_live_x"
            assert plan.document["source_options"] == {
                "watch_paths": ["apps/web/**", "!apps/web/docs/**"]
            }
            assert plan.document["cron"][0]["name"] == "nightly"
        finally:
            NoustStore.reset_instance()

    def test_a_secret_it_could_not_read_stops_the_plan_and_names_it(self, tmp_path: Path) -> None:
        from noust.core.store import NoustStore

        NoustStore.reset_instance()
        NoustStore(tmp_path / "noust.db")
        try:
            document = proposal_document(read_coolify(), domain="shop.example.com", source="")
            with pytest.raises(ValidationError) as caught:
                plan_import(document)
            assert "STRIPE_SECRET" in caught.value.message
            assert "DATABASE_URL" in caught.value.message
        finally:
            NoustStore.reset_instance()

    def test_a_block_that_is_not_valid_is_refused_at_the_plan(self, tmp_path: Path) -> None:
        from noust.core.store import NoustStore

        NoustStore.reset_instance()
        NoustStore(tmp_path / "noust.db")
        try:
            proposal = read_coolify()
            given = {"STRIPE_SECRET": "x", "DATABASE_URL": "postgres://u:p@db.example/shop"}
            document = proposal_document(proposal, domain="shop.example.com", source="", env=given)
            document["app"]["build"] = "build:\n  base_directory: ../../etc\n"
            with pytest.raises(ValidationError) as caught:
                plan_import(document, env=given)
            assert caught.value.field == "build.base_directory"
        finally:
            NoustStore.reset_instance()


def test_plain_commands() -> None:
    assert is_plain_command("pnpm --filter web build")
    assert not is_plain_command("pnpm i && pnpm build")
    assert not is_plain_command("cd web; npm start")
    assert not is_plain_command("PORT=3000 node server.js")
    assert not is_plain_command("echo $(whoami)")
