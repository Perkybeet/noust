# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A hub that was told to deploy containers here: the setting, and what the table of functions says then.

The container of a NAS is a hub because its image has no nginx, no systemd and no Docker client; it
was never a hub because Docker is unwelcome. With the machine's Docker at hand it can deploy
containers, and it does so only when asked twice, on purpose and outside the console: the socket
mounted, and ``central.deploy_here: containers`` (``NOUST_DEPLOY_HERE``). Without the second, a hub
that happens to have Docker behaves exactly as it did, which these tests hold function by function.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from noust import central
from noust.central import features
from noust.central.features import FEATURES, Need
from noust.central.machine import MachineFacts
from noust.core.config import Config
from noust.core.exceptions import ConfigError
from noust.core.runner import FakeRunner
from noust.core.server_text import CATALOG, say
from noust.core.store import NoustStore
from noust.web.server import create_app
from tests.support.web_auth import make_config
from tests.support.web_central import elevate, sign_in

#: A server: everything.
EVERYTHING = MachineFacts(systemd=True, web_server=True, docker=True, root=True, journal=True)

#: The container of a NAS with the machine's Docker mounted: Docker, and nothing else a server has.
NAS_WITH_DOCKER = MachineFacts(
    systemd=False, web_server=False, docker=True, root=False, journal=False
)

#: The same container without the socket.
NAS_WITHOUT_DOCKER = MachineFacts(
    systemd=False, web_server=False, docker=False, root=False, journal=False
)

#: What a NAS that deploys containers does: what asks only for Docker, in the words of the owner's
#: request (containers and stacks, Compose recipes, database engines in containers, copies of
#: volumes, and what is done to an application whatever it runs as).
DEPLOYS_CONTAINERS = {
    "applications",
    "container_deploy",
    "releases",
    "rollbacks",
    "recipes",
    "application_environments",
    "application_backups",
    "database_instances",
    "volume_backups",
    # The connector of a tunnel is one more container Docker keeps alive.
    "tunnels",
}

#: What a NAS that deploys containers does and asks nothing of the machine for: a record and an account
#: at a DNS provider are calls to its API (a tunnel's names are made through them).
ASKS_NOTHING = {"dns_records", "dns_accounts"}

#: What stays on the server's side of the line, with the sentence the console says for each.
NEEDS_SYSTEMD_OR_A_WEB_SERVER = {
    "native_deploy",
    "preview_environments",
    "importing_applications",
    "process_monitor",
    "server_health",
    "server_setup",
    "domains",
    "sites",
    "certificates",
    "console_exposure",
    "status_page",
    "published_ports",
    "noust_dns",
    "databases",
    "public_database_access",
    "database_backups",
    "point_in_time_recovery",
    "restoring_a_server",
    "moving_a_server",
    "checking_a_moved_server",
    "scheduled_jobs",
    "services",
    "software",
    "log_destinations",
    "application_diagnosis",
}


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A configuration read from an empty file in the test's directory."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    NoustStore.reset_instance()
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


def configure(monkeypatch: pytest.MonkeyPatch, *, role: str, deploy_here: str = "") -> None:
    """Set the role and the consent the way the container's environment does."""
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", role)
    if deploy_here:
        monkeypatch.setenv("NOUST_DEPLOY_HERE", deploy_here)
    Config.reset_instance()


class TestTheSetting:
    def test_it_is_off_unless_somebody_turns_it_on(self) -> None:
        assert central.deploy_here() == ""
        assert not central.is_container_host()

    def test_the_environment_of_the_container_turns_it_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub", deploy_here="containers")

        assert central.deploy_here() == "containers"
        assert central.is_container_host()

    def test_it_can_be_written_in_the_configuration_by_whoever_has_a_shell(self) -> None:
        Config().set("central.deploy_here", "Containers")

        assert Config().get("central.deploy_here") == "containers"

    @pytest.mark.parametrize("value", ["", "off", "OFF"])
    def test_off_is_off_however_it_is_written(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        Config.reset_instance()
        Config().set("central.deploy_here", "containers")

        Config().set("central.deploy_here", value)

        assert central.deploy_here() == ""
        assert not central.is_container_host()

    @pytest.mark.parametrize("value", ["everything", "docker", "native", "true", "1"])
    def test_anything_else_is_refused_naming_what_is_allowed(self, value: str) -> None:
        with pytest.raises(ConfigError, match="containers"):
            Config().set("central.deploy_here", value)

    def test_a_mistyped_environment_is_found_when_it_is_read_not_guessed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_DEPLOY_HERE", "contenedores")
        Config.reset_instance()

        with pytest.raises(ConfigError, match=r"central\.deploy_here"):
            central.deploy_here()

    def test_a_server_deploys_here_already_and_the_setting_changes_nothing_for_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="server", deploy_here="containers")

        assert central.deploy_here() == "containers"
        assert not central.is_container_host()
        assert features.published(
            role="server", facts=EVERYTHING, deploy_here="containers"
        ) == features.published(role="server", facts=EVERYTHING)

    def test_a_hub_is_a_container_host_only_when_the_setting_is_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub")
        assert not central.is_container_host()

        configure(monkeypatch, role="hub", deploy_here="containers")
        assert central.is_container_host()


