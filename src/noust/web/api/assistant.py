# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The built-in assistant's settings, probe, usage and conversations (item 171).

A client of :mod:`noust.core.assistant` like ``noust assistant``: an endpoint translates HTTP to a
call of the settings, the probe, the meter or the conversation store, and back (rule 3). The
assistant is off until an administrator turns it on, and this module imports none of it until a
request asks: the routes exist so that the API and the console know them, the work is imported in
the handler.

- ``GET /api/assistant/settings`` shows what is chosen, never the key (only that there is one and
  since when). ``PUT`` changes it, and is ``settings.manage`` in sudo mode: it is the act that
  points a machine that runs as root at a provider and turns it on.
- ``POST /api/assistant/probe`` asks the provider what it can do and says what it found.
- ``POST /api/assistant/off`` turns it off and revokes every token it holds, at once.
- ``GET /api/assistant/usage`` shows what was spent and the limits, for a conversation and for the
  month; ``GET /api/assistant/presets`` lists the providers by name.
- ``/api/assistant/conversations`` are a person's own conversations: listed, read and deleted by
  their author alone.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core.audit import record as audit_record
from noust.core.exceptions import PermissionError as NoustPermissionError
from noust.core.server_text import say
from noust.web.api.assistant_session import AssistantReason
from noust.web.api.auth import get_current_session
from noust.web.api.deps import CodedText, NoustErrorRoute, caller_account, require_elevated
from noust.web.auth import actor_label
from noust.web.permissions.principal import actor_of
from noust.web.pydantic_compat import dump_set_fields

logger = logging.getLogger(__name__)

router = APIRouter(route_class=NoustErrorRoute)


class SentenceOut(CodedText):
    """
    One thing the probe found, as a sentence with a code.

    Attributes:
        text: The sentence in English, always sent.
        hint: How to fix it in English, when the sentence has a fix.
    """

    text: str
    hint: str | None = None


class ProbeOut(BaseModel):
    """
    What the probe found of a provider.

    Attributes:
        reachable: The provider answered something.
        model_found: The model is in the provider's list; None when it gives no list.
        tools: The model made the test call to a tool well formed; None when it was not tried.
            When it is not True the assistant only explains: it neither investigates nor acts.
        models: The models the provider lists.
        sentences: What it found, in order.
    """

    reachable: bool
    model_found: bool | None = None
    tools: bool | None = None
    models: list[str] = Field(default_factory=list)
    sentences: list[SentenceOut] = Field(default_factory=list)


class PriceModel(BaseModel):
    """
    What a model costs per million tokens, in the currency of the settings.

    Attributes:
        input_per_million: What a million tokens sent cost.
        output_per_million: What a million tokens said cost.
    """

    input_per_million: float
    output_per_million: float


class AssistantSettingsOut(BaseModel):
    """
    What is chosen for the assistant. The key is never in it.

    Attributes:
        enabled: The assistant is on.
        configured: A provider and a model were chosen.
        preset: The provider preset it was chosen from, or empty.
        base_url: The provider's address.
        model: The model's name.
        has_key: A key is kept (sealed); it is never given back.
        key_set_at: When the key was set.
        location: Where the data ends up: ``machine``, ``network``, ``eu``, ``outside`` or
            ``unknown`` (the provider's documentation does not say).
        flow_reason: Why a provider outside is an authorized flow, under the ENS profile.
        mask_personal: Emails and addresses are disguised with markers before they are sent: always
            for a provider that is not on this machine or the operator's network.
        limits: The ceilings, each always a number.
        prices: What each model costs.
        currency: What the prices and the spending limits are in.
        probe: What the probe last found; None when it has not run since the provider changed.
        probed_at: When.
        calls_tools: The model was found to call tools, so it may investigate.
        updated_at: When the settings last changed.
        updated_by: Who changed them.
    """

    enabled: bool
    configured: bool
    preset: str
    base_url: str
    model: str
    has_key: bool
    key_set_at: str | None = None
    location: str
    flow_reason: str
    mask_personal: bool
    limits: dict[str, float]
    prices: dict[str, PriceModel]
    currency: str
    probe: ProbeOut | None = None
    probed_at: str | None = None
    calls_tools: bool
    updated_at: str | None = None
    updated_by: str


