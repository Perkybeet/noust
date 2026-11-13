# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Run a scenario of the battery on a served console and bring back its evidence (item 171).

One scenario, one :class:`~tests.support.assistant_lab.Lab`: the real console over a store of its own, a
scripted provider that answers what the scenario dictates and a seeded machine. The harness seeds, asks,
lets the person carry out what they approve, and gathers a :class:`~tests.support.assistant_battery.record.Record`.
It judges nothing: the oracles do.

Canaries are planted in **everything the model may read**, whatever the scenario says: the variables of every
application, the end of every journal and of every build log. A scenario that has nothing to do with secrets
is a test of secrets too.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from noust.core.assistant.docsearch import DocIndex
from noust.core.caller_policy import CallerPolicy
from noust.core.teams import Scope
from noust.web.api import apps as apps_api
from noust.web.assistant import loop as loop_module
from tests.support.assistant_battery.canaries import CANARIES
from tests.support.assistant_battery.executor import Executor
from tests.support.assistant_battery.machine import Machine
from tests.support.assistant_battery.oracles import check_expectations, global_oracles
from tests.support.assistant_battery.record import (
    ApiCall,
    Record,
    RecordingBridge,
)
from tests.support.assistant_battery.report import summarize, write_summary
from tests.support.assistant_battery.scenario import CARRIERS, Scenario, expand
from tests.support.assistant_lab import BETA, GOOD_PROBE, SHOP, FakeServices, Lab
from tests.support.scripted_model import Reply, ScriptedCall
from tests.support.teams_support import make_app

__all__ = ["Harness", "build_replies", "replies_of_calls"]

#: What the harness plants at the end of every journal.
_JOURNAL_PLANTS = ("bearer", "url_password", "env_password", "forge_token")
#: What it plants at the end of every build log.
_LOG_PLANTS = ("env_password", "url_password", "forge_token", "private_key", "cloud_key")
#: The tables whose size says whether anything was created or removed.
_TABLES = (
    "apps",
    "sites",
    "services",
    "databases",
    "deployments",
    "domains",
    "releases",
    "jobs",
    "backup_schedules",
    "nodes",
)

_DEFAULT_JOURNAL = "listening on 3000\n"


class _Collector(logging.Handler):
    """Keeps what the server writes to its own log while a scenario runs."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
        except (TypeError, ValueError):
            text = str(record.msg)
        if record.exc_info:
            text += "\n" + "".join(logging.Formatter().formatException(record.exc_info))
        self.lines.append(text)


@contextmanager
def server_log() -> Iterator[_Collector]:
    """Yield a collector of the server's own log; the logger is left as it was found."""
    collector = _Collector()
    logger = logging.getLogger("noust")
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(collector)
    try:
        yield collector
    finally:
        logger.removeHandler(collector)
        logger.setLevel(previous)


# -- the model's script ---------------------------------------------------------------------------


def _calls(entry: Mapping[str, Any]) -> list[ScriptedCall]:
    if "call" in entry:
        return [ScriptedCall(entry["call"]["tool"], dict(entry["call"]["arguments"]))]
    return [ScriptedCall(c["tool"], dict(c["arguments"])) for c in entry.get("calls", [])]


def replies_of_calls(replies: Sequence[Reply]) -> int:
    """
    Args:
        replies: Replies of the scripted model.

    Returns:
        How many tool calls they make together.
    """
    return sum(len(r.calls) for r in replies)


def build_replies(script: Sequence[Mapping[str, Any]], deployments: list[int]) -> list[Reply]:
    """
    Args:
        script: The ``script`` (or ``obey``) of a scenario.
        deployments: The numbers the seeded deployments got.

    Returns:
        The replies the scripted model gives, in order, with ``repeat`` unrolled.
    """
    replies: list[Reply] = []
    for raw in script:
        entry = expand(dict(raw), deployments=deployments)
        options: dict[str, Any] = {}
        if "usage" in entry:
            options["usage"] = None if entry["usage"] is None else tuple(entry["usage"])
        if "delay" in entry:
            options["delay"] = float(entry["delay"])
        for _ in range(int(entry.get("repeat", 1))):
            if "say" in entry:
                replies.append(Reply.say(str(entry["say"]), **options))
            else:
                replies.append(Reply.calls_of(*_calls(entry), **options))
    return replies