class TestAHubThatWasNotAsksNothingNew:
    """A hub that already exists does not change when it is updated, Docker or not."""

    @pytest.mark.parametrize("facts", [EVERYTHING, NAS_WITH_DOCKER, NAS_WITHOUT_DOCKER])
    def test_the_answer_of_every_function_is_the_one_it_always_was(
        self, facts: MachineFacts
    ) -> None:
        before = features.published(role="hub", facts=facts)

        after = features.published(role="hub", facts=facts, deploy_here="")

        assert after == before
        for key, feature in FEATURES.items():
            if Need.ROLE in feature.needs:
                assert after[key]["available"] is False, key
                assert after[key]["reason"]["code"] == "features.hub_role", key

    def test_the_guard_refuses_what_it_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configure(monkeypatch, role="hub")

        for key in ("container_deploy", "volume_backups", "applications"):
            with pytest.raises(central.RoleError) as caught:
                central.require_server_role(key)
            assert "which is a hub" in caught.value.message, key


class TestWhatAContainerHostDoes:
    def published(self, facts: MachineFacts) -> dict[str, dict[str, Any]]:
        return features.published(role="hub", facts=facts, deploy_here="containers")

    def test_what_asks_only_for_docker_is_offered_when_there_is_docker(self) -> None:
        said = self.published(NAS_WITH_DOCKER)

        for key in DEPLOYS_CONTAINERS:
            assert said[key] == {"available": True, "reason": None}, key

    def test_without_the_socket_the_same_functions_say_it_in_the_words_of_the_doctor(self) -> None:
        said = self.published(NAS_WITHOUT_DOCKER)

        for key in DEPLOYS_CONTAINERS:
            assert said[key]["available"] is False, key
            assert said[key]["reason"]["code"] == "docker.engine.missing", key

    def test_what_needs_systemd_or_a_web_server_stays_where_it_was_with_a_sentence_that_says_so(
        self,
    ) -> None:
        said = self.published(NAS_WITH_DOCKER)

        for key in NEEDS_SYSTEMD_OR_A_WEB_SERVER:
            assert said[key]["available"] is False, key
            reason = said[key]["reason"]
            assert reason["code"] == "features.containers_only", key
            assert reason["params"] == {"feature": FEATURES[key].label}, key
            assert reason["text"] and reason["hint"], key

    def test_the_line_is_drawn_in_one_place_and_leaves_nothing_out(self) -> None:
        said = self.published(NAS_WITH_DOCKER)
        rolled = {key for key, feature in FEATURES.items() if Need.ROLE in feature.needs}

        assert rolled == DEPLOYS_CONTAINERS | ASKS_NOTHING | NEEDS_SYSTEMD_OR_A_WEB_SERVER
        assert {
            key for key in rolled if said[key]["available"]
        } == DEPLOYS_CONTAINERS | ASKS_NOTHING
        # What asks nothing is there without the socket too.
        without = self.published(NAS_WITHOUT_DOCKER)
        assert {key for key in rolled if without[key]["available"]} == ASKS_NOTHING

    def test_the_machines_own_pages_are_what_they_were(self) -> None:
        said = self.published(NAS_WITH_DOCKER)
        hub = features.published(role="hub", facts=NAS_WITH_DOCKER)

        for key, feature in FEATURES.items():
            if Need.ROLE not in feature.needs:
                assert said[key] == hub[key], key

    def test_the_docker_zone_is_a_function_of_the_table_and_needs_only_docker(self) -> None:
        zone = FEATURES["docker_zone"]

        assert zone.needs == (Need.DOCKER,)
        assert not zone.guarded
        for role, deploy_here in (("server", ""), ("hub", ""), ("hub", "containers")):
            answer = features.availability(
                "docker_zone", role=role, facts=NAS_WITH_DOCKER, deploy_here=deploy_here
            )
            assert answer.available, (role, deploy_here)
            missing = features.availability(
                "docker_zone", role=role, facts=NAS_WITHOUT_DOCKER, deploy_here=deploy_here
            )
            assert not missing.available
            assert missing.reason is not None
            assert missing.reason.code == "docker.engine.missing"

    def test_what_a_container_host_asks_of_the_machine_is_docker_and_nothing_else(self) -> None:
        for key, feature in FEATURES.items():
            if feature.on_containers is None:
                continue
            assert Need.ROLE in feature.needs, (
                f"{key} lists what a container host asks and needs no role"
            )
            assert feature.on_containers == (() if key in ASKS_NOTHING else (Need.DOCKER,)), key

    def test_a_server_keeps_asking_for_what_it_asked(self) -> None:
        assert FEATURES["container_deploy"].needs == (
            Need.ROLE,
            Need.SYSTEMD,
            Need.DOCKER,
            Need.ROOT,
        )
        assert (
            features.availability(
                "container_deploy", role="server", facts=NAS_WITH_DOCKER, deploy_here="containers"
            ).reason
            == features.availability(
                "container_deploy", role="server", facts=NAS_WITH_DOCKER
            ).reason
        )

    def test_every_sentence_it_can_say_is_in_the_catalog(self) -> None:
        for facts in (NAS_WITH_DOCKER, NAS_WITHOUT_DOCKER):
            for entry in self.published(facts).values():
                if entry["reason"] is not None:
                    assert entry["reason"]["code"] in CATALOG

    def test_the_refusal_says_what_this_central_does_and_what_to_do_instead(self) -> None:
        sentence = say("features.containers_only", feature="Domains")

        assert sentence.text == (
            "Domains: not available here, which deploys containers and nothing else"
        )
        assert sentence.hint is not None
        assert "central.deploy_here" in sentence.hint
        assert "server of your fleet" in sentence.hint


