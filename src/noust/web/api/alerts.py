# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/alerts``: rules on the metrics, and which of them are firing.

A thin client of :class:`~noust.core.alerts.rules.AlertRules`, which
``noust alert`` uses too. A rule is a metric, a limit, a duration, a hysteresis
and a cooldown (:mod:`noust.core.alerts.model`); the monitor judges them every
scan against the recorded history and sends ``alert_firing`` and
``alert_resolved`` through the notification channels.

The limit travels as a number in the metric's unit or as the text an operator
types (``90%``, ``512MB``, ``2MB/s``), read with the unit of the metric the rule
names. Literal paths come before the parametrised ones.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.alerts import (
    METRICS,
    AlertRule,
    AlertRules,
    AlertState,
    parse_threshold,
)
from noust.core.alerts.model import DEFAULT_COOLDOWN_S, DEFAULT_DURATION_S
from noust.core.exceptions import ValidationError
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(get_current_session)]


class MetricModel(BaseModel):
    """
    One measurement a rule may watch.

    Attributes:
        id: What a rule names.
        scope: ``server`` or ``app``.
        unit: ``percent``, ``bytes``, ``bytes_s``, ``number`` or ``per_min``.
    """

    id: str
    scope: str
    unit: str


class StateModel(BaseModel):
    """
    Where one subject of one rule stands.

    Attributes:
        subject: The application, or empty for the server.
        state: ``firing`` or ``ok``.
        since: Epoch seconds the state began.
        last_value: The reading that last decided it.
        announced: Whether the current firing was told to the channels.
    """

    subject: str
    state: str
    since: float
    last_value: float | None = None
    announced: bool = False


class RuleModel(BaseModel):
    """
    One alert rule.

    Attributes:
        name: Its identifier.
        metric: What it watches.
        scope: ``server`` or ``app``.
        unit: The metric's unit.
        comparator: ``>``, ``>=``, ``<`` or ``<=``.
        threshold: The limit, in the metric's unit.
        app_domain: The application, or null (the server, or every application).
        duration_s: How long the limit must be breached before it fires.
        hysteresis: How far inside the limit it must come to clear.
        cooldown_s: The least time between two messages for one subject.
        severity: ``warning`` or ``failed``.
        enabled: Whether the monitor judges it.
        remind: Say it again every cooldown while it keeps firing.
        note: Free text.
        states: Where each subject of the rule stands.
    """

    name: str
    metric: str
    scope: str
    unit: str
    comparator: str
    threshold: float
    app_domain: str | None = None
    duration_s: int
    hysteresis: float
    cooldown_s: int
    severity: str
    enabled: bool
    remind: bool
    note: str = ""
    states: list[StateModel] = Field(default_factory=list)


class RuleBody(BaseModel):
    """
    What a rule is, to create or replace it.

    Attributes:
        metric: What to watch (``cpu``, ``app_5xx``...).
        comparator: ``>``, ``>=``, ``<`` or ``<=``.
        threshold: The limit: a number in the metric's unit, or text such as
            ``90%``, ``512MB`` or ``2MB/s``.
        app_domain: The application, for an application metric; empty for every
            application.
        duration_s: How long it must hold before it fires (60 to 86400).
        hysteresis: How far inside the limit it must come to clear.
        cooldown_s: The least time between two messages for one subject.
        severity: ``warning`` or ``failed``.
        enabled: Whether the monitor judges it.
        remind: Say it again every cooldown while it keeps firing.
        note: Free text, 500 characters at most.
    """

    metric: str
    comparator: str = ">"
    threshold: float | str
    app_domain: str | None = None
    duration_s: int = DEFAULT_DURATION_S
    hysteresis: float = 0.0
    cooldown_s: int = DEFAULT_COOLDOWN_S
    severity: str = "warning"
    enabled: bool = True
    remind: bool = False
    note: str = ""


class RuleListResponse(BaseModel):
    """
    The rules.

    Attributes:
        items: Every rule, by name, with where each subject stands.
        metrics: What a rule may watch.
        firing: How many subjects are firing now.
    """

    items: list[RuleModel]
    metrics: list[MetricModel]
    firing: int


def _rules() -> AlertRules:
    """
    Returns:
        The rules over the process-wide store. Module level so a test can
        stand in another.
    """
    return AlertRules()


def _model(rule: AlertRule, states: list[AlertState]) -> RuleModel:
    """
    Args:
        rule: A rule.
        states: Where its subjects stand.

    Returns:
        The response model.
    """
    data = rule.to_dict()
    for key in ("id", "created_at", "updated_at"):
        data.pop(key)
    return RuleModel(**data, states=[StateModel(**state.to_dict()) for state in states])


