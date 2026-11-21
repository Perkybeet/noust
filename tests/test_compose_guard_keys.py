# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The guard knows every key of Compose, and refuses the one it does not.

It used to list the keys that give the machine and let the rest through, and Compose gained keys that
give it (``use_api_socket``, ``post_start`` with ``privileged``, ``pre_start``, ``build.entitlements``,
``label_file``). The list is closed now (``compose_keys``): every key of the specification has been
decided about, a key that is not in it is refused for a new stack and warned about for one that already
had it, and what gives the machine is found through every key that can carry it.

Three things hold that together. The table is the specification (``tests/fixtures/compose_spec_keys.json``,
written by ``scripts/compose_spec_keys.py``): a key Compose has that the table has not decided about
fails here. Every key that is not inert has a hostile value that is found and an ordinary one that is
not. And what an installation really has (the recipes of the catalogue, the compose files the suite
carries) uses no key the guard does not know, so an application in production is never stopped by a key
that merely was not written down.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.compose_yaml import load_compose
from noust.core.docker.owners import AppRecord, OwnerIndex, Registry
from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import compose_keys
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.compose_guard import GuardContext, inspect_compose
from noust.deployers.helpers.compose_owners import Owners

APP = Path("/var/www/apps/stack-example-com")
TESTS = Path(__file__).resolve().parent
SPEC = json.loads((TESTS / "fixtures" / "compose_spec_keys.json").read_text(encoding="utf-8"))

#: A hostile case: the document, a word of the sentence that says it, whether it is refused (else only
#: said) and whether the store's list of the other applications is part of it.
Case = tuple[dict[str, Any], str, bool, bool]


def svc(**service: Any) -> dict[str, Any]:
    """A compose document with one service."""
    return {"services": {"web": {"image": "nginx", **service}}}


def top(service: dict[str, Any], **parts: Any) -> dict[str, Any]:
    """A compose document with one service and what else the file has."""
    return {"services": {"web": {"image": "nginx", **service}}, **parts}


def judge(document: dict[str, Any], *, owners: bool = False) -> Any:
    """What the guard finds in a document, for the application ``stack.example.com``."""
    registry = Registry(
        apps=(
            AppRecord("other.example.com", "/var/www/apps/other-example-com"),
            AppRecord("stack.example.com", str(APP)),
        )
    )
    known = Owners(OwnerIndex(registry), "stack.example.com") if owners else None
    context = GuardContext(
        APP, domain="stack.example.com", project="stack-example-com", owners=known
    )
    return inspect_compose(document, APP, context=context)


SECRET_FILE = {"secrets": {"s": {"file": "/etc/noust/config.yaml"}}}
CONFIG_FILE = {"configs": {"c": {"file": "/etc/noust/config.yaml"}}}

