# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The relay: a Compose web service is recreated while a twin of it serves (spec 3.2 section 1.3).

The engine runs against the real deployer, store and servers files (in a
temporary tree); Docker is a fake runner, nginx a fake that records what the
servers files said at every reload, and the health probe answers by port.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, NoustStore, get_store
from noust.deployers import bluegreen, compose_relay, docker_compose
from noust.deployers.compose_relay import (
    ComposeRelay,
    check_relay_eligible,
    disable_relay,
    enable_relay,
    relay_order,
    relayed_services,
)
from noust.deployers.docker_compose import DockerComposeDeployer, parse_services
from noust.managers import webserver
from noust.managers.webserver import read_servers, servers_file, write_servers

DOMAIN = "proggest.example.com"
APP = "proggest-example-com"
PROJECT = "proggest"
SPARES = [25000, 25001, 25002]

#: Proggest's shape: a front that calls its backend, which needs a database,
#: and a worker; the front is listed first.
COMPOSE = """\
services:
  frontend:
    build: ./front
    ports: ["127.0.0.1:3001:3001"]
    depends_on: [backend]
  backend:
    build: ./back
    ports: ["127.0.0.1:3000:3000"]
    depends_on:
      postgres:
        condition: service_healthy
  postgres:
    image: postgres:16-alpine
  worker:
    build: ./back
    depends_on: [backend]
"""


class Docker(FakeRunner):
    """Docker as a stack sees it: relay containers exist from ``run`` until ``rm``."""

    def __init__(self) -> None:
        super().__init__()
        self.relays: set[str] = set()

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv

        def answer(stdout: str = "", stderr: str = "", code: int = 0) -> CommandResult:
            return replace(result, stdout=stdout, stderr=stderr, exit_code=code)

        if args[:3] == ("docker", "container", "inspect"):
            return answer("running\n") if args[-1] in self.relays else answer(code=1)
        if args[:2] == ("docker", "rm"):
            self.relays.discard(args[-1])
        if args[:2] == ("docker", "compose") and "run" in args:
            self.relays.add(args[args.index("--name") + 1])
        if args[:2] == ("docker", "compose") and "ps" in args and "-q" in args:
            return answer("c0ffee01c0ffee01\n")
        if args[:2] == ("docker", "compose") and "ps" in args:
            return answer(json.dumps({"Service": "backend", "Name": "b", "State": "running"}))
        if args[:2] == ("docker", "inspect"):
            return answer("sha256:0ld|proggest-backend|backend|proggest\n")
        return result

    def compose_calls(self) -> list[tuple[str, ...]]:
        """The docker compose subcommands, without the project and file flags."""
        calls = []
        for call in self.calls:
            if call[:2] != ("docker", "compose"):
                continue
            rest = list(call[2:])
            while rest and rest[0] in ("-p", "-f"):
                rest = rest[2:]
            calls.append(tuple(rest))
        return calls


class Web:
    """nginx as the relay sees it: what the servers files said at every reload."""

    def __init__(self, site: str | None = None, *, operator: bool = False) -> None:
        self.site = site
        self.operator = operator
        self.refusals: list[str] = []
        self.loaded: list[dict[str, list[int]]] = []

    def config_errors(self) -> str | None:
        return self.refusals.pop(0) if self.refusals else None

    def reload(self) -> bool:
        self.loaded.append(
            {
                name: read_servers(APP, name)
                for name in ("frontend", "backend")
                if read_servers(APP, name)
            }
        )
        return True

    def site_exists(self, domain: str) -> bool:
        return self.site is not None

    def site_is_noust(self, domain: str) -> bool:
        return not self.operator

    def get_site_config(self, domain: str) -> str | None:
        return self.site

    def config_path(self, domain: str) -> Path:
        return Path("/etc/nginx/sites-available") / domain


