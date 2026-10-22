# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Dokku, read through its own command line (checklist N-13).

Dokku has no API: its commands are run on its server, or over SSH as the ``dokku`` user. The runner
is faked with what Dokku's documentation shows each command printing (``config:export``'s
``export KEY='value'`` lines, the single-value form of the ``*:report`` commands, ``cron:list``'s
table, ``storage:report``'s ``-v host:container`` mounts) and with a failure where a flag does not
exist in an older Dokku. What is pinned: the commands are the read-only ones and are declared as
such, nothing a person or a server supplies reaches a command line as an option, the variables
Dokku keeps for itself are left out, and a service plugin is found by its host name.
"""

from __future__ import annotations

import pytest

from noust.core.exceptions import IntegrationError, ValidationError
from noust.core.runner import FakeRunner, declared_probes, is_read_only
from noust.deployers.importers import dokku
from noust.deployers.importers.base import Proposal

APPS = "node-js-app\nworker-app\n"

ENV = """\
export DATABASE_URL='postgres://postgres:s3cret@dokku-postgres-shop-db:5432/shop_db'
export REDIS_URL='redis://:pw@dokku-redis-cache:6379'
export NODE_ENV='production'
export GREETING='it'\\''s a "quoted" value'
export DOKKU_APP_RESTORE='1'
export DOKKU_PROXY_PORT='80'
export GIT_REV='abc123'
export SESSION_SECRET='hunter2'
"""

CRON = """\
ID                                    Schedule   Command
cGhwPT09cGhwIHRlc3QucGhwPT09QGRhaWx5  @daily     node index.js
cGhwPT09dHJ1ZT09PSogKiAqICogKg==      * * * * *  true
abcdef0123456789                      whenever   odd
"""

RESOURCES = """\
=====> node-js-app resource information
       web limit cpu: 1.5
       web limit memory: 512
       web limit memory swap:
       web reservation memory: 256
