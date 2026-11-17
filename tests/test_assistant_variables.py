# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What of an application's variables reaches the provider: their names, never their values (item 171).

The documentation says the values "are removed", and that has to be true whichever route read them. A value that
nobody knows is a secret and that has no shape of a credential (a note, a partner's code, a date) cannot be told
from any other text, so the one thing that holds is that a read that carries variables is given to the model as
names. That is decided for the route, not for the tool it was asked through, and a test walks the API to find the
routes that carry variables, so one added tomorrow is not left out.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.core.assistant.masking import server_secret_values
from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.deploy_keys import KEYS_FOLDER, DeployKeyStore
from noust.core.fs import RealFileSystem
from noust.core.registry_store import RegistryStore, secret_name_for
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.web.assistant.toolbox import VARIABLE_READS, Reach, Toolbox
from tests.support.assistant_lab import KEY, SHOP, Lab, build_lab
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets

OPENAPI: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[1] / "panel" / "openapi.json").read_text()
)
HELD = (
    frozenset(
        str(operation["x-noust-permission"])
        for item in OPENAPI["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict) and operation.get("x-noust-permission")
    )
    - ASSISTANT_FLOOR
)

PLAIN = ("the-merger-closes-on-friday", "zx81-blue-falcon", "hunter2-hunter2")


#: What has variables in its answer and is not what a server holds: the variables a recipe of the catalogue
#: asks for (a name, a label, an example), the same for everybody and written by whoever wrote the recipe.
NOT_A_SERVER_S = frozenset({("GET", "/api/recipes/{name}")})


def carries_variables() -> list[tuple[str, str]]:
    """Every ``GET`` of the API whose answer has the variables of a server in it, by what its schema says."""
    components = OPENAPI["components"]["schemas"]
    found = []
    for template, item in OPENAPI["paths"].items():
        operation = item.get("get")
        if not isinstance(operation, dict):
            continue
        answer = (
            operation["responses"].get("200", {}).get("content", {}).get("application/json", {})
        ).get("schema", {})
        resolved = components.get(answer.get("$ref", "").rsplit("/", 1)[-1], answer)
        text_file = "text/plain" in operation["responses"].get("200", {}).get("content", {})
        if "variables" in (resolved.get("properties") or {}) or (text_file and "/env" in template):
            found.append(("GET", template))
    return sorted(set(found) - NOT_A_SERVER_S)


@pytest.fixture(scope="module")
def box() -> Toolbox:
    return Toolbox(OPENAPI, token_permissions=HELD, person_permissions=HELD, reach=Reach())


def prepare(box: Toolbox, template: str) -> Any:
    import re

    names = re.findall(r"\{([^{}]+)\}", template)
    return box.prepare(
        "call_operation",
        {"method": "GET", "path": template, "parameters": dict.fromkeys(names, "x")},
    )


class TestEveryReadThatCarriesVariablesIsGivenAsNames:
    def test_the_api_has_the_routes_the_test_expects_to_find(self) -> None:
        assert len(carries_variables()) >= 8

    @pytest.mark.parametrize(("method", "template"), carries_variables(), ids=lambda v: str(v))
    def test_the_route_is_one_of_those_given_as_names(
        self, box: Toolbox, method: str, template: str
    ) -> None:
        assert (method, template) in VARIABLE_READS
        assert prepare(box, template).shape is not None

    def test_nothing_is_listed_that_the_api_does_not_have(self) -> None:
        assert VARIABLE_READS <= {
            (method.upper(), template)
            for template, item in OPENAPI["paths"].items()
            for method in item
        }

    def test_an_environment_of_an_application_loses_its_values_and_keeps_its_names(
        self, box: Toolbox
    ) -> None:
        answer = {
            "domain": SHOP,
            "unmasked": False,
            "variables": {
                "DB_PASSWORD": "********",
                "INTERNAL_NOTE": PLAIN[0],
                "PARTNER": PLAIN[1],
            },
            "secrets": {"DB_PASSWORD": {"secret": True}},
            "comments": {"INTERNAL_NOTE": "ask the lawyers"},
            "predefined": {"NOUST_URL": "https://shop.example.com"},
            "references": {"PARTNER": "{{ partner }}"},
        }

        shaped = prepare(box, "/api/apps/{domain}/env").shape(answer)

        text = json.dumps(shaped)
        assert not any(value in text for value in PLAIN)
        assert "ask the lawyers" not in text
        names = {item["name"]: item["secret"] for item in shaped["variables"]}
        assert names == {"DB_PASSWORD": True, "INTERNAL_NOTE": False, "PARTNER": False}

    @pytest.mark.parametrize(
        "template",
        [
            "/api/teams/{team}/variables",
            "/api/teams/{team}/projects/{project}/variables",
            "/api/teams/{team}/projects/{project}/environments/{environment}/variables",
            "/api/variables/server",
            "/api/apps/{domain}/previews/variables",
            "/api/apps/{domain}/compose/variables",
        ],
    )
    def test_a_list_of_variables_of_a_team_a_project_or_a_preview_is_as_names_too(
        self, box: Toolbox, template: str
    ) -> None:
        answer = {
            "label": "Acme",
            "scope": "team",
            "variables": [
                {
                    "name": "REGION",
                    "value": PLAIN[0],
                    "secrecy": {"secret": False},
                    "default": PLAIN[1],
                },
                {"name": "TOKEN_X", "value": "********", "secrecy": {"secret": True}},
            ],
        }

        shaped = prepare(box, template).shape(answer)

        text = json.dumps(shaped)
        assert not any(value in text for value in PLAIN)
        assert [item["name"] for item in shaped["variables"]] == ["REGION", "TOKEN_X"]

    def test_the_text_of_an_env_file_is_as_names_too(self, box: Toolbox) -> None:
        text = f"# a note\nDB_PASSWORD={PLAIN[2]}\nexport INTERNAL_NOTE={PLAIN[0]}\n\nPARTNER_CODE={PLAIN[1]}\n"

        shaped = prepare(box, "/api/apps/{domain}/env/file").shape(text)

        assert not any(value in json.dumps(shaped) for value in PLAIN)
        assert [item["name"] for item in shaped["variables"]] == [
            "DB_PASSWORD",
            "INTERNAL_NOTE",
            "PARTNER_CODE",
        ]

    def test_it_does_not_offer_to_unmask_or_to_download(self, box: Toolbox) -> None:
        from noust.web.assistant.toolbox import ToolProblem

        for template, name in (
            ("/api/apps/{domain}/env/file", "unmask"),
            ("/api/apps/{domain}/env/file", "download"),
            ("/api/teams/{team}/variables", "unmasked"),
        ):
            with pytest.raises(ToolProblem, match="Unknown argument"):
                import re

                parameters = dict.fromkeys(re.findall(r"\{([^{}]+)\}", template), "x")
                box.prepare(
                    "call_operation",
                    {
                        "method": "GET",
                        "path": template,
                        "parameters": {**parameters, name: True},
                    },
                )


class TestTheModelIsSentNoValue:
    @pytest.fixture(autouse=True)
    def cheap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
        monkeypatch.setattr(passwords, "_dummy_hash", None)

    @pytest.fixture(autouse=True)
    def real_config(self, real_config: Any) -> Any:
        return real_config

    @pytest.fixture
    def model(self) -> Iterator[ScriptedModel]:
        with ScriptedModel(models=["tiny"], api_key=KEY) as scripted:
            yield scripted

    @pytest.fixture
    def lab(
        self, sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel
    ) -> Iterator[Lab]:
        yield from build_lab(sandbox, monkeypatch, model)

    @pytest.mark.parametrize("route", ["env", "env/file"])
    def test_variables_that_look_like_nothing_do_not_reach_the_provider(
        self, lab: Lab, route: str
    ) -> None:
        lab.configure()
        lab.write_env(
            SHOP,
            f"DB_PASSWORD={PLAIN[2]}\nINTERNAL_NOTE={PLAIN[0]}\nPARTNER_CODE={PLAIN[1]}\n",
        )
        lab.model.script(
            Reply.call(
                "call_operation",
                {
                    "method": "GET",
                    "path": "/api/apps/{domain}/" + route,
                    "parameters": {"domain": SHOP},
                },
            ),
            Reply.say("I read the names."),
        )

        turn = lab.ask(lab.conversation(), "what is configured for the shop?")

        sent = " ".join(str(r.body) for r in lab.requests())
        assert [s["state"] for s in turn["steps"]] == ["done"]
        for value in PLAIN:
            assert value not in sent
        # What it may read are the names: the person asked what is configured.
        assert "INTERNAL_NOTE" in sent and "PARTNER_CODE" in sent


class TestWhatTheMaskKnows:
    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[NoustStore]:
        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "noust.db")
        yield instance
        NoustStore.reset_instance()

    def test_the_private_half_of_a_deploy_key_and_the_password_of_a_registry_are_known(
        self, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secrets = SecretStore(root=tmp_path / "secrets", fs=RealFileSystem())
        private = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
            "QyNTUxOQAAACDplainlookingbytesnobodyknowsareasecret0123456789abcdefAAAA\n"
            "-----END OPENSSH PRIVATE KEY-----\n"
        )
        DeployKeyStore(store).create(
            name="shop-key",
            origin="generated",
            key_type="ssh-ed25519",
            fingerprint="SHA256:abc",
            public_key="ssh-ed25519 AAAA shop",
            created_by="test",
        )
        secrets.write(f"{KEYS_FOLDER}/shop-key/key", private)
        RegistryStore(store).add("hub", "custom", "registry.example.com", "robot", created_by="t")
        secrets.write(secret_name_for("hub"), "r3g1stry-p4ssw0rd-nobody-guesses")
        monkeypatch.setattr("noust.core.secrets.SecretStore", lambda *a, **k: secrets)

        values = server_secret_values()

        assert private.strip() in values or private in values
        assert "r3g1stry-p4ssw0rd-nobody-guesses" in values