#: What each key that is not inert does when its value is the hostile one, by section.
HOSTILE: dict[str, dict[str, Case]] = {
    "top": {
        "name": ({"name": "other-example-com", **svc()}, "belongs to", True, True),
        "include": ({"include": ["/etc/other.yml"]}, "outside the application", True, False),
        "services": (svc(privileged=True), "is privileged", True, False),
        "jobs": (
            {"jobs": {"j": {"image": "x", "privileged": True, "triggers": {"manual": True}}}},
            "job j is privileged",
            True,
            False,
        ),
        "networks": (
            {"networks": {"n": {"driver": "macvlan"}}, **svc()},
            "network driver",
            True,
            False,
        ),
        "volumes": (
            {"volumes": {"v": {"driver_opts": {"type": "none", "o": "bind", "device": "/etc"}}}},
            "mounts /etc",
            True,
            False,
        ),
        "secrets": (top({"secrets": ["s"]}, **SECRET_FILE), "as a secret", True, False),
        "configs": (top({"configs": ["c"]}, **CONFIG_FILE), "as a config", True, False),
        "models": (
            {"models": {"m": {"model": "ai/x", "runtime_flags": ["--x"]}}, **svc()},
            "runtime_flags",
            True,
            False,
        ),
    },
    "service": {
        "privileged": (svc(privileged=True), "is privileged", True, False),
        "use_api_socket": (svc(use_api_socket=True), "Docker's API socket", True, False),
        "provider": (svc(provider={"type": "sh"}), "program sh", True, False),
        "devices": (svc(devices=["/dev/kvm"]), "host device /dev/kvm", True, False),
        "device_cgroup_rules": (svc(device_cgroup_rules=["c 1:3 mr"]), "host device", True, False),
        "cgroup_parent": (svc(cgroup_parent="/system.slice"), "cgroup /system.slice", True, False),
        "build": (
            svc(build={"context": ".", "entitlements": ["security.insecure"]}),
            "entitlement",
            True,
            False,
        ),
        "cap_add": (svc(cap_add=["SYS_TIME"]), "gives it this server", True, False),
        "cgroup": (svc(cgroup="host"), "shares the host's cgroup", True, False),
        "configs": (top({"configs": ["c"]}, **CONFIG_FILE), "as a config", True, False),
        "container_name": (
            svc(container_name="other-example-com-web-1"),
            "belongs to",
            True,
            True,
        ),
        "deploy": (
            svc(
                deploy={
                    "resources": {
                        "reservations": {
                            "devices": [
                                {"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}
                            ]
                        }
                    }
                }
            ),
            "graphics",
            False,
            False,
        ),
        "env_file": (svc(env_file=["/etc/noust/config.yaml"]), "env_file", True, False),
        "extends": (
            svc(extends={"file": "/etc/other.yml", "service": "x"}),
            "extends a file outside",
            True,
            False,
        ),
        "gpus": (svc(gpus="all"), "graphics", False, False),
        "image": (svc(image="other-example-com-web"), "built", True, True),
        "ipc": (svc(ipc="host"), "shares the host's ipc", True, False),
        "label_file": (svc(label_file=["/etc/noust/config.yaml"]), "label_file", True, False),
        "models": (
            top({"models": ["m"]}, models={"m": {"model": "ai/x"}}),
            "model",
            False,
            False,
        ),
        "network_mode": (svc(network_mode="host"), "shares the host's network_mode", True, False),
        "pid": (svc(pid="host"), "shares the host's pid", True, False),
        "post_start": (
            svc(post_start=[{"command": ["true"], "privileged": True}]),
            "post_start",
            True,
            False,
        ),
        "pre_start": (
            svc(pre_start=[{"image": "busybox", "privileged": True}]),
            "pre_start",
            True,
            False,
        ),
        "pre_stop": (
            svc(pre_stop=[{"command": ["true"], "privileged": True}]),
            "pre_stop",
            True,
            False,
        ),
        "runtime": (svc(runtime="nvidia"), "runtime nvidia", True, False),
        "secrets": (top({"secrets": ["s"]}, **SECRET_FILE), "as a secret", True, False),
        "security_opt": (
            svc(security_opt=["seccomp=./allow-all.json"]),
            "profile of its own",
            True,
            False,
        ),
        "uts": (svc(uts="host"), "shares the host's uts", True, False),
        "userns_mode": (svc(userns_mode="host"), "shares the host's userns_mode", True, False),
        "volumes": (svc(volumes=["/etc:/m"]), "mounts /etc", True, False),
        "volumes_from": (
            svc(volumes_from=["container:other"]),
            "volumes of the container other",
            True,
            False,
        ),
    },
    "build": {
        "additional_contexts": (
            svc(build={"context": ".", "additional_contexts": {"x": "/etc"}}),
            "build.additional_contexts",
            True,
            False,
        ),
        "cache_from": (
            svc(build={"context": ".", "cache_from": ["type=local,src=/etc/noust"]}),
            "build.cache_from",
            True,
            False,
        ),
        "cache_to": (
            svc(build={"context": ".", "cache_to": ["type=local,dest=/etc/noust"]}),
            "build.cache_to",
            True,
            False,
        ),
        "context": (svc(build={"context": "/etc"}), "build.context", True, False),
        "dockerfile": (
            svc(build={"context": ".", "dockerfile": "/etc/noust/signing.key"}),
            "build.dockerfile",
            True,
            False,
        ),
        "entitlements": (
            svc(build={"context": ".", "entitlements": ["network.host"]}),
            "entitlement network.host",
            True,
            False,
        ),
        "network": (
            svc(build={"context": ".", "network": "host"}),
            "builds on this server's own network",
            True,
            False,
        ),
        "secrets": (
            top({"build": {"context": ".", "secrets": ["s"]}}, **SECRET_FILE),
            "as a secret",
            True,
            False,
        ),
        "ssh": (
            svc(build={"context": ".", "ssh": ["k=/root/.ssh/id_ed25519"]}),
            "build.ssh",
            True,
            False,
        ),
        "tags": (
            svc(build={"context": ".", "tags": ["other-example-com-web:latest"]}),
            "built",
            True,
            True,
        ),
        "privileged": (
            svc(build={"context": ".", "privileged": True}),
            "builds with elevated privileges",
            True,
            False,
        ),
    },
    "network": {
        "driver": (
            {"networks": {"n": {"driver": "ipvlan", "driver_opts": {"parent": "eth0"}}}, **svc()},
            "network driver ipvlan",
            True,
            False,
        ),
        "driver_opts": (
            {
                "networks": {
                    "n": {
                        "driver": "bridge",
                        "driver_opts": {"com.docker.network.bridge.name": "br0"},
                    }
                },
                **svc(),
            },
            "bridge br0",
            True,
            False,
        ),
        "external": (
            {"networks": {"n": {"external": True, "name": "other-example-com_default"}}, **svc()},
            "belongs to",
            True,
            True,
        ),
        "name": (
            {"networks": {"n": {"name": "other-example-com_default"}}, **svc()},
            "belongs to",
            True,
            True,
        ),
    },
    "volume": {
        "driver": (
            {"volumes": {"v": {"driver": "rclone", "driver_opts": {"remote": "x:"}}}, **svc()},
            "volume driver rclone",
            True,
            False,
        ),
        "driver_opts": (
            {"volumes": {"v": {"driver_opts": {"type": "none", "o": "bind", "device": "/etc"}}}},
            "mounts /etc",
            True,
            False,
        ),
        "external": (
            {"volumes": {"v": {"external": True, "name": "other-example-com_data"}}, **svc()},
            "belongs to",
            True,
            True,
        ),
        "name": (
            {"volumes": {"v": {"name": "other-example-com_data"}}, **svc()},
            "belongs to",
            True,
            True,
        ),
    },
    "secret": {"file": (top({"secrets": ["s"]}, **SECRET_FILE), "as a secret", True, False)},
    "config": {"file": (top({"configs": ["c"]}, **CONFIG_FILE), "as a config", True, False)},
    "model": {
        "runtime_flags": (
            {"models": {"m": {"model": "ai/x", "runtime_flags": ["--x"]}}, **svc()},
            "runtime_flags",
            True,
            False,
        )
    },
    "hook": {
        "privileged": (
            svc(post_start=[{"command": ["true"], "privileged": "true"}]),
            "post_start",
            True,
            False,
        )
    },
    "include": {
        "path": ({"include": ["/etc/other.yml"]}, "outside the application", True, False),
        "env_file": (
            {"include": [{"path": "./x.yml", "env_file": "/etc/noust/config.yaml"}]},
            "include.env_file",
            True,
            False,
        ),
        "project_directory": (
            {"include": [{"path": "./x.yml", "project_directory": "/etc"}]},
            "include.project_directory",
            True,
            False,
        ),
    },
    "extends": {
        "file": (
            svc(extends={"file": "/etc/other.yml", "service": "x"}),
            "extends a file outside",
            True,
            False,
        )
    },
    "deploy": {
        "resources": (
            svc(deploy={"resources": {"reservations": {"devices": [{"capabilities": ["gpu"]}]}}}),
            "graphics",
            False,
            False,
        )
    },
    "mount": {
        "type": (
            svc(volumes=[{"type": "image", "source": "other-example-com-web", "target": "/m"}]),
            "built",
            True,
            True,
        ),
        "source": (
            svc(volumes=[{"type": "bind", "source": "/etc", "target": "/m"}]),
            "mounts /etc",
            True,
            False,
        ),
    },
    "pre_start": {
        "privileged": (
            svc(pre_start=[{"image": "busybox", "privileged": True}]),
            "pre_start",
            True,
            False,
        ),
        "devices": (
            svc(pre_start=[{"image": "busybox", "devices": ["/dev/sda"]}]),
            "host device /dev/sda",
            True,
            False,
        ),
        "volumes": (
            svc(pre_start=[{"image": "busybox", "volumes": ["/:/host"]}]),
            "pre_start",
            True,
            False,
        ),
    },
}

#: What the same keys do with an ordinary value: nothing that stops a stack.
ORDINARY: dict[str, Any] = {
    "privileged": False,
    "use_api_socket": False,
    "provider": None,
    "devices": [],
    "device_cgroup_rules": [],
    "cgroup_parent": "",
    "build": ".",
    "cap_add": ["NET_BIND_SERVICE"],
    "cgroup": "private",
    "container_name": "stack-web",
    "deploy": {"replicas": 2, "resources": {"limits": {"memory": "512M"}}},
    "env_file": ["./.env"],
    "extends": {"service": "base"},
    "gpus": None,
    "image": "nginx:1.27",
    "ipc": "private",
    "label_file": ["./labels.env"],
    "models": None,
    "network_mode": "bridge",
    "pid": "private",
    "post_start": [{"command": ["true"], "user": "root"}],
    "pre_start": [{"image": "busybox", "command": ["true"]}],
    "pre_stop": [{"command": ["true"]}],
    "runtime": "runc",
    "security_opt": ["no-new-privileges:true", "seccomp=builtin"],
    "uts": "private",
    "userns_mode": "",
    "volumes": ["./data:/data", "cache:/cache"],
    "volumes_from": ["db"],
}


class TestTheListIsTheSpecification:
    """The table of keys is held to the keys of the specification, in both directions."""

    def test_the_fixture_is_what_the_script_writes(self) -> None:
        assert set(SPEC["sections"]) >= set(compose_keys.SECTIONS)
        assert SPEC["source"].startswith("https://")

    @pytest.mark.parametrize("section", sorted(compose_keys.SECTIONS))
    def test_a_key_of_compose_that_is_not_decided_about_fails_here(self, section: str) -> None:
        undecided = sorted(set(SPEC["sections"][section]) - set(compose_keys.SECTIONS[section]))

        assert not undecided, (
            f"Compose has keys in '{section}' that the guard has not decided about: {undecided}. "
            "Classify each in noust.deployers.helpers.compose_keys (inert, judged or gives) and give "
            "the judged and the gives ones a case in this file."
        )

    @pytest.mark.parametrize("section", sorted(set(compose_keys.SECTIONS) - {"pre_start"}))
    def test_a_key_that_is_not_in_the_specification_is_not_in_the_table(self, section: str) -> None:
        invented = sorted(set(compose_keys.SECTIONS[section]) - set(SPEC["sections"][section]))

        assert not invented, f"'{section}' decides about keys Compose does not have: {invented}"

    def test_a_pre_start_container_has_what_a_service_has(self) -> None:
        assert set(SPEC["sections"]["pre_start"]) - {"per_replica"} <= set(compose_keys.SERVICE)

    def test_an_extension_is_not_a_key(self) -> None:
        assert compose_keys.is_extension("x-anything")
        assert not compose_keys.is_extension("xanything")
        assert not compose_keys.is_extension("extension")


class TestEveryKeyThatIsNotInertIsHeldToAValue:
    """A key that gives the machine, or that something reads, has a hostile value that is found."""

    @pytest.mark.parametrize("section", sorted(set(compose_keys.SECTIONS) - {"pre_start"}))
    def test_each_has_a_hostile_case(self, section: str) -> None:
        decided = {
            key
            for key, kind in compose_keys.SECTIONS[section].items()
            if kind != compose_keys.INERT
        }

        assert decided <= set(HOSTILE[section]), sorted(decided - set(HOSTILE[section]))

    @pytest.mark.parametrize("section", sorted(HOSTILE))
    def test_each_case_is_a_key_of_its_section(self, section: str) -> None:
        assert set(HOSTILE[section]) <= set(compose_keys.SECTIONS[section])

    @pytest.mark.parametrize(
        ("section", "key"),
        [(s, k) for s in sorted(HOSTILE) for k in sorted(HOSTILE[s])],
        ids=lambda value: str(value),
    )
    def test_the_hostile_value_is_found(self, section: str, key: str) -> None:
        document, word, refused, with_owners = HOSTILE[section][key]

        findings = judge(document, owners=with_owners)

        found = findings.refused if refused else findings.warned
        assert any(word in sentence for sentence in found), (section, key, findings)
        if refused:
            assert not [s for s in found if "does not know what that key gives" in s], found

    @pytest.mark.parametrize("key", sorted(ORDINARY))
    def test_an_ordinary_value_stops_nothing(self, key: str) -> None:
        findings = judge(svc(**{key: ORDINARY[key]}))

        assert findings.refused == [], (key, findings.refused)

    @pytest.mark.parametrize(
        "key",
        sorted(k for k, kind in compose_keys.SERVICE.items() if kind == compose_keys.INERT),
    )
    def test_an_inert_key_is_found_in_nothing(self, key: str) -> None:
        findings = judge(svc(**{key: "x"}))

        assert not findings, (key, findings)


UNKNOWN: dict[str, dict[str, Any]] = {
    "top": {"foo": 1, **svc()},
    "service": svc(foo=1),
    "pre_start": svc(pre_start=[{"image": "busybox", "foo": 1}]),
    "build": svc(build={"context": ".", "foo": 1}),
    "network": {"networks": {"n": {"foo": 1}}, **svc()},
    "volume": {"volumes": {"v": {"foo": 1}}, **svc()},
    "secret": {"secrets": {"s": {"foo": 1}}, **svc()},
    "config": {"configs": {"c": {"foo": 1}}, **svc()},
    "model": {"models": {"m": {"model": "ai/x", "foo": 1}}, **svc()},
    "hook": svc(post_start=[{"command": ["true"], "foo": 1}]),
    "include": {"include": [{"path": "./x.yml", "foo": 1}], **svc()},
    "extends": svc(extends={"service": "x", "foo": 1}),
    "deploy": svc(deploy={"foo": 1}),
    "mount": svc(volumes=[{"type": "bind", "source": "./d", "target": "/d", "foo": 1}]),
}


class TestAKeyThatIsNotInTheListIsRefused:
    """In every section, and with its name in the sentence."""

    def test_every_section_is_covered(self) -> None:
        assert set(UNKNOWN) == set(compose_keys.SECTIONS)

    @pytest.mark.parametrize("section", sorted(UNKNOWN))
    def test_an_unknown_key_is_refused_by_name(self, section: str) -> None:
        findings = judge(UNKNOWN[section])

        sentences = [s for s in findings.refused if "does not know what that key gives" in s]
        assert len(sentences) == 1, (section, findings.refused)
        assert "foo" in sentences[0]
        key = findings.keys[sentences[0]]
        assert key[1] == "unknown_key" and key[2].endswith("foo")
        assert sentences[0] not in findings.hard

    @pytest.mark.parametrize("section", sorted(UNKNOWN))
    def test_an_extension_is_not_one(self, section: str) -> None:
        text = json.dumps(UNKNOWN[section]).replace('"foo"', '"x-foo"')

        findings = judge(json.loads(text))

        assert not [s for s in findings.refused if "does not know what that key gives" in s], (
            findings.refused
        )

    def test_a_job_has_the_keys_of_a_service(self) -> None:
        document = {"jobs": {"j": {"image": "x", "triggers": {"manual": True}, "foo": 1}}}

        findings = judge(document)

        assert any("job j uses foo" in s for s in findings.refused), findings.refused

    def test_a_key_of_the_model_compose_prints_is_not_judged_by_this_list(self) -> None:
        context = GuardContext(
            APP, domain="stack.example.com", project="stack-example-com", closed_keys=False
        )

        findings = inspect_compose(svc(foo=1), APP, context=context)

        assert not findings

    def test_a_value_that_is_not_a_mapping_has_no_keys(self) -> None:
        assert not judge({"services": {"web": "nginx"}, "networks": ["n"], "volumes": None})


class TestWhatHappensToTheStack:
    """A new stack with such a key is refused, one that had it is warned about, an exception allows it."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> Any:
        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "noust.db")
        yield instance
        NoustStore.reset_instance()

    def deployer(
        self, tmp_path: Path, store: NoustStore, said: list[str] | None = None
    ) -> DockerComposeDeployer:
        runner = FakeRunner()
        runner.script(
            ["docker", "compose"], stdout="", stderr="invalid compose project", exit_code=1
        )
        compose = DockerComposeDeployer(runner=runner)
        compose.configure("stack.example.com", "src", app_path=tmp_path / "stack")
        compose.store = store
        if said is not None:
            compose.logger.attach_sink(said.append)
        return compose

    def test_a_new_stack_is_refused_with_the_way_out(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        with pytest.raises(DeploymentError) as raised:
            self.deployer(tmp_path, store)._check_host_privileges(
                svc(use_api_socket=True, brand_new_key=1), accepted=[]
            )

        details = raised.value.details or ""
        assert "does not know what that key gives" in details
        assert "brand_new_key" in details
        assert "noust app sandbox compose-exception stack.example.com --reason" in details

    def test_a_stack_that_already_had_it_goes_on_with_a_warning(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        said: list[str] = []
        compose = self.deployer(tmp_path, store, said)
        document = svc(brand_new_key=1)

        compose._check_host_privileges(document, accepted=compose._refusals_in(document))

        assert any("brand_new_key" in line and "A new stack is refused" in line for line in said)

    def test_a_stack_that_adds_one_is_refused(self, tmp_path: Path, store: NoustStore) -> None:
        compose = self.deployer(tmp_path, store)

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_host_privileges(
                svc(brand_new_key=1), accepted=compose._refusals_in(svc())
            )

    def test_an_exception_allows_it(self, tmp_path: Path, store: NoustStore) -> None:
        build_sandbox.set_compose_exception(
            "stack.example.com", allowed=True, actor="alice", reason="a newer compose", store=store
        )
        said: list[str] = []

        self.deployer(tmp_path, store, said)._check_host_privileges(
            svc(brand_new_key=1), accepted=[]
        )

        assert any("allowed for stack.example.com by alice" in line for line in said)


class TestWhatAnInstallationReallyHas:
    """No recipe and no compose file of the suite is stopped by a key that merely was not written down."""

    def recipes(self) -> list[tuple[str, dict[str, Any]]]:
        from noust.recipes.catalog import list_recipes

        found = [
            (recipe.name, load_compose(recipe.compose))
            for recipe in list_recipes()
            if getattr(recipe, "compose", None)
        ]
        assert len(found) > 100
        return found

    def test_no_recipe_uses_a_key_the_guard_does_not_know(self) -> None:
        context = GuardContext(APP, domain="stack.example.com", project="stack-example-com")
        unknown = {
            name: [
                s for s in found.refused + found.warned if "does not know what that key gives" in s
            ]
            for name, document in self.recipes()
            for found in [inspect_compose(document, APP, context=context)]
        }

        assert {name: lines for name, lines in unknown.items() if lines} == {}

    def test_what_a_recipe_may_say_is_all_known_to_the_guard(self) -> None:
        from noust.recipes.composefile import _SERVICE_KEYS

        assert _SERVICE_KEYS <= set(compose_keys.SERVICE)

    def test_the_compose_files_of_the_suite_use_no_unknown_key(self) -> None:
        files = sorted((TESTS / "fixtures" / "compose").glob("*.y*ml"))
        assert files
        context = GuardContext(APP, domain="stack.example.com", project="stack-example-com")

        for path in files:
            found = inspect_compose(
                load_compose(path.read_text(encoding="utf-8")), APP, context=context
            )

            assert not [s for s in found.refused if "does not know what that key gives" in s], (
                path.name
            )