class Probe:
    """The health probe: answers by port, and remembers what it was asked."""

    def __init__(self, down: set[int] | None = None) -> None:
        self.down = down or set()
        self.asked: list[int] = []

    def __call__(self, url: str, **_: Any) -> bool:
        port = int(url.split(":")[2].split("/")[0])
        self.asked.append(port)
        return port not in self.down


@pytest.fixture
def upstreams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The servers files' directory, in the test's tree."""
    directory = tmp_path / "noust-upstreams"
    directory.mkdir()
    monkeypatch.setattr(webserver, "NGINX_UPSTREAMS_DIR", directory)
    return directory


@pytest.fixture
def store() -> Iterator[NoustStore]:
    """The process-wide store, at the location conftest redirects it to."""
    NoustStore.reset_instance()
    instance = get_store()
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def docker() -> Iterator[Docker]:
    """The fake docker, installed as the process-wide runner too."""
    fake = Docker()
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """The stack's checkout; its directory names the Compose project."""
    path = tmp_path / "apps" / PROJECT
    path.mkdir(parents=True)
    (path / ".git").mkdir()
    (path / "docker-compose.yml").write_text(COMPOSE)
    return path


@pytest.fixture
def app(store: NoustStore, root: Path) -> App:
    """The stack's row, in zero-downtime mode with no drain."""
    store.create_app(
        App(
            domain=DOMAIN,
            app_type="docker-compose",
            app_path=str(root),
            port=3001,
            webserver="nginx",
            status="running",
        )
    )
    store.set_zero_downtime(DOMAIN, True, drain_seconds=0)
    row = store.get_app(DOMAIN)
    assert row is not None
    return row


def deployer_for(root: Path, docker: Docker, web: Web) -> DockerComposeDeployer:
    """The deployer the update builds, its compose file found and read."""
    deployer = DockerComposeDeployer(runner=docker)
    deployer.app_path = root
    deployer.app_name = APP
    deployer.domain = DOMAIN
    deployer.webserver_manager = lambda: web  # type: ignore[method-assign,assignment,return-value]
    deployer._discover_compose_file()
    deployer.services = parse_services(deployer._load_compose_document())
    return deployer


def engine(
    deployer: DockerComposeDeployer, web: Web, probe: Probe, slept: list[float] | None = None
) -> ComposeRelay:
    """The relay over the fakes, with the spare ports in order."""
    spares = iter(SPARES)
    return ComposeRelay(
        deployer,
        sleep=(slept if slept is not None else []).append,
        probe=probe,
        web=web,  # type: ignore[arg-type]
        listeners=lambda port: [],
        port_finder=lambda **_: next(spares),
    )


def noust_site() -> str:
    """Noust's site for the stack, with the relay on: both files included."""
    return (
        "# Generated by Noust\n"
        f"upstream wasm_bg_{APP.replace('-', '_')}_frontend {{\n"
        f"    include {servers_file(APP, 'frontend')};\n    keepalive 64;\n}}\n"
        f"upstream wasm_bg_{APP.replace('-', '_')}_backend {{\n"
        f"    include {servers_file(APP, 'backend')};\n    keepalive 64;\n}}\n"
    )


def own_ports() -> None:
    """Both servers files, naming each service's own port."""
    write_servers(APP, "frontend", [3001])
    write_servers(APP, "backend", [3000])


# -- Order -----------------------------------------------------------------------------


def test_web_services_are_relayed_in_depends_on_order() -> None:
    services = parse_services(
        {
            "services": {
                "frontend": {"depends_on": ["backend"]},
                "admin": {"depends_on": {"frontend": {}}},
                "backend": {"depends_on": ["postgres"]},
                "postgres": {},
            }
        }
    )

    assert relay_order(services, ["admin", "frontend", "backend"]) == [
        "backend",
        "frontend",
        "admin",
    ]


# -- The relay -------------------------------------------------------------------------


