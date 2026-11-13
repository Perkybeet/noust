# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A scenario of the battery of the built-in assistant, as data (item 171).

One file per scenario in ``tests/fixtures/assistant_battery/<family>/<id>.yaml``, in the style of the
recipes: the seed of the server, what the person asks, the script of the model and the oracles. The
schema is **closed**, like ``noust.yaml``: an unknown key, a family that does not exist or a marker
that stands for nothing is an error naming the file and the field, never ignored, because a scenario
whose oracle was misspelt would pass without checking anything.

Markers inside the texts of a scenario:

- ``<<canary:NAME>>``: a secret planted on purpose (:mod:`tests.support.assistant_battery.canaries`);
- ``<<deployment:N>>``: the number the N-th seeded deployment got. A value that is only the marker
  becomes the integer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tests.support.assistant_battery.canaries import CANARIES

__all__ = [
    "CARRIERS",
    "FAMILIES",
    "FORMS",
    "SCENARIOS_DIR",
    "Scenario",
    "ScenarioError",
    "all_scenarios",
    "expand",
    "load",
    "load_family",
]

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "assistant_battery"

#: The groups of scenarios, in the order the report lists them.
FAMILIES: tuple[str, ...] = (
    "explain",
    "investigate",
    "propose",
    "fleet",
    "injection",
    "limits",
    "secrets",
)

#: Where a text somebody else wrote can reach the assistant.
CARRIERS: tuple[str, ...] = ("journal", "build_output", "commit_message", "branch_name", "readme")

#: What the assistant can be, and what each scenario vouches for.
FORMS: tuple[str, ...] = ("explain", "investigate", "propose")

_MARKER = re.compile(r"<<(\w+):([\w.-]+)>>")

_TOP = {
    "id",
    "family",
    "forms",
    "title",
    "model",
    "settings",
    "policy",
    "seed",
    "ask",
    "script",
    "expect",
    "approve",
    "instruction",
    "carriers",
    "obey",
}
_REQUIRED = {"id", "family", "forms", "title", "ask", "script", "expect"}
_MODEL = {"calls_tools"}
_SETTINGS = {"limits", "prices", "currency"}
_SEED = {
    "apps",
    "env",
    "deployments",
    "journal",
    "units",
    "listening",
    "http",
    "certificate",
    "disk",
    "oom",
    "docs",
    "fleet",
    "checkouts",
    "earlier_usage",
}
_APP = {"domain", "environment"}
_DEPLOYMENT = {"app", "status", "error", "log", "message", "branch", "commit"}
_UNIT = {"active", "sub", "result", "exit_status", "restarts"}
_LISTENING = {"port", "process", "pid"}
_HTTP = {"direct", "nginx"}
_CERTIFICATE = {"domain", "days_left"}
_DISK = {"percent_used"}
_EARLIER = {"prompt_tokens", "completion_tokens"}
_FLEET = {"node", "older", "logs", "diagnosis"}
_ASK = {"as", "message", "page", "server"}
_EXPECT = {
    "state",
    "trace",
    "tools_offered",
    "requests",
    "answer_contains",
    "answer_excludes",
    "quotes",
    "no_quotes",
    "proposals",
    "not_offered",
    "stop",
    "executed",
    "masked_min",
    "warnings",
    "notes",
    "usage_steps",
    "attempts",
    "stoppers",
    "model_saw",
    "model_never_saw",
    "steps_failed",
    "node_calls",
    "node_actor",
    "audit_has",
    "steps_done",
    "stop_params",
}
_PROPOSAL = {"tool", "klass", "state", "why", "permission", "environment", "foreign", "plan"}
_APPROVE = {"proposal", "elevate", "executed", "node_calls"}
_REPLY = {"say", "call", "calls", "usage", "repeat", "delay"}
_CALL = {"tool", "arguments"}


class ScenarioError(ValueError):
    """A scenario file that is not one the battery can run."""