class AssistantSettingsIn(BaseModel):
    """
    What to change; what is left out stays.

    Attributes:
        enabled: True turns the assistant on (after a probe found the provider answering), False
            turns it off and revokes its tokens.
        preset: A provider preset (``GET /api/assistant/presets``): it fills the address.
        base_url: The provider's address, up to its version.
        model: The model's name.
        api_key: The key; an empty text takes it away. Sealed when stored, never given back.
        location: Where the data ends up, for a public provider the operator has a contract with.
        flow_reason: Why a provider outside is an authorized flow.
        mask_personal: Whether emails and addresses are disguised; only a provider of one's own
            may have it off.
        limits: Limits to change, by name; the others stay.
        prices: The whole price table.
        currency: What the prices are in.
        allow_private_host: Consent that this server calls the provider's host though it is on
            this machine or the network: the host is listed in ``security.allow_private_hosts``.
        probe: Probe the provider after saving a change to it (the default).
    """

    enabled: bool | None = None
    preset: str | None = Field(default=None, max_length=40)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=600, repr=False)
    location: str | None = Field(default=None, max_length=20)
    flow_reason: str | None = Field(default=None, max_length=500)
    mask_personal: bool | None = None
    limits: dict[str, float] | None = None
    prices: dict[str, PriceModel] | None = None
    currency: str | None = Field(default=None, max_length=8)
    allow_private_host: bool = False
    probe: bool = True


class PresetOut(BaseModel):
    """
    A provider by name.

    Attributes:
        id: Its stable name.
        name: What it is called.
        base_url: Its address, read on its documentation.
        needs_key: Whether it asks for a key.
        location: Where the data ends up, as its documentation says.
        docs: The page the address was read from.
    """

    id: str
    name: str
    base_url: str
    needs_key: bool
    location: str
    docs: str


class PresetsOut(BaseModel):
    """
    The providers on offer.

    Attributes:
        presets: Them.
        locations: Every place the data may end up.
        limits: The default limits.
    """

    presets: list[PresetOut]
    locations: list[str]
    limits: dict[str, float]


class TotalsOut(BaseModel):
    """
    What a stretch of conversation used.

    Attributes:
        steps: Requests to the provider.
        prompt_tokens: Tokens sent.
        completion_tokens: Tokens said.
        spend: The cost of the steps that have a price.
        unpriced_steps: Steps whose model had no price, which spend leaves out.
        estimated_steps: Steps whose tokens are an estimate.
    """

    steps: int
    prompt_tokens: int
    completion_tokens: int
    spend: float
    unpriced_steps: int
    estimated_steps: int


class UsageOut(BaseModel):
    """
    What was used, and the limits it runs under.

    Attributes:
        conversation: One conversation's totals, when one was asked for and is the caller's.
        month: The calendar month's totals, everyone's together.
        actions_last_hour: The changes the assistant made for the caller in the last hour.
        limits: The limits in force.
        currency: What spending is in.
        month_start: The first moment of the month counted (UTC).
        priced_models: The models that have a price: for any other, spending is not limited.
        notes: What to know about the numbers (no price for the model, an estimate).
    """

    conversation: TotalsOut | None = None
    month: TotalsOut
    actions_last_hour: int | None = None
    limits: dict[str, float]
    currency: str
    month_start: str
    priced_models: list[str]
    notes: list[AssistantReason] = Field(default_factory=list)


class OffOut(BaseModel):
    """
    The result of turning the assistant off.

    Attributes:
        revoked: How many tokens it held, all revoked.
        settings: The settings as they are now.
    """

    revoked: int
    settings: AssistantSettingsOut


class ConversationOut(BaseModel):
    """
    One conversation, without its messages.

    Attributes:
        id: Its identifier.
        title: What it is called.
        created_at: When it began (UTC).
        updated_at: When it was last used.
    """

    id: str
    title: str
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    """
    One message of a conversation, as its author reads it.

    Attributes:
        seq: Its place, from 1.
        role: ``user``, ``assistant`` or ``tool``.
        content: The text, with the disguised emails and addresses put back.
        tool_calls: For an assistant message that called tools: ``id``, ``name`` and ``arguments``.
        tool_call_id: For a tool message, the call it answers.
        created_at: When it was written.
    """

    seq: int
    role: str
    content: str
    tool_calls: list[dict[str, str]] | None = None
    tool_call_id: str | None = None
    created_at: str


