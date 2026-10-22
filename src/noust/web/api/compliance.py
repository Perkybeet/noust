# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/compliance``: the frameworks, the controls and how to change them.

A thin client of :class:`noust.core.compliance.manager.ComplianceManager`, the
manager ``noust compliance`` drives: every rule about what a change does, and what it
refuses, is there (rule 3). What this module adds is what only the console knows: the
caller's permission, sudo mode and the audit record with the person behind it.

- ``GET /api/compliance`` says what is on and what forces what (``settings.read``:
  anybody signed in sees which frameworks protect the server).
- ``POST /api/compliance/plan`` says what a change would do, writing nothing
  (``security.manage``).
- ``PUT /api/compliance`` makes it (``security.manage``). Turning a framework on, or a
  control stricter, needs nothing more; **turning a framework off, or loosening a
  control, needs sudo mode** and is recorded. A change with warnings (nobody left to
  govern accounts, for one) is only made once the caller acknowledged them.

A central never changes a node's frameworks: ``security.manage`` is not a permission a
fleet request can hold.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from noust.core.compliance import check as compliance_check
from noust.core.compliance.manager import ComplianceManager, Plan
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, ensure_elevated
from noust.web.auth import audit_event, get_client_ip

router = APIRouter(route_class=NoustErrorRoute)


class FrameworkChange(BaseModel):
    """
    What enabling a framework would change in one control.

    Attributes:
        control: The control.
        before: The value in force now.
        after: The value the framework asks.
        clauses: The clauses that value serves.
    """

    control: str
    before: Any = None
    after: Any = None
    clauses: list[str]


class FrameworkInfo(BaseModel):
    """
    A compliance framework and where it stands.

    Attributes:
        id: Its name in ``security.frameworks``.
        name: How it is called.
        family: ``ens``, ``iso``, ``nis2``, ``cis``, ``soc2`` or ``gdpr``.
        level: The level inside the family, or empty.
        summary: What it is, in a sentence.
        reference: The law or the standard it refers to.
        enabled: Whether it is on.
        source: ``setting`` (it is in ``security.frameworks``) or ``profile`` (only
            ``security.profile`` says so), when it is on.
        turns_on: The controls it asks for more than the default of.
        requirement_count: How many controls it asks anything of.
        changes: What enabling it would change right now; empty when it is on.
        organisational: What stays yours.
        not_covered: Technical requirements Noust does not enforce.
        includes: A framework this one contains.
    """

    id: str
    name: str
    family: str
    level: str
    summary: str
    reference: str
    enabled: bool
    source: str | None = None
    turns_on: list[str]
    requirement_count: int
    changes: list[FrameworkChange]
    organisational: list[str]
    not_covered: list[str]
    includes: str | None = None


class ControlInfo(BaseModel):
    """
    A control as it stands on this server.

    Attributes:
        id: Its name.
        group: ``access``, ``accountability``, ``backup`` or ``network``.
        kind: ``flag``, ``choice``, ``min`` or ``max``.
        standard: The default.
        key: The configuration key of its own setting.
        choices: For a choice, from the weakest to the strictest.
        unit: What a number counts.
        check_only: Whether nothing refuses it; only the check reads it.
        value: The value in force.
        operator: The operator's own setting.
        floor: What the enabled frameworks leave of it.
        forced_by: The enabled frameworks that ask anything of it, strictest first.
        locked: Whether one does, so it cannot be loosened.
        enabled: Whether it asks for more than the default.
    """

    id: str
    group: str
    kind: str
    standard: Any = None
    key: str
    choices: list[str]
    unit: str
    check_only: bool
    value: Any = None
    operator: Any = None
    floor: Any = None
    forced_by: list[str]
    locked: bool
    enabled: bool


class ComplianceState(BaseModel):
    """
    The frameworks and the controls.

    Attributes:
        frameworks: Every framework, in catalog order.
        controls: Every control, in catalog order.
        legacy_profile: What ``security.profile`` holds.
        unknown: Names in ``security.frameworks`` this version does not know.
    """

    frameworks: list[FrameworkInfo]
    controls: list[ControlInfo]
    legacy_profile: str
    unknown: list[str]