"""


def runner_for(
    *, ports: str = "http:80:5000 https:443:5000", builder: str = "herokuish"
) -> FakeRunner:
    fake = FakeRunner()
    fake.script(["dokku", "--quiet", "apps:list"], stdout=APPS)
    fake.script(["dokku", "--quiet", "config:export", "node-js-app"], stdout=ENV)
    fake.script(
        ["dokku", "--quiet", "domains:report", "node-js-app", "--domains-app-enabled"],
        stdout="true\n",
    )
    fake.script(
        ["dokku", "--quiet", "domains:report", "node-js-app", "--domains-app-vhosts"],
        stdout="shop.example.com www.shop.example.com\n",
    )
    fake.script(
        ["dokku", "--quiet", "ports:report", "node-js-app", "--ports-map"], stdout=f"{ports}\n"
    )
    fake.script(
        ["dokku", "--quiet", "builder:report", "node-js-app", "--builder-computed-selected"],
        stdout=f"{builder}\n",
    )
    fake.script(
        ["dokku", "--quiet", "git:report", "node-js-app", "--git-computed-deploy-branch"],
        stdout="main\n",
    )
    fake.script(
        ["dokku", "--quiet", "storage:report", "node-js-app", "--storage-run-mounts"],
        stdout="-v /var/lib/dokku/data/storage/node-js-app:/app/storage "
        "-v /srv/shared:/var/shared:ro\n",
    )
    fake.script(["dokku", "--quiet", "resource:report", "node-js-app"], stdout=RESOURCES)
    fake.script(["dokku", "--quiet", "cron:list", "node-js-app"], stdout=CRON)
    fake.script(["dokku", "--quiet", "ps:scale", "node-js-app"], stdout="web: 2\nworker: 1\n")
    return fake


def read(fake: FakeRunner | None = None, host: str | None = None) -> Proposal:
    client = dokku.connect(fake or runner_for(), host)
    return dokku.read_application(client, "node-js-app")


# --------------------------------------------------------------------------- the commands


def test_the_applications_are_listed_with_their_builder_and_domains() -> None:
    fake = runner_for()
    fake.script(
        ["dokku", "--quiet", "domains:report", "worker-app", "--domains-app-enabled"],
        stdout="false\n",
    )
    fake.script(
        ["dokku", "--quiet", "builder:report", "worker-app", "--builder-computed-selected"],
        stdout="dockerfile\n",
    )

    found = {a.name: a for a in dokku.list_applications(dokku.connect(fake))}

    assert set(found) == {"node-js-app", "worker-app"}
    assert found["node-js-app"].build == "herokuish"
    assert found["node-js-app"].domains == ("shop.example.com", "www.shop.example.com")
    assert found["worker-app"].build == "dockerfile"
    assert found["worker-app"].domains == ()


def test_every_command_it_runs_is_one_a_dry_run_may_run() -> None:
    fake = runner_for()
    read(fake)

    assert fake.calls, "nothing was asked"
    for argv in fake.calls:
        assert is_read_only(list(argv)), f"{argv} is not declared read-only"


def test_over_ssh_the_commands_are_the_dokku_users_and_are_read_only_too() -> None:
    fake = FakeRunner()
    fake.script(["ssh"], stdout=APPS)
    client = dokku.connect(fake, "dokku@dokku.example.com")

    dokku.list_applications(client)

    first = fake.calls[0]
    assert first[:6] == (
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "dokku@dokku.example.com",
    )
    assert first[6:] == ("apps:list",)
    assert is_read_only(list(first))


def test_the_probes_are_declared_where_the_runner_looks() -> None:
    shapes = declared_probes()

    assert ("dokku", "--quiet", "apps:list") in shapes
    assert ("dokku", "--quiet", "config:export", "*") in shapes


@pytest.mark.parametrize(
    "target",
    ["root@dokku.example.com", "-oProxyCommand=x", "dokku@host;reboot", "host:2222", "", " "],
)
def test_a_server_that_is_not_a_host_name_never_reaches_ssh(target: str) -> None:
    with pytest.raises(ValidationError):
        dokku.connect(FakeRunner(), target)


def test_a_host_with_or_without_the_dokku_user_is_the_same_server() -> None:
    assert dokku.connect(FakeRunner(), "dokku@a.example.com").host == "a.example.com"
    assert dokku.connect(FakeRunner(), "a.example.com").host == "a.example.com"


@pytest.mark.parametrize("name", ["--global", "-x", "App", "a b", "a;b", "../x", ""])
def test_an_application_name_that_could_be_an_option_or_a_command_is_refused(name: str) -> None:
    client = dokku.connect(runner_for())

    with pytest.raises(ValidationError):
        dokku.read_application(client, name)


def test_an_application_that_does_not_exist_is_an_error() -> None:
    client = dokku.connect(runner_for())

    with pytest.raises(IntegrationError, match="no application 'ghost'"):
        dokku.read_application(client, "ghost")


def test_a_dokku_that_is_not_there_says_where_to_run_it() -> None:
    fake = FakeRunner()
    fake.script(["dokku"], stdout="", stderr="dokku: command not found", exit_code=127)

    with pytest.raises(IntegrationError) as caught:
        dokku.list_applications(dokku.connect(fake))

    assert "as root on the Dokku server" in caught.value.details
    assert "command not found" in caught.value.output


def test_a_refused_ssh_connection_says_how_to_trust_the_host() -> None:
    fake = FakeRunner()
    fake.script(["ssh"], stderr="Host key verification failed.", exit_code=255)

    with pytest.raises(IntegrationError) as caught:
        dokku.list_applications(dokku.connect(fake, "dokku.example.com"))

    assert "host key is known" in caught.value.details


# --------------------------------------------------------------------------- the proposal


def test_the_environment_without_what_dokku_keeps_for_itself() -> None:
    proposal = read()

    names = [v.name for v in proposal.env]
    assert "NODE_ENV" in names
    for own in ("DOKKU_APP_RESTORE", "DOKKU_PROXY_PORT", "GIT_REV"):
        assert own not in names
    env = {v.name: v for v in proposal.env}
    assert env["NODE_ENV"].value == "production"
    assert env["GREETING"].value == 'it\'s a "quoted" value'
    assert (env["SESSION_SECRET"].secret, env["SESSION_SECRET"].value) == (True, None)
    assert (env["DATABASE_URL"].secret, env["DATABASE_URL"].value) == (True, None)


def test_the_domains_the_branch_and_the_container_port() -> None:
    proposal = read()

    assert proposal.domains == ["shop.example.com", "www.shop.example.com"]
    assert proposal.branch == "main"
    assert proposal.port == 5000


def test_the_detected_port_stands_in_when_no_map_is_configured() -> None:
    fake = runner_for(ports="")
    fake.script(
        ["dokku", "--quiet", "ports:report", "node-js-app", "--ports-map-detected"],
        stdout="http:80:3000\n",
    )

    assert read(fake).port == 3000


def test_storage_under_app_is_a_persistent_path_and_a_read_only_mount_elsewhere_is_said() -> None:
    proposal = read()

    state, shared = proposal.storage
    assert (state.mount_path, state.persistent_path) == ("/app/storage", "storage")
    assert state.host_path == "/var/lib/dokku/data/storage/node-js-app"
    assert (
        state.export
        == "tar -C /var/lib/dokku/data/storage/node-js-app -czf node-js-app-storage.tar.gz ."
    )
    assert shared.read_only is True
    assert shared.persistent_path is None
    assert proposal.persistent_paths == ["storage"]


def test_the_limits() -> None:
    proposal = read()

    assert proposal.memory_max_mb == 512
    assert proposal.cpu_quota_percent == 150


def test_the_cron_tasks_of_app_json_where_they_are_cron() -> None:
    proposal = read()

    assert [(c.schedule, c.command) for c in proposal.cron] == [
        ("0 0 * * *", "node index.js"),
        ("* * * * *", "true"),
    ]
    assert any("'odd'" in w and "noust cron create" in w for w in proposal.warnings)


def test_a_service_plugin_is_found_by_its_host_name() -> None:
    proposal = read()

    linked = {d.name: d for d in proposal.database_details}
    assert set(linked) == {"shop-db", "cache"}
    assert linked["shop-db"].engine == "postgresql"
    assert linked["shop-db"].reference == "dokku-postgres-shop-db"
    assert linked["shop-db"].dump == "dokku postgres:export shop-db > shop-db.dump"
    assert linked["shop-db"].restore == "noust db import shop_db shop-db.dump -e postgresql"
    assert linked["cache"].dump is None
    assert proposal.databases == ["postgresql", "redis"]


def test_the_scale_is_said() -> None:
    warnings = "\n".join(read().warnings)

    assert "2 copies of its web process" in warnings
    assert "worker x1" in warnings


def test_a_dockerfile_application_is_a_container_the_operator_creates() -> None:
    proposal = read(runner_for(builder="dockerfile"))

    assert proposal.app_type == "container"
    assert proposal.equivalent is not None and "--builder dockerfile" in proposal.equivalent


def test_where_the_code_lives_is_said_with_the_ssh_address_when_there_is_one() -> None:
    fake = runner_for()
    for argv_prefix, out in [
        (["ssh"], ""),
    ]:
        fake.script(argv_prefix, stdout=out)
    local = read()
    assert any("/home/dokku/node-js-app" in w for w in local.warnings)

    remote_fake = FakeRunner()
    remote_fake.script(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "dokku@d.example.com",
            "apps:list",
        ],
        stdout=APPS,
    )
    remote = dokku.read_application(dokku.connect(remote_fake, "d.example.com"), "node-js-app")
    assert any("ssh://dokku@d.example.com/node-js-app" in w for w in remote.warnings)


def test_nothing_is_written_to_dokku() -> None:
    fake = runner_for()
    read(fake)

    verbs = {argv[2].split(":")[1] if ":" in argv[2] else argv[2] for argv in fake.calls}
    assert not verbs & {"set", "create", "destroy", "mount", "unmount", "add", "remove", "clear"}