class TestTheGuardAndTheCommandLineReadTheSameTable:
    def test_a_container_host_lets_a_container_application_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub", deploy_here="containers")

        for key in DEPLOYS_CONTAINERS:
            central.require_server_role(key)

    def test_and_refuses_the_rest_in_the_words_of_the_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub", deploy_here="containers")

        with pytest.raises(central.RoleError) as caught:
            central.require_server_role("native_deploy")

        assert caught.value.message == (
            "Applications: not available here, which deploys containers and nothing else"
        )
        assert "central.deploy_here" in caught.value.details

    def test_the_commands_of_an_application_are_not_refused_and_the_server_s_are(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub", deploy_here="containers")

        for command in ("create", "list", "update", "logs", "recipe", "rollback", "backup"):
            central.refuse_command_on_hub(command)
        for command in ("setup", "service", "cron", "domain", "cert", "site", "preview", "monitor"):
            with pytest.raises(central.RoleError):
                central.refuse_command_on_hub(command)

    def test_the_guard_does_not_ask_the_machine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """What is refused is the role; a missing Docker is said by what needs it, as on a server."""
        configure(monkeypatch, role="hub", deploy_here="containers")

        central.require_server_role("container_deploy")


class TestTheSessionSaysIt:
    @pytest.fixture
    def client(self, sandbox: Path, runner: FakeRunner) -> TestClient:
        return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))

    def test_the_central_says_whether_it_deploys_here_and_what_follows(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub", deploy_here="containers")
        monkeypatch.setattr(
            "noust.central.machine.read_machine_facts", lambda *args, **kwargs: NAS_WITH_DOCKER
        )
        sign_in(client)

        body = client.get("/api/auth/session").json()["central"]

        assert body["role"] == "hub"
        assert body["deploy_here"] == "containers"
        assert body["features"]["container_deploy"] == {"available": True, "reason": None}
        assert body["features"]["sites"]["reason"]["code"] == "features.containers_only"

    def test_a_hub_that_was_not_asked_says_it_does_not(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub")
        monkeypatch.setattr(
            "noust.central.machine.read_machine_facts", lambda *args, **kwargs: NAS_WITH_DOCKER
        )
        sign_in(client)

        body = client.get("/api/auth/session").json()["central"]

        assert body["deploy_here"] == ""
        assert body["features"]["container_deploy"]["reason"]["code"] == "features.hub_role"


class TestTheEnvironmentsConsentIsNotCopiedIntoTheFile:
    """Taking the line out of the compose file must turn it off: nothing keeps it on behind it."""

    def written(self, tmp_path: Path) -> dict[str, Any]:
        loaded = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    def test_a_save_made_while_the_environment_says_containers_does_not_write_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub", deploy_here="containers")
        config = Config()
        config.set("monitor.scan_interval", 30)

        config.write()

        assert "deploy_here" not in self.written(tmp_path).get("central", {})
        monkeypatch.delenv("NOUST_DEPLOY_HERE")
        Config.reset_instance()
        assert central.deploy_here() == ""

    def test_what_somebody_set_on_purpose_is_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        Config.reset_instance()
        config = Config()
        config.set("central.deploy_here", "containers")

        config.write()

        assert self.written(tmp_path)["central"]["deploy_here"] == "containers"

    def test_a_value_other_than_the_environments_is_written_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub", deploy_here="containers")
        config = Config()
        config.set("central.deploy_here", "off")

        config.write()

        assert self.written(tmp_path)["central"]["deploy_here"] == ""


class TestTheConsoleCannotTurnItOn:
    """The socket is root on the machine: it is a decision made where the compose file is written."""

    @pytest.fixture
    def client(self, sandbox: Path, runner: FakeRunner) -> TestClient:
        client = TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))
        elevate(client, sign_in(client))
        return client

    def test_a_patch_of_the_setting_is_refused_and_nothing_is_written(
        self, client: TestClient
    ) -> None:
        response = client.patch(
            "/api/config", json={"path": "central.deploy_here", "value": "containers"}
        )

        assert response.status_code == 403, response.text
        assert "compose" in response.json()["hint"]
        assert central.deploy_here() == ""

    def test_a_whole_configuration_that_changes_it_is_refused(self, client: TestClient) -> None:
        current = client.get("/api/config").json()["config"]
        current.setdefault("central", {})["deploy_here"] = "containers"

        response = client.put("/api/config", json={"config": current})

        assert response.status_code == 403, response.text
        assert central.deploy_here() == ""

    def test_a_whole_configuration_that_leaves_it_alone_is_not_in_the_way(
        self, client: TestClient
    ) -> None:
        current = client.get("/api/config").json()["config"]

        response = client.put("/api/config", json={"config": current})

        assert response.status_code == 200, response.text

    def test_turning_it_off_from_the_console_is_allowed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = Config()
        config.set("central.deploy_here", "containers")
        config.write()

        response = client.patch("/api/config", json={"path": "central.deploy_here", "value": ""})

        assert response.status_code == 200, response.text
        assert Config().get("central.deploy_here") == ""