def _metrics() -> list[MetricModel]:
    """
    Returns:
        The metrics a rule may watch.
    """
    return [MetricModel(id=m.id, scope=m.scope, unit=m.unit) for m in METRICS.values()]


@router.get("/rules", response_model=RuleListResponse)
def list_rules(session: Session) -> RuleListResponse:
    """
    List the alert rules and where each stands.

    Args:
        session: The authenticated session.

    Returns:
        The rules, the metrics a rule may watch and how many subjects fire.
    """
    rules = _rules()
    states: dict[int, list[AlertState]] = {}
    for state in rules.states():
        states.setdefault(state.rule_id, []).append(state)
    items = [_model(rule, states.get(rule.id or 0, [])) for rule in rules.rules()]
    firing = sum(1 for item in items for state in item.states if state.state == "firing")
    return RuleListResponse(items=items, metrics=_metrics(), firing=firing)


@router.put("/rules/{name}", response_model=RuleModel)
def save_rule(name: str, body: RuleBody, session: Session) -> RuleModel:
    """
    Create a rule or replace the one of that name.

    Replacing it forgets what the old limit had decided: a rule changed from
    90 to 95 is not firing because of 92.

    Args:
        name: The rule's name.
        body: What it is.
        session: The authenticated session.

    Returns:
        The rule as stored.

    Raises:
        ValidationError: 400 naming the field that is not acceptable.
    """
    spec = METRICS.get(body.metric)
    if spec is None:
        raise ValidationError(
            f"Unknown metric: {body.metric!r}",
            details=f"The metrics are: {', '.join(METRICS)}.",
            field="metric",
        )
    rules = _rules()
    saved = rules.save(
        AlertRule(
            name=name,
            metric=body.metric,
            comparator=body.comparator,
            threshold=parse_threshold(body.threshold, spec.unit),
            app_domain=body.app_domain or None,
            duration_s=body.duration_s,
            hysteresis=body.hysteresis,
            cooldown_s=body.cooldown_s,
            severity=body.severity,
            enabled=body.enabled,
            remind=body.remind,
            note=body.note,
        ),
        actor=actor_label(session),
    )
    audit.record(
        "alert.rule.set",
        actor=session_actor(session),
        target=f"alert:{name}",
        details={
            "metric": saved.metric,
            "comparator": saved.comparator,
            "threshold": saved.threshold,
        },
    )
    return _model(saved, rules.states(saved.id))


def _switch(name: str, enabled: bool, session: dict[str, Any]) -> RuleModel:
    """
    Turn a rule on or off.

    Args:
        name: The rule's name.
        enabled: Whether the monitor judges it.
        session: The authenticated session.

    Returns:
        The rule as it is now.

    Raises:
        HTTPException: 404 when there is no such rule.
    """
    rules = _rules()
    rule = rules.set_enabled(name, enabled, actor=actor_label(session))
    if rule is None:
        raise HTTPException(status_code=404, detail=f"No alert rule named {name!r}")
    audit.record(
        "alert.rule.set",
        actor=session_actor(session),
        target=f"alert:{name}",
        details={"enabled": rule.enabled},
    )
    return _model(rule, rules.states(rule.id))


@router.post("/rules/{name}/enable", response_model=RuleModel)
def enable_rule(name: str, session: Session) -> RuleModel:
    """
    Turn a rule on.

    Args:
        name: The rule's name.
        session: The authenticated session.

    Returns:
        The rule as it is now.
    """
    return _switch(name, True, session)


@router.post("/rules/{name}/disable", response_model=RuleModel)
def disable_rule(name: str, session: Session) -> RuleModel:
    """
    Turn a rule off, forgetting what it had decided.

    Args:
        name: The rule's name.
        session: The authenticated session.

    Returns:
        The rule as it is now.
    """
    return _switch(name, False, session)


@router.delete("/rules/{name}", status_code=204)
def remove_rule(name: str, session: Session) -> None:
    """
    Remove a rule and what it remembered.

    Args:
        name: The rule's name.
        session: The authenticated session.

    Raises:
        HTTPException: 404 when there is no such rule.
    """
    if not _rules().remove(name):
        raise HTTPException(status_code=404, detail=f"No alert rule named {name!r}")
    audit.record("alert.rule.remove", actor=session_actor(session), target=f"alert:{name}")
