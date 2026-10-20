# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A recipe with more than one public address (checklist D-31: Typebot's builder and viewer).

A stack whose services must answer on names of their own declares ``entrypoints``: each names the
variable the operator gives the address in (a required variable, so the wizard, the command line
and the API all ask for it) and the loopback port variable the service publishes on. The install
chooses a free port for each, adds the address to the application as an alias proxied to it, and
the application's certificate covers it.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DomainError, ValidationError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.deployers import domains
from noust.recipes import RecipeError, parse_recipe
from noust.recipes import deploy as deploy_module
from noust.recipes.deploy import finish_recipe, plan_recipe
from noust.recipes.model import Entrypoint
from tests.recipes.factory import DIGEST, compose_recipe, use_demo_assets

DOMAIN = "bots.example.com"
VIEWER = "view.example.com"
LOG = Logger(verbose=False)

TWO_SERVICES = f"""
services:
  builder:
    image: ghcr.io/o/builder:1.2.3@{DIGEST}
    restart: unless-stopped
    environment:
      VIEWER_URL: "${{VIEWER_URL}}"
    ports:
      - "127.0.0.1:${{PORT}}:3000"
  viewer:
    image: ghcr.io/o/viewer:1.2.3@{DIGEST}
    restart: unless-stopped
    ports:
      - "127.0.0.1:${{VIEWER_PORT}}:3001"
"""

VARIABLES = {
    "SCHEME": {"from": "scheme"},
    "VIEWER_DOMAIN": {"required": True, "description": "Where visitors open the bots."},
    "VIEWER_PORT": {"default": "3101", "description": "The viewer's port on 127.0.0.1."},
    "VIEWER_URL": {"value": "${SCHEME}://${VIEWER_DOMAIN}"},
}
ENTRYPOINTS = {
    "viewer": {
        "title": "Viewer",
        "description": "Where visitors open and answer the bots you publish.",
        "domain_variable": "VIEWER_DOMAIN",
        "port_variable": "VIEWER_PORT",
    }
}