class TestTheComposeFileThatAsksForIt:
    """Documented, not packaged beside the plain one: the plain one stays what it was."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_the_packaged_compose_file_is_still_a_hub_with_no_socket(self) -> None:
        document = yaml.safe_load(
            (self.ROOT / "packaging" / "container" / "compose.yaml").read_text(encoding="utf-8")
        )
        service = document["services"]["noust"]
        mounts = " ".join(str(volume) for volume in service["volumes"])

        assert "docker.sock" not in mounts
        assert "NOUST_DEPLOY_HERE" not in (service.get("environment") or {})
        assert "group_add" not in service

    def test_the_guide_gives_the_variant_with_the_two_things_it_asks_for(self) -> None:
        guide = (self.ROOT / "docs" / "central-deploy-here.md").read_text(encoding="utf-8")
        block = next(
            part.split("```", 1)[0]
            for part in guide.split("```yaml\n")[1:]
            if "NOUST_DEPLOY_HERE" in part.split("```", 1)[0]
        )
        service = yaml.safe_load(block)["services"]["noust"]

        assert service["environment"]["NOUST_DEPLOY_HERE"] == "containers"
        assert any("/var/run/docker.sock" in str(volume) for volume in service["volumes"])
        assert service["group_add"]
        # What the plain file promises is kept: no privileges beyond the socket's group.
        assert service["user"] == "10001:10001"
        assert service["cap_drop"] == ["ALL"]
        assert service["read_only"] is True
        assert "no-new-privileges:true" in service["security_opt"]
        # The folders Docker is handed paths of are the same path inside and out: each lies under a
        # volume whose two sides are one path.
        same_path = [
            parts[0]
            for parts in (str(volume).split(":") for volume in service["volumes"])
            if len(parts) >= 2 and parts[0] == parts[1]
        ]
        for variable in ("NOUST_APPS_DIR", "NOUST_DATA_DIR", "HOME"):
            folder = service["environment"][variable]
            assert any(folder == base or folder.startswith(f"{base}/") for base in same_path), (
                variable
            )