def test_a_relay_takes_the_traffic_while_each_service_is_recreated(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    own_ports()
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)
    slept: list[float] = []
    store_row = deployer.store.get_app(DOMAIN)
    assert store_row is not None
    deployer.store.set_zero_downtime(DOMAIN, True, drain_seconds=3)

    healthy, evidence = engine(deployer, web, Probe(), slept).recreate(["frontend", "backend"])

    assert healthy, evidence
    calls = docker.compose_calls()
    run_backend = (
        "run", "-d", "--no-deps", "--use-aliases", "--name", "proggest-backend-relay",
        "--publish", "127.0.0.1:25000:3000", "backend",
    )  # fmt: skip
    run_frontend = (
        "run", "-d", "--no-deps", "--use-aliases", "--name", "proggest-frontend-relay",
        "--publish", "127.0.0.1:25001:3001", "frontend",
    )  # fmt: skip
    up_backend = ("up", "-d", "--no-deps", "--force-recreate", "backend")
    up_frontend = ("up", "-d", "--no-deps", "--force-recreate", "frontend")
    assert [c for c in calls if c[0] in ("run", "up")] == [
        run_backend,
        up_backend,
        run_frontend,
        up_frontend,
    ]
    assert web.loaded == [
        {"frontend": [3001], "backend": [25000]},
        {"frontend": [3001], "backend": [3000]},
        {"frontend": [25001], "backend": [3000]},
        {"frontend": [3001], "backend": [3000]},
    ]
    for name in ("proggest-backend-relay", "proggest-frontend-relay"):
        assert ("docker", "stop", "-t", "20", name) in docker.calls
        assert ("docker", "rm", "-f", name) in docker.calls
    assert docker.relays == set()
    assert slept == [3, 3, 3, 3]


def test_a_relay_that_fails_its_gate_leaves_the_service_untouched(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    own_ports()
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)
    docker.script(["docker", "logs"], stdout="Error: Cannot find module 'dist/main'")

    healthy, evidence = engine(deployer, web, Probe(down={25000})).recreate(["frontend", "backend"])

    assert not healthy
    assert "backend kept serving from its own container, which was not touched" in evidence
    assert "Cannot find module 'dist/main'" in evidence
    assert not [c for c in docker.compose_calls() if c[0] == "up"]
    assert not [c for c in docker.compose_calls() if "frontend" in c]
    assert read_servers(APP, "backend") == [3000]
    assert web.loaded == []
    assert ("docker", "rm", "-f", "proggest-backend-relay") in docker.calls
    assert docker.relays == set()