def two_address_recipe(**overrides: Any) -> dict[str, Any]:
    """A Compose recipe with a builder on the application's address and a viewer on another."""
    data = compose_recipe(
        compose=TWO_SERVICES, variables=dict(VARIABLES), entrypoints=dict(ENTRYPOINTS)
    )
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the recipe named ``demo`` use the assets of a shipped recipe."""
    use_demo_assets(monkeypatch)


def use(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any]) -> None:
    """Make ``demo`` a recipe of the catalogue for the plan."""
    recipe = parse_recipe("demo", data)
    monkeypatch.setattr(deploy_module, "get_recipe", lambda name: recipe)


# ---------------------------------------------------------------------------
# The format
# ---------------------------------------------------------------------------


def test_a_recipe_names_the_variables_that_carry_each_extra_address() -> None:
    """The record keeps the name, the words the operator reads and the two variables."""
    recipe = parse_recipe("demo", two_address_recipe())

    assert recipe.entrypoints == (
        Entrypoint(
            name="viewer",
            title="Viewer",
            description="Where visitors open and answer the bots you publish.",
            domain_variable="VIEWER_DOMAIN",
            port_variable="VIEWER_PORT",
        ),
    )
    assert recipe.to_dict()["entrypoints"] == [
        {
            "name": "viewer",
            "title": "Viewer",
            "description": "Where visitors open and answer the bots you publish.",
            "domain_variable": "VIEWER_DOMAIN",
            "port_variable": "VIEWER_PORT",
        }
    ]


def test_a_recipe_without_entrypoints_has_none() -> None:
    """Nothing changes for the ones that serve one address."""
    recipe = parse_recipe("demo", compose_recipe())

    assert recipe.entrypoints == () and recipe.to_dict()["entrypoints"] == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"viewer": "not a mapping"}, "entrypoint viewer must be a mapping"),
        (
            {"viewer": {**ENTRYPOINTS["viewer"], "colour": "red"}},
            "entrypoint viewer has unknown keys: colour",
        ),
        (
            {"viewer": {**ENTRYPOINTS["viewer"], "domain_variable": "NOPE"}},
            "entrypoint viewer: domain_variable NOPE is not a variable of the recipe",
        ),
        (
            {"viewer": {**ENTRYPOINTS["viewer"], "domain_variable": "SCHEME"}},
            "entrypoint viewer: SCHEME must be a required variable",
        ),
        (
            {"viewer": {**ENTRYPOINTS["viewer"], "port_variable": "VIEWER_DOMAIN"}},
            "entrypoint viewer: VIEWER_DOMAIN is not a loopback port the document publishes",
        ),
        (
            {"Viewer!": ENTRYPOINTS["viewer"]},
            "entrypoint name 'Viewer!' must be lower-case letters, digits and dashes",
        ),
        ([ENTRYPOINTS["viewer"]], "entrypoints must be a mapping"),
        ({}, "entrypoints needs at least one entry"),
    ],
)
def test_an_entrypoint_that_cannot_work_is_an_error_naming_it(change: Any, message: str) -> None:
    """Every mistake is reported when the recipe loads, not when somebody installs it."""
    with pytest.raises(RecipeError, match=message):
        parse_recipe("demo", two_address_recipe(entrypoints=change))


def test_two_entrypoints_cannot_share_a_variable() -> None:
    """One variable holds one address and one port."""
    variables = {**VARIABLES, "OTHER_PORT": {"default": "3102"}}
    compose = TWO_SERVICES + (
        "  other:\n"
        f"    image: ghcr.io/o/other:1.2.3@{DIGEST}\n"
        "    restart: unless-stopped\n"
        "    ports:\n"
        '      - "127.0.0.1:${OTHER_PORT}:3002"\n'
    )
    shared = {
        "viewer": ENTRYPOINTS["viewer"],
        "other": {**ENTRYPOINTS["viewer"], "port_variable": "OTHER_PORT"},
    }

    with pytest.raises(RecipeError, match="VIEWER_DOMAIN is the address of two entrypoints"):
        parse_recipe(
            "demo", two_address_recipe(compose=compose, variables=variables, entrypoints=shared)
        )


def test_an_entrypoint_belongs_to_a_compose_recipe_only() -> None:
    """A native recipe has one process behind one address."""
    from tests.recipes.factory import native_recipe

    with pytest.raises(RecipeError, match="unknown keys: entrypoints"):
        parse_recipe("demo", native_recipe(entrypoints=dict(ENTRYPOINTS)))


def test_the_catalogue_allows_a_few_entrypoints_not_a_crowd() -> None:
    """Four extra addresses at most: each needs a DNS record and a place in the certificate."""
    many = {f"e{i}": ENTRYPOINTS["viewer"] for i in range(5)}

    with pytest.raises(RecipeError, match="at most 4 entrypoints"):
        parse_recipe("demo", two_address_recipe(entrypoints=many))


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def free_ports(
    monkeypatch: pytest.MonkeyPatch, *, busy: Collection[int] = ()
) -> list[dict[str, Any]]:
    """Make a port free unless it is in ``busy`` and record what the picker was asked."""
    asked: list[dict[str, Any]] = []

    def pick(
        start: int = 3000,
        end: int = 9000,
        preferred: int | None = None,
        exclude: Collection[int] = (),
    ) -> int | None:
        asked.append({"start": start, "end": end, "preferred": preferred, "exclude": set(exclude)})
        for port in [preferred, *range(start, end)]:
            if port is not None and port not in exclude and port not in busy:
                return port
        return None

    monkeypatch.setattr(deploy_module, "find_available_port", pick)
    return asked


def test_the_plan_gives_each_extra_address_a_free_port_and_the_variables_their_values(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The port variable is written to the stack's .env, and the URL variable composes the address."""
    use(monkeypatch, two_address_recipe())
    asked = free_ports(monkeypatch, busy={3101})
    recipe_store.create_app(App(domain="other.example.com", app_type="nodejs", port=3102))

    plan = plan_recipe(
        "demo", DOMAIN, port=8123, ssl=True, env_overrides={"VIEWER_DOMAIN": VIEWER}, logger=LOG
    )

    assert [(e.name, e.domain, e.port) for e in plan.entrypoints] == [("viewer", VIEWER, 3103)]
    assert plan.env_vars["VIEWER_PORT"] == "3103"
    assert plan.env_vars["VIEWER_URL"] == f"https://{VIEWER}"
    assert asked[0]["preferred"] == 3101 and {8123, 3102} <= asked[0]["exclude"]


