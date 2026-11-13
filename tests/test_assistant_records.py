# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a turn of the assistant kept, and the calls it prepared for a person to carry out (item 171).

Both are read by their author alone, in the store's own layer: another account that guesses an
identifier gets the answer of a record that does not exist. A proposal lives through a small number
of states and never goes back, and what comes out of it (the system's own words) is written masked.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.assistant.conversations import Conversations
from noust.core.assistant.records import (
    ProposalError,
    Proposals,
    Turns,
    new_request_id,
)
from noust.core.store import NoustStore

SECRET = "Bearer abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    opened = NoustStore(tmp_path / "noust.db")
    try:
        yield opened
    finally:
        opened.close()
        NoustStore.reset_instance()


@pytest.fixture
def conversation(store: NoustStore) -> str:
    return Conversations(store).create(1).id


def call(**extra: object) -> dict:
    base = {
        "tool": "restart_application",
        "klass": "ask",
        "method": "POST",
        "path": "/api/apps/api.example.org/restart",
        "query": [],
        "body": None,
        "fingerprint": "f" * 64,
        "permission": "apps.operate",
        "environment": "production",
        "server": "",
        "title": {
            "code": "assistant.act.restart_application",
            "params": {"domain": "api.example.org"},
        },
        "effect": "Restart an application",
        "why": {"code": "assistant.proposal.why.policy_propose", "params": {}},
        "facts": [{"name": "domain", "value": "api.example.org"}],
        "plan": None,
        "needs": {"elevation": False, "second_person": False},
    }
    base.update(extra)
    return base


class TestTurns:
    def test_a_turn_begins_running_and_numbers_itself_in_its_conversation(
        self, store: NoustStore, conversation: str
    ) -> None:
        turns = Turns(store)
        first = turns.begin(conversation, 1, question="why?", model="m", location="eu")
        second = turns.begin(conversation, 1, question="and now?", model="m", location="eu")
        assert (first.seq, second.seq) == (1, 2)
        assert first.state == "running" and first.ended_at is None
        assert [t.id for t in turns.for_conversation(conversation, 1)] == [first.id, second.id]

    def test_what_a_turn_keeps_is_saved_and_read_back(
        self, store: NoustStore, conversation: str
    ) -> None:
        turns = Turns(store)
        turn = turns.begin(conversation, 1, question="why?", model="m", location="eu")
        turn.steps = [{"id": "r1", "tool": "get_deployment", "state": "done"}]
        turn.answer = "It is the variable."
        turn.state = "done"
        turn.usage = {"tokens": 10}
        turns.save(turn)
        again = turns.get(turn.id, 1)
        assert again is not None
        assert (again.state, again.answer, again.steps) == (
            "done",
            "It is the variable.",
            turn.steps,
        )
        assert again.ended_at is not None

    def test_it_is_its_authors_alone(self, store: NoustStore, conversation: str) -> None:
        turns = Turns(store)
        turn = turns.begin(conversation, 1, question="why?", model="m", location="eu")
        assert turns.get(turn.id, 2) is None
        assert turns.for_conversation(conversation, 2) == []

    def test_a_turn_that_runs_is_found_and_one_that_ended_is_not(
        self, store: NoustStore, conversation: str
    ) -> None:
        turns = Turns(store)
        turn = turns.begin(conversation, 1, question="why?", model="m", location="eu")
        found = turns.running(conversation, 1)
        assert found is not None and found.id == turn.id
        turn.state = "done"
        turns.save(turn)
        assert turns.running(conversation, 1) is None

    def test_a_turn_left_running_by_a_restart_is_closed_as_what_it_is(
        self, store: NoustStore, conversation: str
    ) -> None:
        turns = Turns(store)
        turn = turns.begin(conversation, 1, question="why?", model="m", location="eu")
        closed = turns.close_stale(conversation, 1, alive=set())
        assert [t.id for t in closed] == [turn.id]
        after = turns.get(turn.id, 1)
        assert after is not None and after.state == "failed"
        assert after.error is not None and after.error["code"] == "assistant.turn.interrupted"

    def test_a_turn_that_is_alive_is_not_closed(self, store: NoustStore, conversation: str) -> None:
        turns = Turns(store)
        turn = turns.begin(conversation, 1, question="why?", model="m", location="eu")
        assert turns.close_stale(conversation, 1, alive={turn.id}) == []

    def test_what_is_kept_is_masked(self, store: NoustStore, conversation: str) -> None:
        turns = Turns(store)
        turn = turns.begin(conversation, 1, question=f"why {SECRET}", model="m", location="eu")
        turn.answer = f"it is {SECRET}"
        turns.save(turn)
        again = turns.get(turn.id, 1)
        assert again is not None
        assert "abcdefghijklmnopqrstuvwxyz0123456789" not in again.question + again.answer


class TestProposals:
    def make(self, store: NoustStore, conversation: str, **extra: object):
        turn = Turns(store).begin(conversation, 1, question="why?", model="m", location="eu")
        return Proposals(store).create(conversation, turn.id, 1, **call(**extra))

    def test_a_proposal_waits_and_keeps_the_call_exactly(
        self, store: NoustStore, conversation: str
    ) -> None:
        proposal = self.make(store, conversation, body={"force": True}, query=[["a", "1"]])
        assert proposal.state == "waiting"
        assert (proposal.method, proposal.path) == ("POST", "/api/apps/api.example.org/restart")
        assert proposal.body == {"force": True} and proposal.query == [["a", "1"]]
        assert proposal.request_id.startswith("ap-")
        again = Proposals(store).get(proposal.id, 1)
        assert again is not None and again.body == {"force": True}

    def test_each_proposal_has_its_own_request_id_which_is_one_a_header_may_carry(self) -> None:
        a, b = new_request_id(), new_request_id()
        assert a != b
        import re

        assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", a)

    def test_it_is_its_authors_alone(self, store: NoustStore, conversation: str) -> None:
        proposal = self.make(store, conversation)
        proposals = Proposals(store)
        assert proposals.get(proposal.id, 2) is None
        assert proposals.for_conversation(conversation, 2) == []
        with pytest.raises(ProposalError):
            proposals.transition(proposal.id, 2, "done", by="x")

    def test_it_goes_forward_and_never_back(self, store: NoustStore, conversation: str) -> None:
        proposal = self.make(store, conversation)
        proposals = Proposals(store)
        running = proposals.transition(proposal.id, 1, "running", by="marta", job_id="j1")
        assert (running.state, running.job_id, running.decided_by) == ("running", "j1", "marta")
        done = proposals.transition(proposal.id, 1, "done", by="marta", status=200, verified=True)
        assert done.state == "done" and done.verified and done.decided_at is not None
        for later in ("waiting", "running", "failed", "rejected", "second"):
            with pytest.raises(ProposalError):
                proposals.transition(proposal.id, 1, later, by="marta")

    def test_a_second_person_decides_later_what_the_first_started(
        self, store: NoustStore, conversation: str
    ) -> None:
        proposal = self.make(store, conversation, klass="two")
        proposals = Proposals(store)
        waiting = proposals.transition(proposal.id, 1, "second", by="marta", approval=41)
        assert waiting.approval == 41
        assert proposals.transition(proposal.id, 1, "done", by="marta").state == "done"

    def test_a_rejection_is_a_state_of_its_own(self, store: NoustStore, conversation: str) -> None:
        proposal = self.make(store, conversation)
        rejected = Proposals(store).transition(proposal.id, 1, "rejected", by="marta")
        assert rejected.state == "rejected"

    def test_what_the_system_said_is_kept_literally_but_masked(
        self, store: NoustStore, conversation: str
    ) -> None:
        proposal = self.make(store, conversation)
        failed = Proposals(store).transition(
            proposal.id,
            1,
            "failed",
            by="marta",
            status=502,
            output=f"curl: (22) 502\nAuthorization: {SECRET}",
            result={"message": "Health gate failed"},
        )
        assert failed.output is not None
        assert (
            "502" in failed.output and "abcdefghijklmnopqrstuvwxyz0123456789" not in failed.output
        )

    def test_an_unknown_state_is_refused(self, store: NoustStore, conversation: str) -> None:
        proposal = self.make(store, conversation)
        with pytest.raises(ProposalError):
            Proposals(store).transition(proposal.id, 1, "approved", by="marta")

    def test_what_happened_since_the_model_last_heard_is_told_once(
        self, store: NoustStore, conversation: str
    ) -> None:
        a = self.make(store, conversation)
        b = self.make(store, conversation, path="/api/apps/x/stop")
        proposals = Proposals(store)
        proposals.transition(a.id, 1, "done", by="marta")
        assert [p.id for p in proposals.untold(conversation, 1)] == [a.id]
        proposals.mark_told([a.id])
        assert proposals.untold(conversation, 1) == []
        proposals.transition(b.id, 1, "rejected", by="marta")
        assert [p.id for p in proposals.untold(conversation, 1)] == [b.id]

    def test_one_done_by_the_assistant_itself_is_born_done(
        self, store: NoustStore, conversation: str
    ) -> None:
        proposal = self.make(store, conversation, klass="alone", state="done", status=200)
        assert (proposal.klass, proposal.state) == ("alone", "done")

    def test_deleting_the_conversation_takes_its_turns_and_proposals_with_it(
        self, store: NoustStore, conversation: str
    ) -> None:
        proposal = self.make(store, conversation)
        assert Conversations(store).delete(conversation, 1)
        assert Proposals(store).get(proposal.id, 1) is None
        assert Turns(store).for_conversation(conversation, 1) == []
