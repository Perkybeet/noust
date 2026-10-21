# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""scripts/recipe_smoke.py: the project it writes, the gate's request and its verdicts, without Docker."""

from __future__ import annotations

import http.server
import importlib.util
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from noust.recipes import get_recipe, list_recipes, read_asset
from noust.recipes.variables import scan_references

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "recipe_smoke.py"
COMPOSE = [
    recipe.name for recipe in list_recipes() if recipe.available and recipe.kind == "compose"
]


@pytest.fixture(scope="module")
def smoke() -> ModuleType:
    """The script, loaded as a module."""
    spec = importlib.util.spec_from_file_location("recipe_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_smoke"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server() -> Iterator[tuple[str, dict[str, Any]]]:
    """A loopback HTTP server that answers what ``answers`` says, and records the Host it saw."""
    answers: dict[str, Any] = {"status": 200, "location": None, "hosts": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            answers["hosts"].append(self.headers.get("Host"))
            self.send_response(answers["status"])
            if answers["location"]:
                self.send_header("Location", answers["location"])
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            return None

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", answers
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize(
    ("expect", "status", "accepted"),
    [
        (None, 200, True),
        (None, 302, False),
        ("200", 204, False),
        ("200-399", 301, True),
        ("204", 204, True),
    ],
)
def test_the_gate_accepts_what_the_recipe_expects_and_only_200_when_it_says_nothing(
    smoke: ModuleType, expect: str | None, status: int, accepted: bool
) -> None:
    """The same rule as the deployer's health gate."""
    assert smoke.gate_accepts(expect, status) is accepted


@pytest.mark.parametrize("name", COMPOSE)
def test_every_variable_a_document_names_has_a_value_in_the_env_file_it_writes(
    smoke: ModuleType, tmp_path: Path, name: str
) -> None:
    """The project a deployment writes: document, shipped files, and an .env that satisfies it."""
    recipe = get_recipe(name)

    values = smoke.write_project(
        recipe, tmp_path, port=18080, domain="smoke.example.com", ssl=True, given={}
    )

    assert (tmp_path / "docker-compose.yml").read_text() == recipe.compose
    assert all((tmp_path / path).is_file() for path in recipe.files)
    written = dict(
        line.split("=", 1) for line in (tmp_path / ".env").read_text().splitlines() if "=" in line
    )
    assert written == values and written["PORT"] == "18080"
    for reference in scan_references(recipe.compose or ""):
        assert reference.name in values or reference.operator is not None, (name, reference.name)


@pytest.mark.parametrize("name", [n for n in COMPOSE if get_recipe(n).rendered_files])
def test_a_file_the_recipe_renders_is_written_with_its_variables_filled_in(
    smoke: ModuleType, tmp_path: Path, name: str
) -> None:
    """A deployment fills a rendered file's references; a smoke test that wrote them as they are
    (Gatus's ``${GATUS_PUBLIC_URL}/health``) started the stack on a file no deployment writes."""
    recipe = get_recipe(name)

    values = smoke.write_project(
        recipe, tmp_path, port=18080, domain="smoke.example.com", ssl=True, given={}
    )

    for path in recipe.rendered_files:
        written = (tmp_path / path).read_text()
        for reference in scan_references(read_asset(recipe.name, recipe.files[path])):
            if reference.name in values:
                assert f"${{{reference.name}}}" not in written, (name, path, reference.name)
    if name == "gatus":
        assert 'url: "https://smoke.example.com/health"' in (tmp_path / "config.yaml").read_text()


def test_a_required_variable_gets_a_value_so_the_stack_can_be_written(
    smoke: ModuleType, tmp_path: Path
) -> None:
    """FreshRSS demands an e-mail address; the tool gives one unless it is told which."""
    recipe = get_recipe("freshrss")

    values = smoke.write_project(
        recipe, tmp_path, port=18081, domain="rss.example.com", ssl=False, given={}
    )
    assert (
        values["ADMIN_EMAIL"] == "given@example.com"
        and values["PUBLIC_URL"] == "http://rss.example.com"
    )

    other = tmp_path / "other"
    other.mkdir()
    values = smoke.write_project(
        recipe,
        other,
        port=18081,
        domain="rss.example.com",
        ssl=False,
        given={"ADMIN_EMAIL": "me@example.org"},
    )
    assert values["ADMIN_EMAIL"] == "me@example.org"


def test_the_request_is_the_gates_a_redirect_is_the_answer_and_the_host_is_the_loopback(
    smoke: ModuleType, server: tuple[str, dict[str, Any]]
) -> None:
    """An application that checks the host or redirects to https shows up here, as it would to the gate."""
    url, answers = server
    answers.update(status=301, location="https://smoke.example.com/")

    assert smoke.ask(url, follow=False) == (301, "https://smoke.example.com/")
    assert answers["hosts"][-1] == url.removeprefix("http://")

    answers.update(status=404, location=None)
    assert smoke.ask(url, follow=False) == (404, "")
    assert smoke.ask("http://127.0.0.1:9/", follow=False)[0] == "conn:URLError"


class FakeProject:
    """Stands in for the Docker project: nothing runs."""

    containers: list[dict[str, Any]] = []
    log: str = ""
    started = 0

    def __init__(self, recipe: Any, directory: Path, prefix: str = "noust-smoke-") -> None:
        self.name = f"{prefix}{recipe.name}"

    def run(self, *arguments: str, timeout: int = 0) -> subprocess.CompletedProcess[str]:
        FakeProject.started += arguments[0] == "up"
        stdout = FakeProject.log if arguments[0] == "logs" else ""
        return subprocess.CompletedProcess(list(arguments), 0, stdout, "")

    def inspect(self) -> list[dict[str, Any]]:
        return FakeProject.containers


@pytest.fixture
def fake_docker(smoke: ModuleType, monkeypatch: pytest.MonkeyPatch) -> type[FakeProject]:
    """No Docker, no waiting."""
    monkeypatch.setattr(smoke, "Project", FakeProject)
    monkeypatch.setattr(smoke, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: None)
    FakeProject.containers = [
        {"name": "app-1", "state": "running", "restarts": 0, "oom_killed": False, "health": None}
    ]
    FakeProject.log = "starting\nERROR: one expected line\nlistening\n"
    FakeProject.started = 0
    return FakeProject


def run_smoke(smoke: ModuleType, **options: Any) -> Any:
    """Smoke-test Vaultwarden with the fake project."""
    return smoke.smoke(
        get_recipe("vaultwarden"),
        port=18080,
        wait=10,
        given={},
        extra=["SMTP_HOST=mail.example.test"],
        domain="vault.example.com",
        ssl=True,
        keep=False,
        **options,
    )


def test_a_stack_that_answers_the_gate_and_stays_up_passes_with_its_log_counted(
    smoke: ModuleType, fake_docker: type[FakeProject], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report carries what the gate saw, how long it took, and the error-looking lines."""
    seen: list[tuple[str, bool, Any]] = []

    def answer(url: str, *, follow: bool, headers: Any = None) -> tuple[int, str]:
        seen.append((url, follow, headers))
        return 200, ""

    monkeypatch.setattr(smoke, "ask", answer)

    report = run_smoke(smoke)

    assert report.problems == [] and report.gate_status == "200" and report.proxied_status == "200"
    assert report.error_lines == {"count": 1, "first": ["ERROR: one expected line"]}
    assert seen[0] == ("http://127.0.0.1:18080/alive", False, None)
    assert seen[1][2]["Host"] == "vault.example.com" and seen[1][2]["X-Forwarded-Proto"] == "https"


def test_a_gate_that_is_not_answered_as_expected_is_a_problem_and_stops_the_run(
    smoke: ModuleType, fake_docker: type[FakeProject], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finding the tool exists for: the application answers a host it does not know with 404."""
    monkeypatch.setattr(smoke, "ask", lambda url, *, follow, headers=None: (404, ""))

    report = run_smoke(smoke)

    assert report.gate_status == "404"
    assert report.problems == [
        "the health gate's request to /alive got 404, the recipe expects 200"
    ]
    # The report says why without a second run: what the containers did and what they logged.
    assert report.containers == fake_docker.containers
    assert "ERROR: one expected line" in report.log_tail


def test_a_restart_loop_and_an_out_of_memory_kill_are_problems(
    smoke: ModuleType, fake_docker: type[FakeProject], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A health check can pass between two crashes: the restart count cannot."""
    monkeypatch.setattr(smoke, "ask", lambda url, *, follow, headers=None: (200, ""))
    fake_docker.containers = [
        {"name": "app-1", "state": "running", "restarts": 3, "oom_killed": False, "health": None},
        {"name": "db-1", "state": "running", "restarts": 0, "oom_killed": True, "health": None},
        {
            "name": "cache-1",
            "state": "restarting",
            "restarts": 0,
            "oom_killed": False,
            "health": None,
        },
    ]

    report = run_smoke(smoke)

    assert report.problems == [
        "app-1 restarted 3 times",
        "db-1 was killed for memory",
        "cache-1 is restarting",
    ]


def test_the_extra_lines_reach_the_env_file_as_an_operator_editing_it_would(
    smoke: ModuleType,
    fake_docker: type[FakeProject],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Settings passed through from the environment (Plausible's SMTP) are tested this way."""
    written: dict[str, str] = {}
    monkeypatch.setattr(smoke, "ask", lambda url, *, follow, headers=None: (200, ""))
    original = smoke.write_project

    def capture(*args: Any, **kwargs: Any) -> Any:
        values = original(*args, **kwargs)
        written["directory"] = str(args[1])
        return values

    monkeypatch.setattr(smoke, "write_project", capture)
    monkeypatch.setattr(
        smoke.shutil,
        "rmtree",
        lambda path, ignore_errors=False: written.update(env=(Path(path) / ".env").read_text()),
    )

    run_smoke(smoke)

    assert "SMTP_HOST=mail.example.test\n" in written["env"]
    assert written["env"].count("PORT=18080\n") == 1


# ---------------------------------------------------------------------------
# What the tool creates is named and labelled, so it can be found and removed by name
# ---------------------------------------------------------------------------


def test_the_project_is_named_by_the_prefix_the_operator_gives(
    smoke: ModuleType, tmp_path: Path
) -> None:
    """A machine that shares its Docker names what a run makes: the prefix is the first part."""
    recipe = get_recipe("vaultwarden")

    assert smoke.Project(recipe, tmp_path).name == "noust-smoke-vaultwarden"
    assert smoke.Project(recipe, tmp_path, "noust-test-cat-").name == "noust-test-cat-vaultwarden"
    base = smoke.Project(recipe, tmp_path, "noust-test-cat-")._base
    assert base[base.index("-p") + 1] == "noust-test-cat-vaultwarden"


@pytest.mark.parametrize("prefix", ["", "Noust-", "-x", "has space-", "a/b-", "x" * 41])
def test_a_prefix_that_is_not_a_docker_project_name_is_refused(
    smoke: ModuleType, prefix: str
) -> None:
    """Compose project names are lower-case letters, digits, dashes and underscores."""
    with pytest.raises(SystemExit):
        smoke.check_prefix(prefix)


def test_the_project_stays_inside_the_label_override_it_writes(
    smoke: ModuleType, tmp_path: Path
) -> None:
    """Every container, volume and network of a run carries noust.test=1, whatever the recipe says."""
    recipe = get_recipe("nextcloud")

    smoke.write_project(
        recipe, tmp_path, port=18080, domain="cloud.example.com", ssl=True, given={}
    )

    override = yaml.safe_load((tmp_path / smoke.LABEL_OVERRIDE).read_text())
    document = yaml.safe_load(recipe.compose or "")
    assert set(override["services"]) == set(document["services"])
    for service in override["services"].values():
        assert service["labels"] == {"noust.test": "1"}
    assert set(override["volumes"]) == set(document["volumes"])
    for volume in override["volumes"].values():
        assert volume["labels"] == {"noust.test": "1"}
    assert override["networks"]["default"]["labels"] == {"noust.test": "1"}
    base = smoke.Project(recipe, tmp_path)._base
    assert str(tmp_path / smoke.LABEL_OVERRIDE) in base


def test_a_run_uses_the_prefix_and_the_override_through_every_compose_command(
    smoke: ModuleType, tmp_path: Path
) -> None:
    """The same -f chain reaches up, ps, logs and down: down must find what up made."""
    project = smoke.Project(get_recipe("vaultwarden"), tmp_path, "noust-test-cat-")
    files = [project._base[i + 1] for i, item in enumerate(project._base) if item == "-f"]

    assert files == [
        str(tmp_path / "docker-compose.yml"),
        str(tmp_path / smoke.LABEL_OVERRIDE),
    ]


def test_a_port_somebody_listens_on_is_a_problem_before_anything_starts(
    smoke: ModuleType, fake_docker: type[FakeProject], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only free loopback ports are used: a stack must never be started on another service's."""
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    taken = listener.getsockname()[1]
    try:
        report = smoke.smoke(
            get_recipe("vaultwarden"),
            port=taken,
            wait=1,
            given={},
            extra=[],
            domain="vault.example.com",
            ssl=True,
            keep=False,
        )
    finally:
        listener.close()

    assert fake_docker.started == 0
    assert report.problems == [f"127.0.0.1:{taken} is already in use; pick a free port"]


def test_a_container_that_exited_with_an_error_is_a_problem_and_one_that_finished_is_not(
    smoke: ModuleType, fake_docker: type[FakeProject], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration that failed leaves a stopped container the web service may not notice."""
    monkeypatch.setattr(smoke, "ask", lambda url, *, follow, headers=None: (200, ""))
    fake_docker.containers = [
        {"name": "web-1", "state": "running", "restarts": 0, "oom_killed": False, "exit_code": 0},
        {"name": "init-1", "state": "exited", "restarts": 0, "oom_killed": False, "exit_code": 0},
        {
            "name": "migrate-1",
            "state": "exited",
            "restarts": 0,
            "oom_killed": False,
            "exit_code": 1,
        },
    ]

    report = run_smoke(smoke)

    assert report.problems == ["migrate-1 exited with status 1"]


def test_the_prefix_reaches_the_project_the_smoke_run_makes(
    smoke: ModuleType, fake_docker: type[FakeProject], monkeypatch: pytest.MonkeyPatch
) -> None:
    """smoke() passes it on, so that down removes the project it started."""
    names: list[str] = []
    real = smoke.Project
    monkeypatch.setattr(
        smoke,
        "Project",
        lambda recipe, directory, prefix="noust-smoke-": (
            names.append(prefix) or real(recipe, directory, prefix)
        ),
    )
    monkeypatch.setattr(smoke, "ask", lambda url, *, follow, headers=None: (200, ""))
    monkeypatch.setattr(real, "run", FakeProject.run)
    monkeypatch.setattr(real, "inspect", FakeProject.inspect)

    run_smoke(smoke, prefix="noust-test-cat-")

    assert names == ["noust-test-cat-"]


def test_a_stop_signal_runs_the_cleanup_instead_of_leaving_the_stack_behind(
    smoke: ModuleType,
) -> None:
    """`timeout` sends SIGTERM: without a handler the project and its volumes stay on the machine."""
    import signal

    previous = signal.getsignal(signal.SIGTERM)
    try:
        smoke.stop_on_sigterm()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(SystemExit) as stopped:
            handler(signal.SIGTERM, None)
        assert stopped.value.code == 143
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_a_port_the_previous_recipe_only_left_in_time_wait_is_free(smoke: ModuleType) -> None:
    """The finding of the first real batch: every recipe after the first was refused its port.

    The previous stack's health check had closed its connections from the server side, which
    leaves the port in TIME_WAIT; a bind without SO_REUSEADDR refuses that, a listener is what
    must be refused.
    """
    import socket

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    assert smoke.port_is_free(port) is False

    client = socket.create_connection(("127.0.0.1", port))
    served, _ = listener.accept()
    served.close()  # the server closes first: its side of the connection waits
    client.close()
    listener.close()

    assert smoke.port_is_free(port) is True


def test_the_gate_waits_as_long_as_the_recipe_s_own_timeout_unless_told_otherwise(
    smoke: ModuleType, fake_docker: type[FakeProject], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stack that takes ten minutes to migrate is judged as a deployment would judge it."""
    import dataclasses

    from noust.recipes.model import RecipeHealth

    clock = [0.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        smoke.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    asked: list[float] = []

    def refuse(url: str, *, follow: bool, headers: Any = None) -> tuple[int, str]:
        asked.append(clock[0])
        return 503, ""

    monkeypatch.setattr(smoke, "ask", refuse)
    slow = dataclasses.replace(
        get_recipe("vaultwarden"), health=RecipeHealth(path="/alive", expect="200", timeout=20)
    )

    def run(wait: int | None) -> Any:
        asked.clear()
        clock[0] = 0.0
        return smoke.smoke(
            slow,
            port=18080,
            wait=wait,
            given={},
            extra=[],
            domain="vault.example.com",
            ssl=True,
            keep=False,
        )

    run(None)
    assert asked == [0.0, 5.0, 10.0, 15.0]
    run(10)
    assert asked == [0.0, 5.0]
    no_timeout = dataclasses.replace(slow, health=RecipeHealth(path="/alive", expect="200"))
    slow = no_timeout
    run(None)
    assert len(asked) == 60


def test_one_list_of_variables_serves_a_batch_and_each_recipe_takes_the_ones_it_declares(
    smoke: ModuleType, tmp_path: Path
) -> None:
    """A batch passes the identity provider's addresses for Outline; Vaultwarden ignores them."""
    recipe = get_recipe("vaultwarden")
    shared = {"OIDC_AUTH_URI": "https://idp.example.com/auth", "SIGNUPS_ALLOWED": "false"}

    values = smoke.write_project(
        recipe, tmp_path, port=18080, domain="vault.example.com", ssl=True, given=shared
    )

    assert "OIDC_AUTH_URI" not in values and values["SIGNUPS_ALLOWED"] == "false"