def test_a_port_the_operator_chose_is_kept(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An override of the port variable is the operator's decision."""
    use(monkeypatch, two_address_recipe())
    asked = free_ports(monkeypatch)

    plan = plan_recipe(
        "demo",
        DOMAIN,
        port=8123,
        ssl=False,
        env_overrides={"VIEWER_DOMAIN": VIEWER, "VIEWER_PORT": "4500"},
        logger=LOG,
    )

    assert plan.entrypoints[0].port == 4500 and plan.env_vars["VIEWER_PORT"] == "4500"
    assert plan.env_vars["VIEWER_URL"] == f"http://{VIEWER}"
    assert asked == []


def test_an_install_without_the_address_stops_before_anything_exists(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The domain variable is required, so the wizard and the command ask for it like any other."""
    use(monkeypatch, two_address_recipe())

    with pytest.raises(RecipeError, match="needs a value for VIEWER_DOMAIN"):
        plan_recipe("demo", DOMAIN, port=8123, ssl=True, logger=LOG)


@pytest.mark.parametrize(
    ("given", "message"),
    [
        (DOMAIN, "is the application's own address"),
        ("Not A Domain", r"VIEWER_DOMAIN \(viewer\) is not usable"),
    ],
)
def test_an_address_that_is_the_application_s_own_or_not_a_domain_is_refused(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch, given: str, message: str
) -> None:
    """A second address of the same name, or something that is not a name, is an input error."""
    use(monkeypatch, two_address_recipe())
    free_ports(monkeypatch)

    with pytest.raises((RecipeError, ValidationError), match=message):
        plan_recipe(
            "demo", DOMAIN, port=8123, ssl=True, env_overrides={"VIEWER_DOMAIN": given}, logger=LOG
        )


def test_an_address_another_application_answers_on_is_refused_before_anything_exists(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store refuses it at the end; saying so at the start spares a deployment."""
    use(monkeypatch, two_address_recipe())
    free_ports(monkeypatch)
    recipe_store.create_app(App(domain=VIEWER, app_type="nodejs", port=3200))

    with pytest.raises(RecipeError, match=f"{VIEWER} already belongs to"):
        plan_recipe(
            "demo", DOMAIN, port=8123, ssl=True, env_overrides={"VIEWER_DOMAIN": VIEWER}, logger=LOG
        )


def test_a_server_with_no_free_port_near_the_default_says_so(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No port is a reason to stop, with what to do."""
    use(monkeypatch, two_address_recipe())
    monkeypatch.setattr(deploy_module, "find_available_port", lambda **_: None)

    with pytest.raises(RecipeError, match="No free port for the viewer"):
        plan_recipe(
            "demo", DOMAIN, port=8123, ssl=True, env_overrides={"VIEWER_DOMAIN": VIEWER}, logger=LOG
        )


# ---------------------------------------------------------------------------
# Finishing: the address becomes an alias of the application
# ---------------------------------------------------------------------------


def test_finishing_adds_each_extra_address_as_an_alias_proxied_to_its_port(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the one implementation of "the names an application answers on"."""
    use(monkeypatch, two_address_recipe())
    free_ports(monkeypatch)
    plan = plan_recipe(
        "demo", DOMAIN, port=8123, ssl=True, env_overrides={"VIEWER_DOMAIN": VIEWER}, logger=LOG
    )
    recipe_store.create_app(App(domain=DOMAIN, app_type="docker-compose", port=8123))
    calls: list[tuple[Any, ...]] = []

    def add(app: str, name: str, kind: str = "alias", **options: Any) -> None:
        calls.append((app, name, kind, options.get("port"), options.get("issue_cert", True)))

    monkeypatch.setattr(domains, "add_domain", add)

    notes = finish_recipe(plan, logger=LOG, default_backups=False)

    assert calls == [(DOMAIN, VIEWER, "alias", 3101, True)]
    assert notes == list(plan.notes)


def test_an_address_that_cannot_be_added_is_a_note_with_the_command_that_repeats_it(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The application is deployed and works on its own address: the rest is said, never lost."""
    use(monkeypatch, two_address_recipe())
    free_ports(monkeypatch)
    plan = plan_recipe(
        "demo", DOMAIN, port=8123, ssl=True, env_overrides={"VIEWER_DOMAIN": VIEWER}, logger=LOG
    )
    recipe_store.create_app(App(domain=DOMAIN, app_type="docker-compose", port=8123))

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise DomainError("Another site of the web server already answers on view.example.com")

    monkeypatch.setattr(domains, "add_domain", refuse)

    notes = finish_recipe(plan, logger=LOG, default_backups=False)

    assert notes[: len(plan.notes)] == list(plan.notes)
    assert any(
        VIEWER in note
        and "already answers" in note
        and f"noust domain add {DOMAIN} {VIEWER} --port 3101" in note
        for note in notes[len(plan.notes) :]
    ), notes


def test_a_stack_that_failed_to_deploy_adds_no_address(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No application row, nothing to add an address to."""
    use(monkeypatch, two_address_recipe())
    free_ports(monkeypatch)
    plan = plan_recipe(
        "demo", DOMAIN, port=8123, ssl=True, env_overrides={"VIEWER_DOMAIN": VIEWER}, logger=LOG
    )
    called: list[str] = []
    monkeypatch.setattr(domains, "add_domain", lambda *a, **k: called.append("added"))

    assert finish_recipe(plan, logger=LOG, default_backups=False) == list(plan.notes)
    assert called == []


# ---------------------------------------------------------------------------
# The chokepoint: a name of a Compose stack may go to a port the stack publishes
# ---------------------------------------------------------------------------


def stack() -> App:
    """A Compose application."""
    return App(domain=DOMAIN, app_type="docker-compose", port=8123)


def test_a_name_of_a_stack_may_go_to_a_port_the_stack_publishes() -> None:
    """The check that kept every stack from having a name of its own port, kept for what it protects."""
    domains._check_port(stack(), "alias", 3101, published=frozenset({8123, 3101}))


def test_a_name_of_a_stack_cannot_go_to_a_port_it_does_not_publish() -> None:
    """Something else on the server may listen there: the proxy would show that to the world."""
    with pytest.raises(ValidationError, match="does not publish 3999"):
        domains._check_port(stack(), "alias", 3999, published=frozenset({8123, 3101}))


def test_a_stack_whose_ports_are_not_known_keeps_the_refusal() -> None:
    """Nothing says the port is the stack's: routes in noust.nginx.yaml are how it is done."""
    with pytest.raises(ValidationError, match=r"routes by noust\.nginx\.yaml"):
        domains._check_port(stack(), "alias", 3101)


def test_only_an_alias_has_a_port_of_its_own_in_a_stack_too() -> None:
    """A redirect has nothing to proxy."""
    with pytest.raises(DomainError, match="Only an alias"):
        domains._check_port(stack(), "redirect", 3101, published=frozenset({3101}))


# ---------------------------------------------------------------------------
# What the stack publishes, read the way Compose reads it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("127.0.0.1:${VIEWER_PORT}:3000", "127.0.0.1:3103:3000"),
        ("127.0.0.1:${VIEWER_PORT:-3101}:3000", "127.0.0.1:3103:3000"),
        ("127.0.0.1:${MISSING:-3101}:3000", "127.0.0.1:3101:3000"),
        ("127.0.0.1:${MISSING-3101}:3000", "127.0.0.1:3101:3000"),
        ("127.0.0.1:${EMPTY:-3101}:3000", "127.0.0.1:3101:3000"),
        ("127.0.0.1:${EMPTY-3101}:3000", "127.0.0.1::3000"),
        ("127.0.0.1:$VIEWER_PORT:3000", "127.0.0.1:3103:3000"),
        ("127.0.0.1:${MISSING}:3000", "127.0.0.1:${MISSING}:3000"),
        ("8080:80", "8080:80"),
    ],
)
def test_the_variables_of_a_port_are_filled_from_the_env_file_as_compose_does(
    text: str, expected: str
) -> None:
    """A recipe's install writes the chosen port to .env: that is the port the stack publishes."""
    from noust.deployers.helpers.compose_ports import interpolate

    assert interpolate(text, {"VIEWER_PORT": "3103", "EMPTY": ""}) == expected


def test_a_deployed_stack_lists_the_ports_its_env_file_gives(tmp_path: Any) -> None:
    """The ports a name of the stack may be sent to: the web service's and the viewer's."""
    from noust.deployers.docker_compose import DockerComposeDeployer

    root = tmp_path / "bots"
    root.mkdir()
    (root / "docker-compose.yml").write_text(
        TWO_SERVICES.replace(DIGEST, "0" * 64).replace("@sha256:", "@sha256:"), encoding="utf-8"
    )
    (root / ".env").write_text("PORT=8123\nVIEWER_PORT=3103\n", encoding="utf-8")
    deployer = DockerComposeDeployer()
    deployer.configure(DOMAIN, "https://example.com/bots.git", app_path=root)
    deployer.compose_path = root / "docker-compose.yml"

    assert deployer.published_host_ports() == frozenset({8123, 3103})
    (root / ".env").write_text("PORT=8123\n", encoding="utf-8")
    assert deployer.published_host_ports() == frozenset({8123})


class FakeSiteDeployer:
    """What domains.add_domain needs of a stack's deployer, without a web server."""

    def __init__(self, published: frozenset[int]) -> None:
        self.published = published
        self.rendered = 0

    def published_host_ports(self) -> frozenset[int]:
        return self.published

    def refresh_site(self, *, with_ssl: bool) -> None:
        self.rendered += 1

    def webserver_manager(self) -> Any:
        class Manager:
            def site_exists(self, name: str) -> bool:
                return False

            def config_path(self, name: str) -> Any:
                from pathlib import Path

                return Path(f"/etc/nginx/sites-available/{name}")

        return Manager()


def test_add_domain_sends_a_name_of_a_stack_to_the_port_its_recipe_published(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row keeps the port, the site is rendered, and a port the stack does not publish is refused."""
    recipe_store.create_app(App(domain=DOMAIN, app_type="docker-compose", port=8123))
    deployer = FakeSiteDeployer(frozenset({8123, 3101}))
    monkeypatch.setattr(domains, "_site_deployer", lambda app, verbose=False: deployer)
    monkeypatch.setattr(domains, "find_overlaps", lambda *args, **kwargs: [])
    monkeypatch.setattr(domains, "_serves_tls", lambda app, deployer: False)
    monkeypatch.setattr(domains, "_adopt_served_names", lambda *args, **kwargs: ())
    monkeypatch.setattr(domains, "_point_dns", lambda *args, **kwargs: ())

    domains.add_domain(DOMAIN, VIEWER, "alias", port=3101, dns=False)

    assert {record.domain: record.port for record in recipe_store.list_domains(DOMAIN)} == {
        DOMAIN: None,
        VIEWER: 3101,
    }
    assert deployer.rendered == 1
    with pytest.raises(ValidationError, match="does not publish 3999"):
        domains.add_domain(DOMAIN, "other.example.com", "alias", port=3999, dns=False)
    assert [record.domain for record in recipe_store.list_domains(DOMAIN)] == [DOMAIN, VIEWER]


@pytest.fixture(scope="module")
def smoke() -> Any:
    """scripts/recipe_smoke.py, loaded as a module."""
    import importlib.util
    import sys

    script = Path(__file__).resolve().parents[2] / "scripts" / "recipe_smoke.py"
    spec = importlib.util.spec_from_file_location("recipe_smoke_entrypoints", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_smoke_entrypoints"] = module
    spec.loader.exec_module(module)
    return module


def test_the_extra_address_of_a_recipe_with_several_is_a_name_under_the_domain(
    smoke: Any, tmp_path: Path
) -> None:
    """A viewer's address must be a domain, not the e-mail address a required variable gets."""
    recipe = parse_recipe("demo", two_address_recipe())

    values = smoke.write_project(
        recipe, tmp_path, port=18080, domain="bots.example.com", ssl=True, given={}
    )

    assert values["VIEWER_DOMAIN"] == "viewer.bots.example.com"
    assert values["VIEWER_URL"] == "https://viewer.bots.example.com"