def test_nginx_refusing_the_relay_recreates_with_a_cut_and_says_so(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    own_ports()
    web = Web(noust_site())
    web.refusals = ["nginx: [emerg] no servers are inside upstream"]
    deployer = deployer_for(root, docker, web)
    warned: list[str] = []
    deployer.logger.warning = warned.append  # type: ignore[method-assign]

    healthy, evidence = engine(deployer, web, Probe()).recreate(["backend"])

    assert healthy, evidence
    assert any("with a cut" in line and "no servers are inside upstream" in line for line in warned)
    assert read_servers(APP, "backend") == [3000]
    assert ("up", "-d", "--no-deps", "--force-recreate", "backend") in docker.compose_calls()
    assert docker.relays == set()


def test_a_service_that_does_not_come_back_leaves_the_relay_serving(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    own_ports()
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)

    healthy, evidence = engine(deployer, web, Probe(down={3000})).recreate(["backend"])

    assert not healthy
    assert "proggest-backend-relay" in evidence and "127.0.0.1:25000" in evidence
    assert "keeps serving" in evidence
    assert read_servers(APP, "backend") == [25000]
    assert "proggest-backend-relay" in docker.relays
    assert not [c for c in docker.calls if c[:2] == ("docker", "stop")]


def test_leftover_relay_is_resolved_first(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    """An update killed between the switch and the way back: the next one gives it back."""
    write_servers(APP, "frontend", [3001])
    write_servers(APP, "backend", [25999])
    docker.relays.add("proggest-backend-relay")
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)

    healthy, evidence = engine(deployer, web, Probe()).recreate(["frontend", "backend"])

    assert healthy, evidence
    removed = docker.calls.index(("docker", "rm", "-f", "proggest-backend-relay"))
    first_run = next(i for i, c in enumerate(docker.calls) if "run" in c)
    assert removed < first_run
    assert web.loaded[0] == {"frontend": [3001], "backend": [3000]}
    assert read_servers(APP, "backend") == [3000]


def test_a_leftover_relay_whose_service_does_not_answer_changes_nothing(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    write_servers(APP, "backend", [25999])
    docker.relays.add("proggest-backend-relay")
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)

    healthy, evidence = engine(deployer, web, Probe(down={3000})).recreate(["backend"])

    assert not healthy
    assert "nothing was changed" in evidence and "127.0.0.1:25999" in evidence
    assert read_servers(APP, "backend") == [25999]
    assert "proggest-backend-relay" in docker.relays
    assert not [c for c in docker.compose_calls() if c[0] in ("run", "up")]


def test_a_servers_file_naming_the_previous_own_port_is_not_a_relay(
    root: Path, app: App, upstreams: Path, docker: Docker, store: NoustStore
) -> None:
    """Review E2: a service moved from 2999 to 3000 left no relay; its update goes on."""
    write_servers(APP, "frontend", [3001])
    write_servers(APP, "backend", [2999])
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)
    # The new port has nobody yet: the old container still listens on 2999.
    relay = engine(deployer, web, Probe(down={3000}))

    assert relay.resolve_leftovers(["frontend", "backend"]) is None
    assert relay.leftovers(["frontend", "backend"]) == []
    assert compose_relay.leftover_relays(store, runner=docker) == []
    # Left for the relay to move, once the new container answers.
    assert read_servers(APP, "backend") == [2999]
    assert web.loaded == []


def test_a_servers_file_in_the_relay_range_is_a_relay_even_without_its_container(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    write_servers(APP, "backend", [25999])
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)

    problem = engine(deployer, web, Probe(down={3000})).resolve_leftovers(["backend"])

    assert problem is not None and "127.0.0.1:25999" in problem