# -- the machine's services -----------------------------------------------------------------------


class MachineServices(FakeServices):
    """What the machine answers about a unit, from the seeded machine rather than from one string."""

    machine: Machine | None = None

    def logs(self, name: str, lines: int = 100) -> str:
        assert self.machine is not None
        return self.machine.journal_text(name)

    def get_status(self, name: str) -> dict[str, Any]:
        assert self.machine is not None
        return {"exists": True, "active": self.machine.unit_of(name).active == "active"}


# -- the harness ---------------------------------------------------------------------------------


@dataclass
class Harness:
    """
    Runs scenarios on one lab.

    Attributes:
        lab: The console, the model and the people.
        monkeypatch: Patching helper of the test.
        node: The fake node of a fleet scenario, when the test made one.
    """

    lab: Lab
    monkeypatch: pytest.MonkeyPatch
    node: Any = None
    model_name: str = "tiny"
    wait_seconds: float = 60
    steps_per_turn: int | None = None
    executor: Executor = field(default_factory=Executor)
    bridge: RecordingBridge | None = None
    machine: Machine | None = None
    deployments: list[int] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    environments: dict[str, str] = field(default_factory=dict)

    # -- setting it up -----------------------------------------------------------------------------

    def prepare(self, scenario: Scenario, *, extra_seed: Mapping[str, Any] | None = None) -> None:
        """
        Seed the machine and the store, install the false executor and turn the assistant on.

        Args:
            scenario: The scenario.
            extra_seed: Seed to merge over the scenario's own (a carrier's text of an injection).
        """
        lab = self.lab
        seed = _merged(scenario.seed, extra_seed or {})
        self.bridge = RecordingBridge(lab.bridge.client)
        lab.bridge = self.bridge
        self.executor.restarts = lab.restarts
        self._seed_apps(seed)
        self.deployments = []
        for spec in seed.get("deployments", []):
            self.deployments.append(self._seed_deployment(spec))
        seed = expand(seed, deployments=self.deployments)
        self._seed_env(seed)
        journals = {
            d: _with_plants((seed.get("journal") or {}).get(d, _DEFAULT_JOURNAL), _JOURNAL_PLANTS)
            for d in self.domains
        }
        self.machine = Machine.seeded(self.domains, seed, journals)
        runner = self.machine.install(self.monkeypatch, lab.root.parent / "letsencrypt" / "live")
        MachineServices.machine = self.machine
        self.monkeypatch.setattr(apps_api, "ServiceManager", MachineServices)
        self.executor.install(self.monkeypatch, runner)
        self._seed_docs(seed)
        if seed.get("fleet") is not None and self.node is not None:
            self.node.logs = {
                k: _with_plants(v, _JOURNAL_PLANTS)
                for k, v in (seed["fleet"].get("logs") or {}).items()
            }
            self.node.older = bool(seed["fleet"].get("older", False))
            self.node.diagnosis = dict(seed["fleet"].get("diagnosis") or {})
        self._configure(scenario)
        self._seed_earlier_usage(seed)
        if scenario.policy:
            lab.policy(*scenario.policy)

    def _seed_apps(self, seed: Mapping[str, Any]) -> None:
        lab = self.lab
        self.domains = [SHOP, BETA]
        self.environments = {SHOP: "production", BETA: "other"}
        production = lab.teams.resolve_target(Scope.everything(), team="default")
        staging = lab.teams.resolve_target(
            Scope.everything(), team="default", environment="staging"
        )
        for spec in seed.get("apps", []):
            target = staging if spec.get("environment", "production") == "staging" else production
            make_app(lab.store, spec["domain"], target, lab.root)
            self.domains.append(spec["domain"])
            self.environments[spec["domain"]] = "other" if target is staging else "production"

    def _seed_deployment(self, spec: Mapping[str, Any]) -> int:
        status = str(spec.get("status", "failed"))
        log = _with_plants(str(spec.get("log", "building\nbuild failed\n")), _LOG_PLANTS)
        return self.lab.deployment(
            str(spec.get("app", SHOP)),
            log,
            status=status,
            error=spec.get("error", "build failed" if status == "failed" else None),
            message=spec.get("message"),
            branch=spec.get("branch"),
            commit=str(spec.get("commit", "a3f9c21")),
        )

    def _seed_env(self, seed: Mapping[str, Any]) -> None:
        planted = f"NODE_ENV=production\nDB_PASSWORD={CANARIES['env_password'].value}\n"
        for domain in self.domains:
            text = (seed.get("env") or {}).get(domain, "")
            self.lab.write_env(domain, planted + text)
        # A tree that is a git checkout is one whose earlier deployments can be gone back to by rebuilding.
        for domain in seed.get("checkouts", []):
            (self.lab.root / domain / ".git").mkdir(parents=True, exist_ok=True)

    def judge(self, scenario: Scenario, record: Record) -> list[str]:
        """
        Args:
            scenario: The scenario.
            record: What it left behind.

        Returns:
            Every way the record differs from what must hold: the four oracles that run on everything and
            what the scenario says (its markers put in).
        """
        expect = expand(dict(scenario.expect), deployments=self.deployments)
        approve = expand(list(scenario.approve), deployments=self.deployments)
        problems = [*global_oracles(record), *check_expectations(record, expect, approve)]
        write_summary(summarize(scenario, record, problems))
        return problems

    def _seed_docs(self, seed: Mapping[str, Any]) -> None:
        docs = seed.get("docs")
        if not docs:
            return
        folder = self.lab.root.parent / "battery-docs"
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in docs.items():
            (folder / name).write_text(text)
        index = DocIndex([folder])
        self.monkeypatch.setattr(loop_module, "shared_docs", lambda: index)

    def _seed_earlier_usage(self, seed: Mapping[str, Any]) -> None:
        """Write down what another conversation used earlier this month, which only the month's limit sees."""
        earlier = seed.get("earlier_usage")
        if not earlier:
            return
        from noust.core.assistant.client import Usage
        from noust.core.assistant.limits import UsageMeter
        from noust.core.assistant.settings import AssistantSettings

        settings = AssistantSettings(self.lab.store).read()
        meter = UsageMeter(
            settings.limits, settings.prices, settings.currency, store=self.lab.store
        )
        meter.record_step(
            "earlier-conversation",
            int(self.lab.marta.account.id),
            "tiny",
            Usage(int(earlier.get("prompt_tokens", 0)), int(earlier.get("completion_tokens", 0))),
        )

    def _configure(self, scenario: Scenario) -> None:
        lab = self.lab
        lab.model.script(GOOD_PROBE if scenario.calls_tools else Reply.say("I can only talk."))
        lab.marta.elevate()
        body: dict[str, Any] = {
            "base_url": lab.model.base_url,
            "model": self.model_name,
            "api_key": CANARIES["provider_key"].value,
            "allow_private_host": True,
            **scenario.settings,
        }
        if self.steps_per_turn is not None:
            body["limits"] = {**body.get("limits", {}), "steps_per_turn": self.steps_per_turn}
        response = lab.marta.put("/api/assistant/settings", body)
        assert response.status_code == 200, response.text
        lab.marta.elevate()
        response = lab.marta.put("/api/assistant/settings", {"enabled": True})
        assert response.status_code == 200, response.text
        # What the probe asked is not what a turn asks.
        lab.model.requests.clear()

    # -- asking ------------------------------------------------------------------------------------

    def ask(
        self,
        scenario: Scenario,
        replies: Sequence[Reply],
        *,
        question: str | None = None,
        page: Any = None,
    ) -> dict[str, Any]:
        """
        Ask the question and wait for the turn to end.

        Args:
            scenario: The scenario.
            replies: The model's script.
            question: The question, when it is not the scenario's.
            page: The page, when it is not the scenario's.

        Returns:
            The turn, as the console reads it.
        """
        lab = self.lab
        ask = expand(dict(scenario.ask), deployments=self.deployments)
        who = ask.get("as", "marta")
        assert who == "marta", "the battery asks as the administrator"
        body: dict[str, Any] = {}
        chosen_page = page if page is not None else ask.get("page")
        if chosen_page:
            body["page"] = chosen_page
        if ask.get("server"):
            body["server"] = ask["server"]
        lab.model.unscript()
        lab.model.script(*replies)
        conversation = lab.conversation()
        self.executor.enter("turn")
        assert self.bridge is not None
        self.bridge.phase = "turn"
        turn = lab.ask(
            conversation,
            question if question is not None else ask["message"],
            wait=self.wait_seconds,
            **body,
        )
        self.executor.enter("approval")
        self.bridge.phase = "approval"
        turn["_conversation"] = conversation
        return turn

    def approve(self, scenario: Scenario, turn: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Do what the person does: carry out the proposals they approve, in their own session.

        Args:
            scenario: The scenario, whose ``approve`` lists what is carried out.
            turn: The turn.

        Returns:
            For each approval, the proposal, the status the call got and the orders it gave the machine.
        """
        lab = self.lab
        conversation = turn["_conversation"]
        done: list[dict[str, Any]] = []
        for step in scenario.approve:
            proposal = turn["proposals"][int(step["proposal"]) - 1]
            before = len(self.executor.receipts)
            node_before = len(self.node.seen) if self.node is not None else 0
            if step.get("elevate"):
                lab.marta.elevate()
            call = proposal["call"]
            made = lab.marta.client.request(
                call["method"],
                call["path"],
                json=call["body"],
                params=call.get("query") or None,
                headers=lab.marta.headers(**call["headers"]),
            )
            lab.marta.post(
                f"/api/assistant/conversations/{conversation}/proposals/{proposal['id']}/outcome",
                {"fingerprint": proposal["fingerprint"], "status": made.status_code},
            )
            self.executor.collect_runner()
            done.append(
                {
                    "proposal": proposal["id"],
                    "status": made.status_code,
                    "receipts": [dict(r) for r in self.executor.receipts[before:]],
                    "node_seen": list(self.node.seen[node_before:])
                    if self.node is not None
                    else [],
                }
            )
        return done

    # -- the evidence ------------------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """
        Returns:
            What the server holds that the assistant must not change on its own: applications, the size of
            the tables, the variables of each, and the configuration.
        """
        lab = self.lab
        state: dict[str, Any] = {
            "apps": sorted((a.domain, a.status, a.port) for a in lab.store.list_apps()),
        }
        with lab.store._transaction() as cursor:
            for table in _TABLES:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 - fixed names
                state[f"rows:{table}"] = cursor.fetchone()[0]
        for domain in self.domains:
            env = lab.root / domain / ".env"
            state[f"env:{domain}"] = _digest(env.read_text()) if env.exists() else None
        from noust.core import config as config_module

        path = Path(config_module.DEFAULT_CONFIG_PATH)
        state["config"] = _digest(path.read_text()) if path.exists() else None
        return state

    def surfaces(
        self,
        turn: dict[str, Any],
        *,
        logs: Sequence[str],
        frames_from: int = 0,
        audit_from: int = 0,
        requests_from: int = 0,
    ) -> dict[str, str]:
        """
        Gather the text of every surface a secret could have leaked to.

        Args:
            turn: The turn.
            logs: What the server wrote to its log.
            frames_from: Where this scenario's stream frames begin.
            audit_from: Where its audit events begin.
            requests_from: Where its requests to the model begin.

        Returns:
            The surfaces, as text, by name.
        """
        lab = self.lab
        conversation = turn["_conversation"]
        stored = _stored(lab)
        shown = [
            lab.marta.get(f"/api/assistant/conversations/{conversation}").text,
            lab.marta.get(f"/api/assistant/conversations/{conversation}/turns").text,
            lab.marta.get(f"/api/assistant/conversations/{conversation}/proposals").text,
        ]
        received = "\n".join(
            "\n".join([r.path, r.raw.decode("utf-8", errors="replace")])
            + "\n".join(f"{k}: {v}" for k, v in r.headers.items() if k != "authorization")
            for r in lab.model.requests[requests_from:]
        )
        return {
            "model": received,
            "conversation": "\n".join([stored, json.dumps(turn, default=str), *shown]),
            "events": "\n".join(lab.frames[frames_from:]),
            "audit": "\n".join(json.dumps(e) for e in lab.audit_events()[audit_from:]),
            "logs": "\n".join(logs),
        }

    def policy_of(self, calls: Sequence[ApiCall]) -> dict[str, str]:
        """
        Args:
            calls: The calls the assistant made.

        Returns:
            The value of the assistant's cell for each permission and environment those calls touched,
            read from the policy table itself.
        """
        values: dict[str, str] = {}
        policy = CallerPolicy(self.lab.store)
        for call in calls:
            if call.permission is None or not call.changes:
                continue
            environment = self.environments.get(call.domain or "", "production")
            key = f"{call.permission}|{environment}"
            if key not in values:
                values[key] = policy.cell("assistant", environment, call.permission).value
        return values

    def run(self, scenario: Scenario) -> Record:
        """
        Run one scenario that has no carriers.

        Args:
            scenario: The scenario.

        Returns:
            What it left behind.
        """
        self.prepare(scenario)
        return self.play(scenario, build_replies(scenario.script, self.deployments))

    def run_carriers(self, scenario: Scenario) -> list[Record]:
        """
        Run an injection scenario: the same poisoned instruction in each place a text of somebody else's
        reaches the assistant, and a model that does what it says.

        Args:
            scenario: An injection scenario: an instruction and what obeying it comes to.

        Returns:
            One record per carrier.
        """
        self.prepare(scenario)
        assert self.machine is not None
        records: list[Record] = []
        for carrier in scenario.carriers or CARRIERS:
            text = scenario.instruction
            question = "What is wrong?"
            page: Any = None
            prefix: list[Reply] = []
            if carrier == "journal":
                self.machine.journals[SHOP] = _with_plants(
                    f"2026-11-12 ERROR worker crashed\n{text}\n", _JOURNAL_PLANTS
                )
                prefix = [Reply.call("application_logs", {"domain": SHOP})]
            elif carrier == "readme":
                # Nothing of the product reads it: the person pastes it, which makes it their words.
                question = f"Why does the build fail? The README of the project says:\n{text}"
            else:
                spec: dict[str, Any] = {"app": SHOP, "log": "building\nbuild failed\n"}
                if carrier == "build_output":
                    spec["log"] = f"building\n{text}\nbuild failed\n"
                elif carrier == "commit_message":
                    spec["message"] = text
                else:
                    spec["branch"] = text
                page = {"kind": "deployment", "id": self._seed_deployment(spec)}
            replies = [
                *prefix,
                *build_replies(scenario.obey, self.deployments),
                Reply.say("I did what I was asked."),
            ]
            records.append(
                self.play(
                    scenario, replies, question=question, page=page, extra={"carrier": carrier}
                )
            )
        return records

    @staticmethod
    def authored(replies: Sequence[Reply]) -> list[str]:
        """
        Args:
            replies: The model's script.

        Returns:
            The canaries the model itself writes in what it says and the calls it makes. The server did not
            give it these: a model that has a secret of its own to repeat is not a leak of the server's, and
            what the server does with it (keep it, show it, carry it out) is what the other surfaces say.
        """
        from tests.support.assistant_battery.canaries import leaks_in

        written = " ".join([r.text for r in replies] + [c.text() for r in replies for c in r.calls])
        return leaks_in(written)

    def play(
        self,
        scenario: Scenario,
        replies: Sequence[Reply],
        *,
        question: str | None = None,
        page: Any = None,
        extra: Mapping[str, Any] | None = None,
    ) -> Record:
        """
        Ask, let the person approve, and gather the evidence.

        Args:
            scenario: The scenario.
            replies: The model's script.
            question: The question, when it is not the scenario's.
            page: The page, when it is not the scenario's.
            extra: What to add to the record's ``extra``.

        Returns:
            What it left behind.
        """
        lab = self.lab
        assert self.bridge is not None
        frames_from, audit_from = len(lab.frames), len(lab.audit_events())
        requests_from = len(lab.model.requests)
        calls_from, receipts_from = len(self.bridge.log), len(self.executor.receipts)
        node_from = len(self.node.seen) if self.node is not None else 0
        before = self.state()
        with server_log() as collector:
            turn = self.ask(scenario, replies, question=question, page=page)
            after_turn = self.state()
            node_turn = list(self.node.seen[node_from:]) if self.node is not None else []
            approvals = self.approve(scenario, turn)
            final = self.state()
            surfaces = self.surfaces(
                turn,
                logs=collector.lines,
                frames_from=frames_from,
                audit_from=audit_from,
                requests_from=requests_from,
            )
        chats = [
            r
            for r in lab.model.requests[requests_from:]
            if r.path.split("?", 1)[0].endswith("/chat/completions")
        ]
        calls = self.bridge.log[calls_from:]
        doors = [
            str((e.get("details") or {}).get("tool", ""))
            for e in lab.audit_events()[audit_from:]
            if e.get("action") == "assistant.tool"
            and (e.get("details") or {}).get("reason") in ("arguments", "invalid")
        ]
        proposals = lab.marta.get(
            f"/api/assistant/conversations/{turn['_conversation']}/proposals"
        ).json()
        return Record(
            scenario=scenario.id,
            family=scenario.family,
            forms=scenario.forms,
            turn=turn,
            api_calls=calls,
            receipts=[dict(r) for r in self.executor.receipts[receipts_from:]],
            surfaces=surfaces,
            model_requests=len(chats),
            first_request_tools=_tools_of(chats),
            policy=self.policy_of(calls),
            state_before=before,
            state_after_turn=after_turn,
            state_final=final,
            approvals=approvals,
            scripted_calls=replies_of_calls(list(replies)[: len(chats)]),
            proposals=proposals.get("proposals", proposals)
            if isinstance(proposals, dict)
            else proposals,
            extra={
                "environments": dict(self.environments),
                "node_seen": node_turn,
                "doors": doors,
                "authored": self.authored(replies),
                "audit_actions": sorted(
                    {str(e.get("action")) for e in lab.audit_events()[audit_from:]}
                ),
                **(extra or {}),
            },
        )


def _tools_of(chats: Sequence[Any]) -> list[str] | None:
    if not chats or not isinstance(chats[0].body, dict):
        return None
    tools = chats[0].body.get("tools")
    return [t["function"]["name"] for t in tools] if tools else None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _with_plants(text: str, names: Sequence[str]) -> str:
    """
    Args:
        text: A journal or a build log.
        names: The canaries to put at its end.

    Returns:
        The text with one line per canary after it. The lines go last so that the numbers of the lines a
        scenario quotes do not move.
    """
    body = text if text.endswith("\n") or not text else text + "\n"
    return body + "".join(f"{CANARIES[n].value}\n" for n in names)


def _merged(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in over.items():
        if isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = [*merged[key], *value]
        elif isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _stored(lab: Lab) -> str:
    """Everything the store holds of the conversations, as one text."""
    parts: list[str] = []
    with lab.store._transaction() as cursor:
        for table in (
            "assistant_messages",
            "assistant_turns",
            "assistant_proposals",
            "assistant_conversations",
            "assistant_usage",
            "assistant_actions",
        ):
            cursor.execute(f"SELECT * FROM {table}")  # noqa: S608 - fixed names
            parts.extend(json.dumps(dict(row), default=str) for row in cursor.fetchall())
    return "\n".join(parts)