class ConversationDetailOut(ConversationOut):
    """
    A conversation with its messages.

    Attributes:
        messages: In order.
    """

    messages: list[MessageOut]


class DeletedOut(BaseModel):
    """
    The result of deleting a conversation.

    Attributes:
        deleted: Whether it was deleted.
    """

    deleted: bool


# -- helpers ---------------------------------------------------------------------------------------


def _sentences(items: Any) -> list[SentenceOut]:
    """
    Args:
        items: ``ServerText`` objects.

    Returns:
        Them as the API carries a sentence.
    """
    return [SentenceOut(**s.to_dict(), text=s.text, hint=s.hint) for s in items]


def _settings_out(settings: Any) -> AssistantSettingsOut:
    """
    Args:
        settings: A :class:`noust.core.assistant.settings.Settings`.

    Returns:
        It as the API shows it.
    """
    from noust.core.assistant.probe import ProbeResult

    document = settings.to_public()
    probe = ProbeResult.from_dict(settings.probe) if settings.probe else None
    document["probe"] = (
        ProbeOut(
            reachable=probe.reachable,
            model_found=probe.model_found,
            tools=probe.tools,
            models=probe.models,
            sentences=_sentences(probe.sentences),
        )
        if probe is not None
        else None
    )
    return AssistantSettingsOut(**document)


def _stop_turns() -> None:
    """Ask every turn that is being answered to stop, and forget the tokens held for them: it was turned off."""
    from noust.web.assistant.turns import registry, tokens

    registry().cancel_all()
    tokens().clear()


def _account_id(session: dict[str, Any]) -> int:
    """
    Args:
        session: The authenticated payload.

    Returns:
        The account the caller acts as.

    Raises:
        NoustPermissionError: It is not an account's (the master token, a fleet token).
    """
    account = caller_account(session)
    if account is None:
        raise NoustPermissionError.from_text(say("assistant.unavailable.account"))
    return int(account.id)


# -- settings --------------------------------------------------------------------------------------


@router.get("/settings", response_model=AssistantSettingsOut)
def get_settings(session: Annotated[dict, Depends(get_current_session)]) -> AssistantSettingsOut:
    """
    Show what is chosen for the assistant, never the key.

    Args:
        session: The authenticated session.

    Returns:
        The settings.
    """
    from noust.core.assistant.settings import AssistantSettings

    return _settings_out(AssistantSettings().read())


@router.put("/settings", response_model=AssistantSettingsOut)
def put_settings(
    body: AssistantSettingsIn, session: Annotated[dict, Depends(require_elevated)]
) -> AssistantSettingsOut:
    """
    Choose the provider, the limits and the prices, and turn the assistant on or off, in sudo mode.

    A change to the provider is probed (unless ``probe`` is false), and turning it on needs a probe
    that found the provider answering. Turning it off revokes every token the assistant holds.

    Args:
        body: What to change; what is left out stays.
        session: The authenticated, elevated session.

    Returns:
        The settings as they are now.

    Raises:
        ProviderAddressError: The address cannot be called.
        ValidationError: A value is not acceptable.
    """
    from noust.core.assistant.probe import probe_configured
    from noust.core.assistant.settings import AssistantSettings
    from noust.web.assistant.identity import AssistantIdentity

    fields = {k: v for k, v in dump_set_fields(body).items() if v is not None}
    enabled = fields.pop("enabled", None)
    probe_after = bool(fields.pop("probe", True))
    consent = bool(fields.pop("allow_private_host", False))
    who = actor_label(session)
    store = AssistantSettings()
    if fields or consent:
        store.update(actor=who, allow_private_host=consent, **fields)
        audit_record(
            "assistant.settings",
            actor=actor_of(session),
            target="setting:assistant",
            details={"changed": sorted(fields), "key_set": "api_key" in fields},
        )
    current = store.read()
    if (
        probe_after
        and current.configured
        and {"preset", "base_url", "model", "api_key"} & set(fields)
    ):
        result = probe_configured(store, current)
        audit_record(
            "assistant.probe",
            actor=actor_of(session),
            target="setting:assistant",
            outcome="ok" if result.reachable else "failure",
            details={"reachable": result.reachable, "tools": result.tools},
        )
    if enabled is True:
        store.enable(actor=who)
        audit_record("assistant.enable", actor=actor_of(session), target="setting:assistant")
    elif enabled is False:
        store.disable(actor=who)
        audit_record("assistant.disable", actor=actor_of(session), target="setting:assistant")
        AssistantIdentity().revoke_all(actor=actor_of(session))
        _stop_turns()
    return _settings_out(store.read())