def test_an_update_refused_over_a_relay_puts_the_serving_commit_back(
    root: Path, app: App, upstreams: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pull already moved the tree: the refusal leaves it on the commit that serves."""
    previous = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
    write_servers(APP, "frontend", [3001])
    write_servers(APP, "backend", [25999])
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)
    deployer.previous_commit = previous
    monkeypatch.setattr(docker_compose, "wait_until_healthy", lambda url, **kw: ":3000" not in url)
    monkeypatch.setattr(
        docker_compose.SourceManager, "resolve_commit", lambda self, path, commit: commit
    )

    with pytest.raises(DeploymentError):
        deployer.update()

    assert [c for c in docker.calls if c[0] == "git" and "checkout" in c and previous in c]


def test_doctor_finds_a_relay_the_servers_file_still_names(
    root: Path, app: App, upstreams: Path, docker: Docker, store: NoustStore
) -> None:
    """Review focus 3: found, with the command that resolves it, and nothing touched."""
    write_servers(APP, "frontend", [3001])
    write_servers(APP, "backend", [25999])
    docker.relays.add("proggest-backend-relay")

    (found,) = compose_relay.leftover_relays(store, runner=docker)

    assert found.domain == DOMAIN
    assert "127.0.0.1:25999" in found.what and "proggest-backend-relay" in found.what
    assert f"noust update {DOMAIN}" in found.fix
    assert read_servers(APP, "backend") == [25999]
    assert not [call for call in docker.calls if call[:2] in (("docker", "rm"), ("docker", "stop"))]


def test_doctor_finds_a_relay_container_nobody_is_sent_to(
    root: Path, app: App, upstreams: Path, docker: Docker, store: NoustStore
) -> None:
    own_ports()
    docker.relays.add("proggest-frontend-relay")

    (found,) = compose_relay.leftover_relays(store, runner=docker)

    assert "proggest-frontend-relay" in found.what and "nothing is sent to it" in found.what


def test_doctor_has_nothing_to_say_about_a_settled_stack(
    root: Path, app: App, upstreams: Path, docker: Docker, store: NoustStore
) -> None:
    own_ports()

    assert compose_relay.leftover_relays(store, runner=docker) == []


def test_a_rehearsal_relays_nothing(
    root: Path, app: App, upstreams: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    own_ports()
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)
    monkeypatch.setattr(deployer, "_rehearsing", lambda: True)

    healthy, _ = engine(deployer, web, Probe()).recreate(["backend"])

    assert healthy
    assert docker.calls == [] and web.loaded == []


# -- Who may use it --------------------------------------------------------------------


def test_an_operator_site_without_the_include_is_refused_with_the_exact_line(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    site = (
        "upstream nestjs_upstream { server 127.0.0.1:3000; keepalive 64; }\n"
        f"upstream next_upstream {{ include {servers_file(APP, 'frontend')}; }}\n"
        "server { location /api/ { proxy_pass http://nestjs_upstream; } }\n"
    )
    deployer = deployer_for(root, docker, Web(site, operator=True))

    problems = check_relay_eligible(deployer)

    assert len(problems) == 1
    assert f"include {upstreams}/{APP}/backend.servers;" in problems[0]
    assert "127.0.0.1:3000" in problems[0]


def test_an_operator_site_that_includes_every_file_is_eligible(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    deployer = deployer_for(root, docker, Web(noust_site().replace("# Generated by Noust\n", "")))

    assert check_relay_eligible(deployer) == []


def test_a_web_service_without_a_fixed_host_port_is_an_actionable_problem(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    (root / "docker-compose.yml").write_text(
        'services:\n  web:\n    build: .\n    ports: ["127.0.0.1:3001:3001"]\n'
        '  api:\n    build: ./api\n    ports: ["8080"]\n'
    )
    deployer = deployer_for(root, docker, Web(noust_site()))

    problems = check_relay_eligible(deployer)

    assert len(problems) == 1
    assert "api" in problems[0] and '"127.0.0.1:<port>:8080"' in problems[0]


def test_a_headless_stack_has_nothing_to_relay(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    (root / "docker-compose.yml").write_text("services:\n  worker:\n    build: .\n")
    deployer = deployer_for(root, docker, Web(noust_site()))

    problems = check_relay_eligible(deployer)

    assert problems and "no web service to relay" in problems[0]


def test_only_what_the_site_includes_is_relayed(
    root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    own_ports()
    site = f"upstream front {{ include {servers_file(APP, 'frontend')}; }}\n"
    deployer = deployer_for(root, docker, Web(site, operator=True))

    assert relayed_services(deployer) == ["frontend"]
    deployer.store.set_zero_downtime(DOMAIN, False)
    assert relayed_services(deployer) == []


# -- Turning it on and off -------------------------------------------------------------


def test_turning_it_on_with_an_operator_site_writes_the_files_then_asks_for_the_line(
    root: Path, store: NoustStore, upstreams: Path, docker: Docker
) -> None:
    row = store.create_app(
        App(domain=DOMAIN, app_type="docker-compose", app_path=str(root), port=3001)
    )
    site = "upstream nestjs_upstream { server 127.0.0.1:3000; }\nupstream n { server 127.0.0.1:3001; }\n"
    deployer = deployer_for(root, docker, Web(site, operator=True))

    with pytest.raises(ValidationError) as refusal:
        enable_relay(row, drain_seconds=None, logger=deployer.logger, deployer=deployer)

    assert f"include {upstreams}/{APP}/backend.servers;" in (refusal.value.details or "")
    assert f"include {upstreams}/{APP}/frontend.servers;" in (refusal.value.details or "")
    # The files are in place, so the line the operator adds loads.
    assert read_servers(APP, "backend") == [3000]
    assert read_servers(APP, "frontend") == [3001]
    after = store.get_app(DOMAIN)
    assert after is not None and not after.zero_downtime


def test_turning_it_on_and_off_switches_noust_site(
    root: Path, store: NoustStore, upstreams: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = store.create_app(
        App(domain=DOMAIN, app_type="docker-compose", app_path=str(root), port=3001)
    )
    web = Web("# Generated by Noust\nserver { location / { proxy_pass http://127.0.0.1:3001; } }\n")
    deployer = deployer_for(root, docker, web)
    refreshed: list[bool] = []

    enable_relay(
        row,
        drain_seconds=5,
        logger=deployer.logger,
        deployer=deployer,
        refresh=lambda app: refreshed.append(app.zero_downtime),
    )

    on = store.get_app(DOMAIN)
    assert on is not None and on.zero_downtime and on.drain_seconds == 5
    assert refreshed == [True]
    # Only the service the site proxies to: the backend is not routed.
    assert read_servers(APP, "frontend") == [3001]
    assert read_servers(APP, "backend") == []

    disable_relay(
        on,
        logger=deployer.logger,
        deployer=deployer,
        refresh=lambda app: refreshed.append(app.zero_downtime),
        relay=engine(deployer, web, Probe()),
    )

    off = store.get_app(DOMAIN)
    assert off is not None and not off.zero_downtime
    assert refreshed == [True, False]
    assert not (upstreams / APP).exists()


def test_set_zero_downtime_accepts_a_compose_stack(
    root: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.create_app(App(domain=DOMAIN, app_type="docker-compose", app_path=str(root), port=3001))
    seen: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        compose_relay,
        "enable_relay",
        lambda app, *, drain_seconds, logger: seen.append((app.domain, drain_seconds)),
    )

    change = bluegreen.set_zero_downtime(DOMAIN, True, drain_seconds=4)

    assert seen == [(DOMAIN, 4)]
    assert change.enabled and change.changed and change.active_color is None


def test_check_eligible_of_a_compose_stack_is_the_relay_check(
    store: NoustStore, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = store.create_app(
        App(domain=DOMAIN, app_type="docker-compose", app_path=str(root), port=3001)
    )
    monkeypatch.setattr(
        compose_relay,
        "check_app_relay_eligible",
        lambda app, site=True: ["api publishes no fixed port"],
    )

    with pytest.raises(ValidationError, match="api publishes no fixed port"):
        bluegreen.check_eligible(row, store=store)

    monkeypatch.setattr(compose_relay, "check_app_relay_eligible", lambda app, site=True: [])
    bluegreen.check_eligible(row, store=store)


# -- The deployer ----------------------------------------------------------------------


def test_an_update_relays_the_web_services_then_brings_up_the_rest(
    root: Path, app: App, upstreams: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    own_ports()
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)
    deployer.previous_commit = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
    monkeypatch.setattr(docker_compose, "wait_until_healthy", lambda url, **kw: True)
    monkeypatch.setattr(docker_compose.time, "sleep", lambda seconds: None)
    spares = iter(SPARES)
    monkeypatch.setattr(compose_relay, "find_available_port", lambda **_: next(spares))
    monkeypatch.setattr(compose_relay, "listeners_on", lambda port, runner=None: [])

    deployer.update()

    steps = [c[0] if c[0] != "up" else " ".join(c) for c in docker.compose_calls()]
    assert steps == [
        "ps",
        "build",
        "run",
        "up -d --no-deps --force-recreate backend",
        "run",
        "up -d --no-deps --force-recreate frontend",
        "up -d --remove-orphans",
        "ps",
    ]
    assert read_servers(APP, "backend") == [3000] and read_servers(APP, "frontend") == [3001]


def test_an_update_refuses_to_start_over_a_relay_it_cannot_resolve(
    root: Path, app: App, upstreams: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_servers(APP, "frontend", [3001])
    write_servers(APP, "backend", [25999])
    web = Web(noust_site())
    deployer = deployer_for(root, docker, web)
    monkeypatch.setattr(docker_compose, "wait_until_healthy", lambda url, **kw: ":3000" not in url)

    with pytest.raises(DeploymentError) as refused:
        deployer.update()

    assert "nothing was changed" in (refused.value.details or "")
    assert not [c for c in docker.compose_calls() if c[0] in ("build", "run", "up")]


def test_noust_site_proxies_through_the_servers_file_when_the_relay_is_on(
    tmp_path: Path, root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    from noust.managers.nginx_manager import NginxManager
    from noust.managers.webserver import NGINX_BACKEND

    manager = NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )
    deployer = deployer_for(root, docker, Web())
    deployer.webserver_manager = lambda: manager  # type: ignore[method-assign]
    upstream = f"wasm_bg_{APP.replace('-', '_')}_frontend"

    deployer._write_site(with_ssl=False)
    direct = manager.get_site_config(DOMAIN) or ""
    own_ports()
    deployer._write_site(with_ssl=False)
    relayed = manager.get_site_config(DOMAIN) or ""

    assert "proxy_pass http://127.0.0.1:3001;" in direct and "include" not in direct
    assert (
        f"upstream {upstream} {{\n    include {servers_file(APP, 'frontend')};\n"
        "    keepalive 64;\n}" in relayed
    )
    assert f"proxy_pass http://{upstream};" in relayed
    assert "backend.servers" not in relayed


def test_a_noust_nginx_yaml_route_reaches_its_service_through_the_file(
    tmp_path: Path, root: Path, app: App, upstreams: Path, docker: Docker
) -> None:
    (root / "noust.nginx.yaml").write_text(
        "routes:\n  - path: /api/\n    port: 3000\n    name: api\n"
        "  - path: /\n    port: 3001\n    name: front\n"
    )
    own_ports()
    deployer = deployer_for(root, docker, Web())

    template, context = deployer._site_template(with_ssl=False)

    assert template == "advanced"
    files = {up["name"]: up.get("servers_file") for up in context["upstreams"].values()}
    assert files == {
        "api": str(servers_file(APP, "backend")),
        "front": str(servers_file(APP, "frontend")),
    }


def test_the_api_leaves_a_compose_site_to_the_job(
    root: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job writes the servers files before it checks the site; the request does not."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from noust.web.api import zero_downtime as api_module
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    store.create_app(App(domain=DOMAIN, app_type="docker-compose", app_path=str(root), port=3001))
    asked: list[bool] = []
    monkeypatch.setattr(
        compose_relay,
        "check_app_relay_eligible",
        lambda app, site=True: asked.append(site) or [],
    )
    queued: list[dict[str, Any]] = []

    class Job:
        id = "job1"
        status = type("S", (), {"value": "pending"})()

        def to_dict(self) -> dict[str, Any]:
            return {"id": self.id}

    class Jobs:
        def create_job(self, **kwargs: Any) -> Job:
            queued.append(kwargs)
            return Job()

    monkeypatch.setattr(api_module, "get_job_manager", lambda: Jobs())
    api = FastAPI()
    install_error_handlers(api)
    api.include_router(api_module.router, prefix="/api/apps")
    session = {"sid": "t", "type": "master"}
    api.dependency_overrides[get_current_session] = lambda: session
    api.dependency_overrides[require_elevated] = lambda: session
    client = TestClient(api, raise_server_exceptions=False)

    put = client.put(f"/api/apps/{DOMAIN}/zero-downtime", json={"enabled": True})

    assert put.status_code == 202, put.text
    assert asked == [False] and len(queued) == 1