class EffectInfo(BaseModel):
    """
    What a change does to one control.

    Attributes:
        control: The control.
        before: The value in force now.
        after: The value in force once the change is made.
        forced_by: The frameworks that will ask anything of it.
        clauses: For each of them, the clauses it serves.
    """

    control: str
    before: Any = None
    after: Any = None
    forced_by: list[str]
    clauses: dict[str, list[str]]


class WarningInfo(BaseModel):
    """
    Something about this server a change would affect.

    Attributes:
        id: What it is.
        count: How many it concerns, when it counts.
    """

    id: str
    count: int | None = None


class PlanInfo(BaseModel):
    """
    What a change would do, before it is made.

    Attributes:
        enable: Frameworks that will be turned on.
        disable: Frameworks that will be turned off.
        settings: Control to the value its setting will hold.
        enabled_after: The frameworks on afterwards.
        effects: The controls whose value in force changes.
        loosens: Whether anything protects less afterwards, which asks sudo mode.
        warnings: What a person must acknowledge.
    """

    enable: list[str]
    disable: list[str]
    settings: dict[str, Any]
    enabled_after: list[str]
    effects: list[EffectInfo]
    loosens: bool
    warnings: list[WarningInfo]


class ChangeRequest(BaseModel):
    """
    A change of frameworks and controls.

    Attributes:
        enable: Frameworks to turn on.
        disable: Frameworks to turn off.
        controls: Control to the value wanted for its setting: on or off, a number,
            a choice, or null for no limit.
        acknowledge: The caller saw the warnings of the plan and goes ahead.
    """

    enable: list[str] = Field(default_factory=list)
    disable: list[str] = Field(default_factory=list)
    controls: dict[str, Any] = Field(default_factory=dict)
    acknowledge: bool = False


class ChangeResult(BaseModel):
    """
    What a change did.

    Attributes:
        enabled_before: The frameworks on before.
        enabled_after: The frameworks on after.
        settings: Control to the value its setting now holds, for the ones changed.
        state: The picture afterwards.
    """

    enabled_before: list[str]
    enabled_after: list[str]
    settings: dict[str, Any]
    state: ComplianceState


class CheckFinding(BaseModel):
    """
    One control of one framework, judged.

    Attributes:
        framework: The framework.
        control: The control.
        clauses: The clauses it serves, ``id: title``.
        status: ``ok``, ``warning``, ``fail`` or ``n/a``.
        required: What the framework asks.
        actual: The value in force.
        summary: What was found.
        evidence: Values and facts, verbatim.
        remediation: What to do.
    """

    framework: str
    control: str
    clauses: list[str]
    status: str
    required: Any = None
    actual: Any = None
    summary: str
    evidence: list[str]
    remediation: str


class CheckFramework(BaseModel):
    """
    One framework's findings.

    Attributes:
        framework: Its id.
        name: How it is called.
        enabled: Whether it is on.
        verdict: The worst status.
        counts: Findings per status.
        findings: One per control it asks of.
        organisational: What stays yours.
        not_covered: Technical requirements Noust does not enforce.
    """

    framework: str
    name: str
    enabled: bool
    verdict: str
    counts: dict[str, int]
    findings: list[CheckFinding]
    organisational: list[str]
    not_covered: list[str]


class CheckResponse(BaseModel):
    """
    The result of the compliance check.

    Attributes:
        verdict: The worst verdict among the frameworks.
        frameworks: One per framework judged.
        errors: Areas that could not be read.
        disclaimer: What a pass does and does not mean.
    """

    verdict: str
    frameworks: list[CheckFramework]
    errors: dict[str, str]
    disclaimer: str


def _state() -> dict[str, Any]:
    """
    Returns:
        The frameworks and controls as they stand, in the response's shape.
    """
    state = ComplianceManager().describe()
    for entry in state["frameworks"]:
        entry.setdefault("includes", None)
    return state