@router.post("/probe", response_model=ProbeOut)
def probe(session: Annotated[dict, Depends(get_current_session)]) -> ProbeOut:
    """
    Ask the provider what it can do and say what was found.

    Whether it answers, whether it has the model, whether the model makes a well formed call to a
    toy tool (if not, the assistant only explains) and, for a model on this machine, an honest word
    about its memory. What is found is kept with the settings.

    Args:
        session: The authenticated session.

    Returns:
        What the probe found.

    Raises:
        ValidationError: No provider was chosen.
    """
    from noust.core.assistant.probe import probe_configured
    from noust.core.assistant.settings import AssistantSettings
    from noust.core.exceptions import ValidationError

    store = AssistantSettings()
    current = store.read()
    if not current.configured:
        raise ValidationError(
            "Choose a provider and a model before probing it",
            details="Give an address and a model, or a preset and a model.",
        )
    result = probe_configured(store, current)
    audit_record(
        "assistant.probe",
        actor=actor_of(session),
        target="setting:assistant",
        outcome="ok" if result.reachable else "failure",
        details={"reachable": result.reachable, "tools": result.tools},
    )
    return ProbeOut(
        reachable=result.reachable,
        model_found=result.model_found,
        tools=result.tools,
        models=result.models,
        sentences=_sentences(result.sentences),
    )


@router.post("/off", response_model=OffOut)
def turn_off(session: Annotated[dict, Depends(require_elevated)]) -> OffOut:
    """
    Turn the assistant off and revoke every token it holds, at once, in sudo mode.

    What a conversation in progress tries next fails with the sentence ``assistant.off``.

    Args:
        session: The authenticated, elevated session.

    Returns:
        How many tokens were revoked, and the settings as they are now.
    """
    from noust.core.assistant.settings import AssistantSettings
    from noust.web.assistant.identity import AssistantIdentity

    store = AssistantSettings()
    store.disable(actor=actor_label(session))
    audit_record("assistant.disable", actor=actor_of(session), target="setting:assistant")
    revoked = AssistantIdentity().revoke_all(actor=actor_of(session))
    _stop_turns()
    return OffOut(revoked=revoked, settings=_settings_out(store.read()))


@router.get("/presets", response_model=PresetsOut)
def presets(session: Annotated[dict, Depends(get_current_session)]) -> PresetsOut:
    """
    List the providers by name, each with the address read on its documentation.

    Args:
        session: The authenticated session.

    Returns:
        The presets, the places the data may end up and the default limits.
    """
    from noust.core.assistant import presets as table
    from noust.core.assistant.limits import Limits

    return PresetsOut(
        presets=[
            PresetOut(
                id=p.id,
                name=p.name,
                base_url=p.base_url,
                needs_key=p.needs_key,
                location=p.location,
                docs=p.docs,
            )
            for p in table.PRESETS
        ],
        locations=list(table.LOCATIONS),
        limits={k: float(v) for k, v in Limits().to_dict().items()},
    )


# -- usage -----------------------------------------------------------------------------------------