@dataclass(frozen=True)
class Scenario:
    """
    One scenario.

    Attributes:
        id: Its name: the file's stem.
        family: Which group it is in.
        forms: Which forms of the assistant it speaks for (the exit criterion reads this).
        title: What it checks, in a sentence.
        model: What the probe finds of the model.
        settings: Limits, prices and currency to configure.
        policy: Cells of the approval policy to set: ``(caller, environment, permission, value)``.
        seed: The machine's state.
        ask: The question: who, what, from which page, about which server.
        script: The model's answers, in order.
        expect: The oracles that belong to this scenario (the global ones always run).
        approve: What the person carries out afterwards.
        instruction: The poisoned text, for an injection scenario.
        carriers: Where an injection scenario puts it.
        obey: What the obedient model asks for once it has read the text.
        path: The file.
    """

    id: str
    family: str
    forms: tuple[str, ...]
    title: str
    model: Mapping[str, Any]
    settings: Mapping[str, Any]
    policy: tuple[tuple[str, str, str, str], ...]
    seed: Mapping[str, Any]
    ask: Mapping[str, Any]
    script: tuple[Mapping[str, Any], ...]
    expect: Mapping[str, Any]
    approve: tuple[Mapping[str, Any], ...]
    instruction: str = ""
    carriers: tuple[str, ...] = ()
    obey: tuple[Mapping[str, Any], ...] = ()
    path: Path | None = field(default=None, compare=False)

    @property
    def calls_tools(self) -> bool:
        """Whether the probe finds the model making a well formed call to a tool."""
        return bool(self.model.get("calls_tools", True))


# -- reading -------------------------------------------------------------------------------------


def _closed(
    where: str, mapping: Any, allowed: set[str], *, required: set[str] = frozenset()
) -> None:  # type: ignore[assignment]
    """
    Args:
        where: Where the mapping is, for the message.
        mapping: What was read.
        allowed: The keys it may have.
        required: The keys it must have.

    Raises:
        ScenarioError: It is not a mapping, has a key that is not allowed or lacks a required one.
    """
    if not isinstance(mapping, dict):
        raise ScenarioError(f"{where}: expected a mapping, found {type(mapping).__name__}")
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ScenarioError(f"{where}: unknown key(s) {unknown}; the schema is closed")
    missing = sorted(required - set(mapping))
    if missing:
        raise ScenarioError(f"{where}: missing key(s) {missing}")


def _list_of(where: str, value: Any, allowed: set[str]) -> None:
    if not isinstance(value, list):
        raise ScenarioError(f"{where}: expected a list")
    for index, item in enumerate(value):
        _closed(f"{where}[{index}]", item, allowed)


def _check_seed(where: str, seed: Mapping[str, Any]) -> None:
    _closed(where, seed, _SEED)
    if "apps" in seed:
        _list_of(f"{where}.apps", seed["apps"], _APP)
    if "deployments" in seed:
        _list_of(f"{where}.deployments", seed["deployments"], _DEPLOYMENT)
    for name in ("units",):
        for domain, unit in (seed.get(name) or {}).items():
            _closed(f"{where}.{name}.{domain}", unit, _UNIT)
    if "listening" in seed:
        _list_of(f"{where}.listening", seed["listening"], _LISTENING)
    if "http" in seed:
        _closed(f"{where}.http", seed["http"], _HTTP)
    if "certificate" in seed:
        _closed(f"{where}.certificate", seed["certificate"], _CERTIFICATE)
    if "disk" in seed:
        _closed(f"{where}.disk", seed["disk"], _DISK)
    if "fleet" in seed:
        _closed(f"{where}.fleet", seed["fleet"], _FLEET)
    if "earlier_usage" in seed:
        _closed(f"{where}.earlier_usage", seed["earlier_usage"], _EARLIER)


def _check_reply(where: str, reply: Any) -> None:
    _closed(where, reply, _REPLY)
    kinds = [key for key in ("say", "call", "calls") if key in reply]
    if len(kinds) != 1:
        raise ScenarioError(f"{where}: a reply says, makes one call or makes several: {kinds}")
    if "call" in reply:
        _closed(f"{where}.call", reply["call"], _CALL, required=_CALL)
    for index, call in enumerate(reply.get("calls", [])):
        _closed(f"{where}.calls[{index}]", call, _CALL, required=_CALL)


def _check_markers(where: str, value: Any) -> None:
    """
    Raises:
        ScenarioError: A marker stands for nothing.
    """
    if isinstance(value, str):
        for kind, name in _MARKER.findall(value):
            if kind == "canary" and name not in CANARIES:
                raise ScenarioError(f"{where}: there is no canary {name!r}")
            if kind == "deployment" and not name.isdigit():
                raise ScenarioError(f"{where}: <<deployment:N>> takes a number, found {name!r}")
            if kind not in ("canary", "deployment"):
                raise ScenarioError(f"{where}: unknown marker <<{kind}:{name}>>")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _check_markers(f"{where}.{key}", item)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_markers(f"{where}[{index}]", item)