def _plan(manager: ComplianceManager, body: ChangeRequest) -> Plan:
    """
    Args:
        manager: The manager.
        body: What was asked.

    Returns:
        The plan.
    """
    return manager.plan(enable=body.enable, disable=body.disable, settings=body.controls)


@router.get("", response_model=ComplianceState)
def get_compliance(_session: dict[str, Any] = Depends(get_current_session)) -> Any:
    """
    Say which frameworks are on, which controls they force, and what enabling each would change.

    Args:
        _session: The authenticated payload.

    Returns:
        Every framework and control as it stands.
    """
    return _state()


@router.post("/plan", response_model=PlanInfo)
def plan_change(
    body: ChangeRequest, _session: dict[str, Any] = Depends(get_current_session)
) -> Any:
    """
    Say what a change would do, writing nothing.

    Args:
        body: The frameworks and controls to change.
        _session: The authenticated payload.

    Returns:
        The controls whose value changes, whether it loosens anything, and what
        about this server the caller must see first.

    Raises:
        ValidationError: For a framework or a control that does not exist, or a
            control set looser than an enabled framework asks.
    """
    return _plan(ComplianceManager(), body).to_dict()


@router.put("", response_model=ChangeResult)
def change_compliance(
    request: Request,
    body: ChangeRequest,
    session: dict[str, Any] = Depends(get_current_session),
) -> Any:
    """
    Turn frameworks on or off and set controls.

    Turning a framework on or a control stricter needs only the permission to change
    security settings. Turning a framework off, or loosening a control, needs sudo
    mode. A change with warnings is made only once ``acknowledge`` is true.

    Args:
        request: The request.
        body: What to change.
        session: The authenticated payload.

    Returns:
        What changed and the picture afterwards.

    Raises:
        HTTPException: 403 ``elevation_required`` when the change loosens something
            and sudo mode is not open.
        ValidationError: When the change is refused: an unknown name, a control looser
            than a framework asks, warnings not acknowledged.
        ConfigError: When the configuration cannot be written.
    """
    manager = ComplianceManager()
    plan = _plan(manager, body)
    if plan.loosens:
        ensure_elevated(request, session)
    applied = manager.apply(plan, acknowledge=body.acknowledge)
    client_ip = get_client_ip(request)
    if plan.enable or plan.disable:
        audit_event(
            "security.framework.change",
            "success",
            client_ip=client_ip,
            session=session,
            target="frameworks",
            detail=(
                f"enabled {', '.join(plan.enable) or 'none'}; "
                f"disabled {', '.join(plan.disable) or 'none'}"
            ),
        )
    for control_id, value in plan.settings.items():
        audit_event(
            "security.control.change",
            "success",
            client_ip=client_ip,
            session=session,
            target=f"control:{control_id}",
            detail=f"set to {value!r}",
        )
    return {
        "enabled_before": list(applied.enabled_before),
        "enabled_after": list(applied.enabled_after),
        "settings": dict(applied.settings),
        "state": _state(),
    }


@router.get("/check", response_model=CheckResponse)
def check_compliance(
    framework: Annotated[list[str] | None, Query()] = None,
    _session: dict[str, Any] = Depends(get_current_session),
) -> Any:
    """
    Judge this server framework by framework, with the clause of each finding.

    Reads the server's state (the last hardening report, the accounts, the backups);
    a pass means the technical control is in force, never that the server complies.

    Args:
        framework: Frameworks to judge; the ones that are on by default.
        _session: The authenticated payload.

    Returns:
        The findings per framework.

    Raises:
        ValidationError: For a framework that does not exist.
    """
    from noust.core.compliance import frameworks
    from noust.core.exceptions import ValidationError

    names = [name.strip().lower() for name in (framework or [])]
    for name in names:
        if not frameworks.exists(name):
            raise ValidationError(
                f"Unknown compliance framework: {name!r}",
                details=f"The frameworks are: {', '.join(frameworks.FRAMEWORK_IDS)}.",
                field="framework",
            )
    return compliance_check.run(names or None).to_dict()