@router.get("/usage", response_model=UsageOut)
def usage(
    session: Annotated[dict, Depends(get_current_session)],
    conversation: Annotated[
        str | None, Query(description="A conversation of the caller's, to give its totals too")
    ] = None,
) -> UsageOut:
    """
    Show what the assistant cost and the limits it runs under.

    Args:
        session: The authenticated session.
        conversation: One of the caller's conversations.

    Returns:
        The month's totals (everyone's together), the conversation's when asked for and its
        author's, what the caller made it do in the last hour, and the limits.
    """
    from noust.core.assistant.conversations import Conversations
    from noust.core.assistant.limits import UsageMeter
    from noust.core.assistant.settings import AssistantSettings

    account_id = _account_id(session)
    current = AssistantSettings().read()
    chosen = (
        conversation
        if conversation is not None and Conversations().get(conversation, account_id) is not None
        else None
    )
    meter = UsageMeter(current.limits, current.prices, current.currency)
    report = meter.report(conversation_id=chosen, account_id=account_id)
    notes = []
    if current.model and current.model not in current.prices:
        notes.append(say("assistant.limit.no_price", model=current.model))
    if report.month.estimated_steps:
        notes.append(say("assistant.limit.estimated"))

    def totals(item: Any) -> TotalsOut:
        return TotalsOut(**vars(item))

    return UsageOut(
        conversation=totals(report.conversation) if report.conversation is not None else None,
        month=totals(report.month),
        actions_last_hour=report.actions_last_hour,
        limits={k: float(v) for k, v in report.limits.to_dict().items()},
        currency=report.currency,
        month_start=report.month_start,
        priced_models=list(report.priced_models),
        notes=[AssistantReason(**n.to_dict(), text=n.text, hint=n.hint) for n in notes],
    )


# -- conversations ---------------------------------------------------------------------------------


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    session: Annotated[dict, Depends(get_current_session)],
) -> list[ConversationOut]:
    """
    List the caller's own conversations with the assistant, the latest first.

    Args:
        session: The authenticated session.

    Returns:
        Them. Nobody else's is ever in the list.
    """
    from noust.core.assistant.conversations import Conversations

    return [
        ConversationOut(**vars(c)) for c in Conversations().recent(_account_id(session), limit=200)
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(
    conversation_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> ConversationDetailOut:
    """
    Read one of the caller's conversations.

    Args:
        conversation_id: Its identifier.
        session: The authenticated session.

    Returns:
        The conversation with its messages, the disguised emails and addresses put back.

    Raises:
        ConversationNotFound: There is none, or it is another person's (the same answer).
    """
    from noust.core.assistant.conversations import ConversationNotFound, Conversations
    from noust.core.assistant.masking import Masker

    account_id = _account_id(session)
    chats = Conversations()
    found = chats.get(conversation_id, account_id)
    if found is None:
        raise ConversationNotFound(
            "There is no such conversation", details="It may have been deleted."
        )
    masker = Masker(mask_personal=True, markers=chats.markers(conversation_id, account_id))
    return ConversationDetailOut(
        **vars(found),
        messages=[
            MessageOut(
                seq=m.seq,
                role=m.role,
                content=masker.restore(m.content),
                tool_calls=(
                    [{**c, "arguments": masker.restore(c["arguments"])} for c in m.tool_calls]
                    if m.tool_calls
                    else None
                ),
                tool_call_id=m.tool_call_id,
                created_at=m.created_at,
            )
            for m in chats.messages(conversation_id, account_id)
        ],
    )


@router.delete("/conversations/{conversation_id}", response_model=DeletedOut)
def delete_conversation(
    conversation_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> DeletedOut:
    """
    Delete one of the caller's conversations, and take back the token it acted with.

    What it cost stays counted: deleting it does not give the month's spending back.

    Args:
        conversation_id: Its identifier.
        session: The authenticated session.

    Returns:
        Whether it was deleted; False for one that does not exist or is another person's.
    """
    from noust.core.assistant.conversations import Conversations
    from noust.web.assistant.identity import AssistantIdentity

    deleted = Conversations().delete(conversation_id, _account_id(session))
    if deleted:
        AssistantIdentity().revoke_conversation(conversation_id)
        audit_record(
            "assistant.conversation.delete",
            actor=actor_of(session),
            target=f"conversation:{conversation_id[:8]}",
        )
    return DeletedOut(deleted=deleted)


__all__ = ["router"]