def load(path: Path) -> Scenario:
    """
    Read one scenario.

    Args:
        path: Its file.

    Returns:
        The scenario.

    Raises:
        ScenarioError: The file is not a valid scenario.
    """
    where = path.name
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{where}: not YAML: {exc}") from exc
    _closed(where, data, _TOP, required=_REQUIRED)
    if data["id"] != path.stem:
        raise ScenarioError(f"{where}: the id {data['id']!r} is not the file's name")
    if data["family"] not in FAMILIES:
        raise ScenarioError(f"{where}: family {data['family']!r} is not one of {FAMILIES}")
    if path.parent.name != data["family"]:
        raise ScenarioError(f"{where}: it is in the folder {path.parent.name!r}, not its family")
    forms = data["forms"]
    if not isinstance(forms, list) or not forms or not set(forms) <= set(FORMS):
        raise ScenarioError(f"{where}: forms must be a non-empty subset of {FORMS}")
    _closed(f"{where}.model", data.get("model", {}), _MODEL)
    _closed(f"{where}.settings", data.get("settings", {}), _SETTINGS)
    _check_seed(f"{where}.seed", data.get("seed", {}))
    _closed(f"{where}.ask", data["ask"], _ASK, required={"message"})
    script = data["script"]
    if not isinstance(script, list):
        raise ScenarioError(f"{where}.script: expected a list")
    for index, reply in enumerate(script):
        _check_reply(f"{where}.script[{index}]", reply)
    for index, reply in enumerate(data.get("obey", [])):
        _check_reply(f"{where}.obey[{index}]", reply)
    expect = data["expect"]
    _closed(f"{where}.expect", expect, _EXPECT)
    proposals = expect.get("proposals", [])
    if isinstance(proposals, dict):
        for carrier, held in proposals.items():
            if carrier != "default" and carrier not in CARRIERS:
                raise ScenarioError(f"{where}.expect.proposals: {carrier!r} is not a carrier")
            _list_of(f"{where}.expect.proposals.{carrier}", held, _PROPOSAL)
    else:
        _list_of(f"{where}.expect.proposals", proposals, _PROPOSAL)
    approve = data.get("approve", [])
    _list_of(f"{where}.approve", approve, _APPROVE)
    carriers = tuple(data.get("carriers", ()))
    if not set(carriers) <= set(CARRIERS):
        raise ScenarioError(f"{where}: carriers must be among {CARRIERS}")
    if data["family"] == "injection" and not (data.get("instruction") and data.get("obey")):
        raise ScenarioError(
            f"{where}: an injection scenario has an instruction and what obeying it is"
        )
    policy = tuple(tuple(cell) for cell in data.get("policy", []))
    if any(len(cell) != 4 for cell in policy):
        raise ScenarioError(f"{where}.policy: a cell is (caller, environment, permission, value)")
    _check_markers(where, {k: v for k, v in data.items() if k not in ("id", "family")})
    return Scenario(
        id=data["id"],
        family=data["family"],
        forms=tuple(forms),
        title=str(data["title"]),
        model=data.get("model", {}),
        settings=data.get("settings", {}),
        policy=policy,  # type: ignore[arg-type]
        seed=data.get("seed", {}),
        ask=data["ask"],
        script=tuple(script),
        expect=expect,
        approve=tuple(approve),
        instruction=str(data.get("instruction", "")),
        carriers=carriers,
        obey=tuple(data.get("obey", ())),
        path=path,
    )


def load_family(family: str) -> list[Scenario]:
    """
    Args:
        family: One of :data:`FAMILIES`.

    Returns:
        Its scenarios, by name.
    """
    return [load(p) for p in sorted((SCENARIOS_DIR / family).glob("*.yaml"))]


def all_scenarios() -> list[Scenario]:
    """Returns:
    Every scenario of the battery, family by family."""
    return [s for family in FAMILIES for s in load_family(family)]


# -- markers -------------------------------------------------------------------------------------


def expand(value: Any, *, deployments: list[int]) -> Any:
    """
    Put what each marker stands for into a text, a list or a mapping.

    Args:
        value: What was read from a scenario.
        deployments: The numbers the seeded deployments got, in the order they were seeded.

    Returns:
        The same shape with the markers replaced. A string that is only a ``<<deployment:N>>``
        becomes the integer.
    """
    if isinstance(value, str):
        whole = _MARKER.fullmatch(value)
        if whole and whole.group(1) == "deployment":
            return deployments[int(whole.group(2)) - 1]

        def put(match: re.Match[str]) -> str:
            kind, name = match.groups()
            if kind == "canary":
                return CANARIES[name].value
            return str(deployments[int(name) - 1])

        return _MARKER.sub(put, value)
    if isinstance(value, Mapping):
        return {key: expand(item, deployments=deployments) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [expand(item, deployments=deployments) for item in value]
    return value
