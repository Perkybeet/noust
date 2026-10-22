# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The compliance frameworks: presets over the controls, and the clause each serves.

A framework is a named set of values for :mod:`noust.core.compliance.controls`.
**This table is the one place** that says which control a framework turns on, to
what value, and which clause of the framework that serves (rule 3);
``docs/COMPLIANCE.md`` is generated from it (``scripts/compliance_docs.py``),
and a test refuses a row without a clause.

**What a preset is, and is not.** It turns on the *technical* controls that help
with a framework. It never claims that a server *is* compliant: every framework
below asks for far more than a deployment tool can do - a risk analysis, a policy,
people who are trained, suppliers who are managed, an audit by somebody else - and
:attr:`Framework.organisational` lists what stays yours, while
:attr:`Framework.not_covered` lists the technical parts Noust does not enforce.
A requirement is only listed when Noust can state the clause it serves; a control
that no clause of a framework asks for is not part of that framework's preset.

**Numbers.** Most frameworks leave a limit to the organisation (how long a session
may idle, how long a token lives). Where the framework fixes it, the row says so
(:attr:`Requirement.prescribed`, CIS 4.3's 15 minutes); everywhere else the value
is Noust's reading of a reasonable limit, and the row is still bounded by the
clause it cites. The values of ENS MEDIA are the ones Noust has applied since 3.1
(spec §12.7); ENS BÁSICA and ALTA take theirs from the same table.

**Several at once.** Frameworks combine by taking, for every control, the
strictest value any enabled framework asks for
(:mod:`noust.core.compliance.resolve`).

Pure data: it imports only :mod:`~noust.core.compliance.controls`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from noust.core.compliance import controls


@dataclass(frozen=True)
class Clause:
    """
    A clause of a framework.

    Attributes:
        id: Its identifier in the framework (``A.8.5``, ``op.acc.6.8``, ``CC6.1``).
        title: What it asks for, in a short English phrase.
    """

    id: str
    title: str


@dataclass(frozen=True)
class Requirement:
    """
    What a framework asks of one control.

    Attributes:
        value: The value the framework requires of the control.
        clauses: The clauses that value serves; never empty.
        prescribed: Whether the framework itself fixes the number (CIS 4.3's 15
            minutes), as opposed to leaving it to the organisation.
        note: A caveat in a sentence, such as "the framework asks for this from a
            higher level" or "the number is Noust's".
    """

    value: Any
    clauses: tuple[Clause, ...]
    prescribed: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The requirement, JSON-ready.
        """
        return {
            "value": self.value,
            "clauses": [{"id": clause.id, "title": clause.title} for clause in self.clauses],
            "prescribed": self.prescribed,
            "note": self.note,
        }


@dataclass(frozen=True)
class Framework:
    """
    A compliance framework or one of its levels.

    Attributes:
        id: Its stable name, in ``security.frameworks``.
        name: How it is called.
        family: ``ens``, ``iso``, ``nis2``, ``cis``, ``soc2`` or ``gdpr``.
        level: The level inside the family, such as ``MEDIA`` or ``IG2``; empty
            for one without levels.
        summary: What it is, in one sentence.
        reference: What it is a reference to: a law, a standard and its edition.
        requirements: Control id to what the framework asks of it.
        organisational: What the framework asks for that no setting can do for
            you; the honest other half of the preset.
        not_covered: Technical requirements Noust does not (yet) enforce.
        includes: Another framework this one contains (CIS IG2 contains IG1).
    """

    id: str
    name: str
    family: str
    level: str
    summary: str
    reference: str
    requirements: dict[str, Requirement]
    organisational: tuple[str, ...]
    not_covered: tuple[str, ...] = ()
    includes: str | None = None

    def all_requirements(self) -> dict[str, Requirement]:
        """
        Returns:
            What it asks of each control, including what the framework it
            includes asks.
        """
        return effective_requirements(self)

    def requirement(self, control_id: str) -> Requirement | None:
        """
        Args:
            control_id: A control's id.

        Returns:
            What the framework asks of it, or None when it asks nothing.
        """
        return self.all_requirements().get(control_id)

    def turns_on(self) -> list[str]:
        """
        Returns:
            The controls it asks for something stricter than the default of,
            in catalog order: what enabling it will change.
        """
        asked = self.all_requirements()
        found = []
        for control in controls.CONTROLS:
            required = asked.get(control.id)
            if required is not None and control.is_stricter_than_standard(required.value):
                found.append(control.id)
        return found

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The framework, JSON-ready, with its whole mapping.
        """
        asked = self.all_requirements()
        return {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "level": self.level,
            "summary": self.summary,
            "reference": self.reference,
            "requirements": {
                control.id: asked[control.id].to_dict()
                for control in controls.CONTROLS
                if control.id in asked
            },
            "turns_on": self.turns_on(),
            "organisational": list(self.organisational),
            "not_covered": list(self.not_covered),
            "includes": self.includes,
        }


def _c(clause_id: str, title: str) -> Clause:
    return Clause(clause_id, title)


def _r(value: Any, *clauses: Clause, prescribed: bool = False, note: str = "") -> Requirement:
    if not clauses:
        raise ValueError("a requirement names the clause it serves")
    return Requirement(value, clauses, prescribed, note)


# ============================================================== ENS (RD 311/2022)
# The measures of Annex II. The categories BÁSICA, MEDIA and ALTA select the BAJO,
# MEDIO and ALTO columns of each measure's "Aplicación de la medida"; a measure is
# only used for a level where that column says it applies. The numbers are the
# organisation's to set (op.acc.6.4 "periodicidad marcada por la política"); these
# are the ones Noust has applied since 3.1.

_OP_ACC_1_2 = _c("op.acc.1.2", "A separate identifier for each profile of the same person")
_OP_ACC_1_3 = _c("op.acc.1.3", "A unique identifier for every entity that accesses the system")
_OP_ACC_1_4 = _c("op.acc.1.4", "Accounts are disabled when no longer needed")
_OP_ACC_2_3 = _c(
    "op.acc.2.3", "Access to operating-system components and configuration is controlled"
)
_OP_ACC_3_1 = _c("op.acc.3.1", "Development and operation do not fall on the same person")
_OP_ACC_3_2 = _c(
    "op.acc.3.2", "Whoever authorises and whoever controls the use are different people"
)
_OP_ACC_4_2 = _c("op.acc.4.2", "Least privilege")
_OP_ACC_4_4 = _c("op.acc.4.4", "Access rights are reviewed periodically")
_OP_ACC_4_5 = _c("op.acc.4.5", "A specific remote-access policy with express authorisation")
_OP_ACC_6_4 = _c("op.acc.6.4", "Credentials are changed periodically")
_OP_ACC_6_8 = _c("op.acc.6.8", "Authentication attempts are limited, then the account locks")
_OP_ACC_6_R1 = _c("op.acc.6.r1.2", "Minimum password complexity and robustness")
_OP_ACC_6_R6 = _c("op.acc.6.r6.1", "Points where authentication is renewed, not just the session")
_OP_ACC_6_R8 = _c("op.acc.6.r8.1", "Two factors from or through uncontrolled zones")
_OP_EXP_5_1 = _c("op.exp.5.1", "Every change request is recorded with a reference")
_OP_EXP_5_2 = _c("op.exp.5.2", "A change request carries enough information to authorise it")
_OP_EXP_5_4 = _c("op.exp.5.4", "Relevant changes are approved beforehand")
_OP_EXP_8_1 = _c("op.exp.8.1", "An audit record of the activity is generated")
_OP_EXP_8_R1 = _c("op.exp.8.r1.1", "Activity records are reviewed periodically")
_OP_EXP_8_R3 = _c("op.exp.8.r3.1", "The retention time of the records is defined")
_OP_EXP_8_R4 = _c("op.exp.8.r4.1", "Records are accessible and deletable only by authorised staff")
_OP_MON_3_1 = _c("op.mon.3.1", "An automatic collection of security events")
_MP_EQ_2_1 = _c("mp.eq.2.1", "Locks after a prudent time of inactivity")
_MP_EQ_2_R1 = _c("mp.eq.2.r1.1", "Open sessions are cancelled after a longer time")
_MP_COM_1_1 = _c("mp.com.1.1", "A perimeter protection separates the internal network from outside")
_MP_COM_2_1 = _c("mp.com.2.1", "Encrypted channels outside the security domain")
_MP_COM_2_R1 = _c("mp.com.2.r1.1", "Algorithms and parameters authorised by the CCN")
_MP_COM_3_R2 = _c("mp.com.3.r2.1", "Authenticity of the other end, with authorised parameters")
_MP_SI_2_1 = _c("mp.si.2.1", "Cryptographic mechanisms for confidentiality and integrity")
_MP_SI_2_R2 = _c("mp.si.2.r2.1", "Backups are encrypted")
_MP_INFO_6_1 = _c("mp.info.6.1", "Backups allow recovering lost data")
_MP_INFO_6_R1 = _c("mp.info.6.r1.1", "Backup and restore procedures are tested regularly")
_MP_INFO_6_R2 = _c("mp.info.6.r2.1", "One backup is kept apart, in a different place")

_ENS_ORGANISATIONAL = (
    "The security policy and its approval (org.1, org.2), the security roles and the committee "
    "(org.3, org.4).",
    "The risk analysis and the categorisation of the system (op.pl.1, Annex I), and the "
    "declaration of applicability.",
    "People: duties, awareness and training (mp.per).",
    "Physical security of the premises and the equipment (mp.if), and the workstation (mp.eq).",
    "Service continuity: impact analysis, plan, tests, alternative means (op.cont).",
    "Suppliers and external services (op.ext) and certified components (op.pl.5).",
    "Incident management and notification to the CCN-CERT (op.exp.7, op.exp.9), and the "
    "periodic security audit (art. 31).",
)

_ENS_B: dict[str, Requirement] = {
    "single_role_per_account": _r(True, _OP_ACC_1_2),
    "restricted_tokens": _r(True, _OP_ACC_1_3, _OP_ACC_4_2),
    "token_max_days": _r(
        90, _OP_ACC_6_4, note="The period is the organisation's; 90 days is Noust's."
    ),
    "master_token": _r("recovery", _OP_ACC_6_R8),
    "lockout_threshold": _r(5, _OP_ACC_6_8),
    "lockout_minutes": _r(15, _OP_ACC_6_8),
    "password_min_length": _r(
        12, _OP_ACC_6_R1, note="The same as the default; the floor cannot be lowered."
    ),
    "access_review_days": _r(90, _OP_ACC_4_4),
    "stale_account_days": _r(90, _OP_ACC_1_4),
    "external_audit_sink": _r(True, _OP_MON_3_1),
    "log_shipping_tls": _r(True, _MP_COM_2_1),
    "backup_max_age_hours": _r(26, _MP_INFO_6_1),
    "explicit_allowlist": _r(True, _OP_ACC_4_5, _MP_COM_1_1),
    "console_exposure": _r("refused", _MP_COM_1_1, _OP_ACC_4_5),
    "database_remote_listen": _r("refused", _MP_COM_1_1),
    "mounts_read_only": _r(True, _OP_ACC_2_3),
}

_ENS_M: dict[str, Requirement] = {
    **_ENS_B,
    "roles_split": _r(True, _OP_ACC_3_1, _OP_ACC_3_2),
    "separation_of_duties": _r(True, _OP_ACC_3_1, _OP_ACC_3_2),
    "sso_no_admin": _r(True, _OP_ACC_3_2),
    "approvals": _r(True, _OP_ACC_3_2, _OP_EXP_5_4),
    "second_factor": _r(
        True,
        _OP_ACC_6_R8,
        note="Every account has held one since 3.1 under this category; the control keeps it.",
    ),
    "sudo_password": _r(
        True,
        _OP_ACC_6_R6,
        note="The measure asks for renewal points from category ALTA; MEDIA keeps what 3.1 decided.",
    ),
    "sudo_idle_minutes": _r(10, _OP_ACC_6_R6, note="The number is Noust's."),
    "sudo_max_minutes": _r(30, _OP_ACC_6_R6, note="The number is Noust's."),
    "session_idle_minutes": _r(
        15, _MP_EQ_2_1, note="'Prudent time' is the organisation's; 15 minutes is Noust's."
    ),
    "session_absolute_hours": _r(8, _MP_EQ_2_1),
    "password_min_length": _r(14, _OP_ACC_6_R1, note="The number is Noust's."),
    "change_reasons": _r(True, _OP_EXP_5_1),
    "exception_reasons": _r(True, _OP_EXP_5_2),
    "audit_retention_days": _r(
        365, _OP_EXP_8_R3, note="The period is the organisation's; 365 days is Noust's."
    ),
    "audit_fail_closed": _r(True, _OP_EXP_8_1),
    "external_audit_sink": _r(True, _OP_MON_3_1, _OP_EXP_8_R4),
    "audit_review_days": _r(7, _OP_EXP_8_R1),
    "backup_encryption": _r("required", _MP_SI_2_1),
    "backup_verify_days": _r(
        7, _MP_INFO_6_R1, note="The frequency is the organisation's; 7 days is Noust's."
    ),
    "tls_certificate": _r("operator", _MP_COM_2_R1, _MP_COM_3_R2),
    "certificate_min_days": _r(30, _MP_COM_2_R1),
    "server_terminal": _r("refused", _OP_ACC_4_2),
    "terminal_idle_minutes": _r(10, _MP_EQ_2_1),
    "terminal_max_minutes": _r(60, _MP_EQ_2_1),
    "db_write_root_equivalent": _r(True, _OP_ACC_4_2),
}

_ENS_A: dict[str, Requirement] = {
    **_ENS_M,
    "session_absolute_hours": _r(8, _MP_EQ_2_R1),
    "backup_encryption": _r("required", _MP_SI_2_R2),
    "backup_offsite": _r(True, _MP_INFO_6_R2),
}

_ENS_NOT_COVERED = (
    "Suspending credentials after a period of non-use (op.acc.6.r7.1, from ALTA).",
    "Automatic correlation of events and automatic response (op.exp.8.r5, op.mon.3.r1, from ALTA).",
    "Qualified certificates as authentication (op.acc.6.r3, r4), where chosen instead of a second factor.",
)

# ============================================================ ISO/IEC 27001:2022
_A_5_3 = _c("A.5.3", "Segregation of duties")
_A_5_17 = _c("A.5.17", "Authentication information")
_A_5_18 = _c("A.5.18", "Access rights")
_A_8_2 = _c("A.8.2", "Privileged access rights")
_A_8_3 = _c("A.8.3", "Information access restriction")
_A_8_5 = _c("A.8.5", "Secure authentication")
_A_8_13 = _c("A.8.13", "Information backup")
_A_8_15 = _c("A.8.15", "Logging")
_A_8_16 = _c("A.8.16", "Monitoring activities")
_A_8_18 = _c("A.8.18", "Use of privileged utility programs")
_A_8_20 = _c("A.8.20", "Networks security")
_A_8_21 = _c("A.8.21", "Security of network services")
_A_8_22 = _c("A.8.22", "Segregation of networks")
_A_8_24 = _c("A.8.24", "Use of cryptography")
_A_8_32 = _c("A.8.32", "Change management")
_ISO_6_1_3 = _c("6.1.3", "Information security risk treatment (acceptance of residual risk)")

_ISO_NOTE = "ISO/IEC 27001 prescribes no number; this one is Noust's reading."

_ISO: dict[str, Requirement] = {
    "roles_split": _r(True, _A_5_3),
    "separation_of_duties": _r(True, _A_5_3),
    "single_role_per_account": _r(True, _A_8_2),
    "sso_no_admin": _r(True, _A_8_2),
    "restricted_tokens": _r(True, _A_5_17, _A_8_2),
    "token_max_days": _r(90, _A_5_17, note=_ISO_NOTE),
    "master_token": _r("recovery", _A_8_2),
    "second_factor": _r(
        True,
        _A_8_5,
        note="A.8.5 leaves the method to the organisation; a second factor is Noust's reading.",
    ),
    "sudo_password": _r(True, _A_8_2, _A_8_5),
    "sudo_idle_minutes": _r(10, _A_8_2, note=_ISO_NOTE),
    "sudo_max_minutes": _r(30, _A_8_2, note=_ISO_NOTE),
    "session_idle_minutes": _r(15, _A_8_5, note=_ISO_NOTE),
    "session_absolute_hours": _r(8, _A_8_5, note=_ISO_NOTE),
    "lockout_threshold": _r(5, _A_8_5, note=_ISO_NOTE),
    "lockout_minutes": _r(15, _A_8_5, note=_ISO_NOTE),
    "password_min_length": _r(14, _A_5_17, note=_ISO_NOTE),
    "access_review_days": _r(90, _A_5_18, note=_ISO_NOTE),
    "stale_account_days": _r(90, _A_5_18, note=_ISO_NOTE),
    "change_reasons": _r(True, _A_8_32),
    "exception_reasons": _r(True, _ISO_6_1_3),
    "audit_retention_days": _r(365, _A_8_15, note=_ISO_NOTE),
    "audit_fail_closed": _r(True, _A_8_15),
    "external_audit_sink": _r(True, _A_8_15),
    "audit_review_days": _r(7, _A_8_15, _A_8_16, note=_ISO_NOTE),
    "log_shipping_tls": _r(True, _A_8_24),
    "backup_encryption": _r("required", _A_8_13, _A_8_24),
    "backup_verify_days": _r(7, _A_8_13, note=_ISO_NOTE),
    "backup_max_age_hours": _r(26, _A_8_13, note=_ISO_NOTE),
    "backup_offsite": _r(True, _A_8_13),
    "certificate_min_days": _r(30, _A_8_24, note=_ISO_NOTE),
    "explicit_allowlist": _r(True, _A_8_20),
    "console_exposure": _r("refused", _A_8_20, _A_8_21),
    "database_remote_listen": _r("refused", _A_8_20, _A_8_22),
    "server_terminal": _r("refused", _A_8_18),
    "terminal_idle_minutes": _r(10, _A_8_5, _A_8_18, note=_ISO_NOTE),
    "terminal_max_minutes": _r(60, _A_8_5, _A_8_18, note=_ISO_NOTE),
    "mounts_read_only": _r(True, _A_8_3),
    "db_write_root_equivalent": _r(True, _A_8_2),
}

# ================================================================== NIS2 (art. 21)
_N_B = _c("Art. 21(2)(b)", "Incident handling")
_N_C = _c(
    "Art. 21(2)(c)",
    "Business continuity, such as backup management and disaster recovery, and crisis management",
)
_N_F = _c(
    "Art. 21(2)(f)",
    "Policies and procedures to assess the effectiveness of cybersecurity risk-management measures",
)
_N_G = _c("Art. 21(2)(g)", "Basic cyber hygiene practices and cybersecurity training")
_N_H = _c(
    "Art. 21(2)(h)",
    "Policies and procedures regarding the use of cryptography and, where appropriate, encryption",
)
_N_I = _c("Art. 21(2)(i)", "Human resources security, access control policies and asset management")
_N_J = _c(
    "Art. 21(2)(j)",
    "The use of multi-factor authentication or continuous authentication solutions",
)
_NIS2_NOTE = "Article 21 names areas, not settings; this number is Noust's reading."

_NIS2: dict[str, Requirement] = {
    "audit_retention_days": _r(365, _N_B, note=_NIS2_NOTE),
    "audit_fail_closed": _r(True, _N_B, note=_NIS2_NOTE),
    "external_audit_sink": _r(True, _N_B, note=_NIS2_NOTE),
    "audit_review_days": _r(7, _N_F, note=_NIS2_NOTE),
    "backup_verify_days": _r(7, _N_C, note=_NIS2_NOTE),
    "backup_max_age_hours": _r(26, _N_C, note=_NIS2_NOTE),
    "backup_offsite": _r(True, _N_C),
    "backup_encryption": _r("required", _N_H),
    "log_shipping_tls": _r(True, _N_H),
    "certificate_min_days": _r(30, _N_H, note=_NIS2_NOTE),
    "password_min_length": _r(14, _N_G, note=_NIS2_NOTE),
    "lockout_threshold": _r(5, _N_G, note=_NIS2_NOTE),
    "lockout_minutes": _r(15, _N_G, note=_NIS2_NOTE),
    "access_review_days": _r(90, _N_I, note=_NIS2_NOTE),
    "stale_account_days": _r(90, _N_I, note=_NIS2_NOTE),
    "token_max_days": _r(90, _N_I, note=_NIS2_NOTE),
    "restricted_tokens": _r(True, _N_I),
    "second_factor": _r(True, _N_J),
    "sudo_password": _r(True, _N_J),
    "sudo_idle_minutes": _r(10, _N_J, note=_NIS2_NOTE),
    "sudo_max_minutes": _r(30, _N_J, note=_NIS2_NOTE),
}

# ================================================================ CIS Controls v8
_CIS_NOTE = "CIS Controls v8 leaves this number to the enterprise; it is Noust's reading."
_CIS_3_10 = _c("3.10", "Encrypt sensitive data in transit")
_CIS_3_11 = _c("3.11", "Encrypt sensitive data at rest")
_CIS_4_3 = _c("4.3", "Configure automatic session locking on enterprise assets")
_CIS_5_1 = _c("5.1", "Establish and maintain an inventory of accounts")
_CIS_5_3 = _c("5.3", "Disable dormant accounts")
_CIS_5_4 = _c("5.4", "Restrict administrator privileges to dedicated administrator accounts")
_CIS_6_5 = _c("6.5", "Require MFA for administrative access")
_CIS_8_9 = _c("8.9", "Centralize audit logs")
_CIS_8_10 = _c("8.10", "Retain audit logs")
_CIS_8_11 = _c("8.11", "Conduct audit log reviews")
_CIS_11_3 = _c("11.3", "Protect recovery data")
_CIS_11_4 = _c("11.4", "Establish and maintain an isolated instance of recovery data")

_CIS_IG1: dict[str, Requirement] = {
    "session_idle_minutes": _r(15, _CIS_4_3, prescribed=True),
    "access_review_days": _r(
        90, _CIS_5_1, prescribed=True, note="Validate the accounts at least quarterly."
    ),
    "stale_account_days": _r(45, _CIS_5_3, prescribed=True),
    "single_role_per_account": _r(True, _CIS_5_4),
    "second_factor": _r(True, _CIS_6_5, prescribed=True),
    "backup_encryption": _r(
        "required",
        _CIS_11_3,
        note="The safeguard says encryption or separation, based on requirements.",
    ),
    "backup_offsite": _r(True, _CIS_11_4),
}

_CIS_IG2: dict[str, Requirement] = {
    **_CIS_IG1,
    "backup_encryption": _r("required", _CIS_11_3, _CIS_3_11),
    "log_shipping_tls": _r(True, _CIS_3_10),
    "external_audit_sink": _r(True, _CIS_8_9),
    "audit_retention_days": _r(90, _CIS_8_10, prescribed=True, note="At least 90 days."),
    "audit_review_days": _r(7, _CIS_8_11, prescribed=True, note="Weekly, or more often."),
}

# ====================================================================== SOC 2
_CC6_1 = _c("CC6.1", "Logical access security software, infrastructure and architectures")
_CC6_2 = _c(
    "CC6.2",
    "Registration and authorisation of users, and removal when access is no longer authorised",
)
_CC6_3 = _c("CC6.3", "Role-based access, least privilege and segregation of duties")
_CC6_6 = _c(
    "CC6.6", "Logical access security measures against threats from outside the system boundaries"
)
_CC6_7 = _c("CC6.7", "Restriction of transmission, movement and removal of information")
_CC7_2 = _c("CC7.2", "Monitoring of system components for anomalies")
_CC8_1 = _c("CC8.1", "Change management")
_SOC2_NOTE = "The criteria name no number; this one is Noust's reading."

_SOC2: dict[str, Requirement] = {
    "roles_split": _r(True, _CC6_3),
    "separation_of_duties": _r(True, _CC6_3),
    "approvals": _r(True, _CC8_1),
    "change_reasons": _r(True, _CC8_1),
    "restricted_tokens": _r(True, _CC6_1, _CC6_3),
    "token_max_days": _r(90, _CC6_1, note=_SOC2_NOTE),
    "master_token": _r("recovery", _CC6_1),
    "second_factor": _r(
        True,
        _CC6_1,
        note="The criteria name no method; a second factor is Noust's reading.",
    ),
    "sudo_password": _r(True, _CC6_1),
    "lockout_threshold": _r(5, _CC6_1, note=_SOC2_NOTE),
    "lockout_minutes": _r(15, _CC6_1, note=_SOC2_NOTE),
    "password_min_length": _r(14, _CC6_1, note=_SOC2_NOTE),
    "session_idle_minutes": _r(15, _CC6_1, note=_SOC2_NOTE),
    "access_review_days": _r(90, _CC6_2, note=_SOC2_NOTE),
    "stale_account_days": _r(90, _CC6_2, note=_SOC2_NOTE),
    "backup_encryption": _r("required", _CC6_1),
    "log_shipping_tls": _r(True, _CC6_7),
    "explicit_allowlist": _r(True, _CC6_6),
    "console_exposure": _r("refused", _CC6_6),
    "database_remote_listen": _r("refused", _CC6_6),
    "audit_retention_days": _r(365, _CC7_2, note=_SOC2_NOTE),
    "audit_fail_closed": _r(True, _CC7_2),
    "external_audit_sink": _r(True, _CC7_2),
    "audit_review_days": _r(7, _CC7_2, note=_SOC2_NOTE),
}

# ================================================================== GDPR art. 32
_G_A = _c("Art. 32(1)(a)", "Pseudonymisation and encryption of personal data")
_G_B = _c(
    "Art. 32(1)(b)",
    "Ongoing confidentiality, integrity, availability and resilience of processing systems",
)
_G_C = _c(
    "Art. 32(1)(c)",
    "The ability to restore the availability of and access to personal data in a timely manner",
)
_G_D = _c(
    "Art. 32(1)(d)",
    "A process for regularly testing, assessing and evaluating the effectiveness of the measures",
)
_GDPR_NOTE = (
    "Article 32 asks for measures appropriate to the risk, not for a number; this one is Noust's."
)

_GDPR: dict[str, Requirement] = {
    "backup_encryption": _r("required", _G_A),
    "log_shipping_tls": _r(True, _G_A),
    "password_min_length": _r(14, _G_B, note=_GDPR_NOTE),
    "lockout_threshold": _r(5, _G_B, note=_GDPR_NOTE),
    "lockout_minutes": _r(15, _G_B, note=_GDPR_NOTE),
    "session_idle_minutes": _r(15, _G_B, note=_GDPR_NOTE),
    "session_absolute_hours": _r(8, _G_B, note=_GDPR_NOTE),
    "restricted_tokens": _r(True, _G_B),
    "token_max_days": _r(90, _G_B, note=_GDPR_NOTE),
    "access_review_days": _r(90, _G_B, _G_D, note=_GDPR_NOTE),
    "stale_account_days": _r(90, _G_B, note=_GDPR_NOTE),
    "console_exposure": _r("refused", _G_B),
    "database_remote_listen": _r("refused", _G_B),
    "backup_verify_days": _r(7, _G_C, _G_D, note=_GDPR_NOTE),
    "backup_max_age_hours": _r(26, _G_C, note=_GDPR_NOTE),
    "backup_offsite": _r(True, _G_C),
    "audit_review_days": _r(7, _G_D, note=_GDPR_NOTE),
}

#: Every framework Noust ships, in the order the console lists them.
FRAMEWORKS: tuple[Framework, ...] = (
    Framework(
        "ens-basica",
        "ENS BÁSICA",
        "ens",
        "BÁSICA",
        "Spain's National Security Scheme, category BÁSICA: the measures of Annex II that "
        "apply to the lowest category.",
        "Real Decreto 311/2022, Annex II",
        _ENS_B,
        _ENS_ORGANISATIONAL,
    ),
    Framework(
        "ens-media",
        "ENS MEDIA",
        "ens",
        "MEDIA",
        "Spain's National Security Scheme, category MEDIA: what Noust has called the "
        "ens-medium profile since 3.1.",
        "Real Decreto 311/2022, Annex II",
        _ENS_M,
        _ENS_ORGANISATIONAL,
    ),
    Framework(
        "ens-alta",
        "ENS ALTA",
        "ens",
        "ALTA",
        "Spain's National Security Scheme, category ALTA: MEDIA plus a backup kept apart.",
        "Real Decreto 311/2022, Annex II",
        _ENS_A,
        _ENS_ORGANISATIONAL,
        _ENS_NOT_COVERED,
    ),
    Framework(
        "iso-27001",
        "ISO/IEC 27001:2022",
        "iso",
        "",
        "The international information security management standard: the technical "
        "controls of Annex A that a deployment tool can enforce.",
        "ISO/IEC 27001:2022, Annex A",
        _ISO,
        (
            "The management system itself: context, scope, leadership, policy, risk assessment, "
            "the statement of applicability, internal audit and management review (clauses 4 to 10).",
            "People and physical controls (Annex A, themes 6 and 7).",
            "Supplier relationships (A.5.19 to A.5.23) and incident management (A.5.24 to A.5.28).",
            "Vulnerability and malware management on the hosts (A.8.7, A.8.8).",
            "Certification, which only an accredited body grants.",
        ),
    ),
    Framework(
        "nis2",
        "NIS2",
        "nis2",
        "",
        "The EU directive on the security of network and information systems: the "
        "risk-management measures of article 21(2) a server can help with.",
        "Directive (EU) 2022/2555, article 21",
        _NIS2,
        (
            "Governance: management approves and answers for the measures (art. 20).",
            "Risk analysis and security policies (art. 21(2)(a)), supply-chain security (d), "
            "secure development and vulnerability handling (e), training (g).",
            "Reporting a significant incident: early warning in 24 hours, notification in 72, "
            "final report in a month (art. 23).",
            "Registration with the authority, and the national transposition that applies to you.",
        ),
    ),
    Framework(
        "cis-ig1",
        "CIS Controls v8, IG1",
        "cis",
        "IG1",
        "The Center for Internet Security's essential cyber hygiene (Implementation Group 1): "
        "the safeguards of IG1 a deployment tool can enforce.",
        "CIS Critical Security Controls v8",
        _CIS_IG1,
        (
            "Inventories of enterprise assets and software (controls 1 and 2).",
            "Malware defences, email and browser protections, vulnerability management "
            "(controls 7, 9, 10).",
            "Security awareness training (control 14), service-provider management (control 15).",
            "An incident response process (control 17).",
        ),
    ),
    Framework(
        "cis-ig2",
        "CIS Controls v8, IG2",
        "cis",
        "IG2",
        "CIS Controls v8, Implementation Group 2: IG1 and the safeguards of IG2 a deployment "
        "tool can enforce.",
        "CIS Critical Security Controls v8",
        _CIS_IG2,
        (
            "Everything IG1 leaves organisational, and penetration testing (control 18).",
            "Centralised account and access management beyond the server (5.6, 6.7).",
            "Network monitoring and defence (control 13).",
        ),
        includes="cis-ig1",
    ),
    Framework(
        "soc2-security",
        "SOC 2, Security",
        "soc2",
        "",
        "The AICPA Trust Services Criteria for Security (the common criteria): the logical "
        "access, monitoring and change-management criteria a server can help with.",
        "AICPA Trust Services Criteria (2017, revised points of focus 2022), CC6, CC7, CC8",
        _SOC2,
        (
            "The audit: a SOC 2 report is an independent auditor's opinion, not a setting.",
            "The control environment, risk assessment and communication (CC1 to CC5).",
            "Vendor management (CC9.2), human resources, physical access (CC6.4) and disposal (CC6.5).",
            "Availability, confidentiality, processing integrity and privacy are other criteria; "
            "backups serve Availability (A1.2), so they are not part of this preset.",
        ),
    ),
    Framework(
        "gdpr-32",
        "GDPR / RGPD, article 32",
        "gdpr",
        "",
        "Security of processing under the General Data Protection Regulation: the technical "
        "measures of article 32(1) a server can help with.",
        "Regulation (EU) 2016/679, article 32",
        _GDPR,
        (
            "The lawful basis, purposes, records of processing (art. 30) and impact assessments (art. 35).",
            "Data-subject rights (art. 15 to 22), processor contracts (art. 28), the data "
            "protection officer (art. 37).",
            "Notifying a breach to the authority and to the people affected (art. 33, 34).",
            "Pseudonymisation of the data your applications hold (art. 32(1)(a)): Noust does not "
            "see inside them.",
        ),
    ),
)

_BY_ID: dict[str, Framework] = {framework.id: framework for framework in FRAMEWORKS}

#: The ids, for a command's choices and a request's validation.
FRAMEWORK_IDS: tuple[str, ...] = tuple(_BY_ID)

#: ``security.profile`` (the setting before frameworks) names ENS MEDIA.
LEGACY_PROFILE_FRAMEWORK = "ens-media"


def get(framework_id: str) -> Framework:
    """
    Args:
        framework_id: A framework's id.

    Returns:
        The framework.

    Raises:
        KeyError: When there is none; callers validate against :data:`FRAMEWORK_IDS`.
    """
    return _BY_ID[framework_id]


def exists(framework_id: str) -> bool:
    """
    Args:
        framework_id: A name.

    Returns:
        Whether it is a framework.
    """
    return framework_id in _BY_ID


def effective_requirements(framework: Framework) -> dict[str, Requirement]:
    """
    Args:
        framework: A framework.

    Returns:
        What it asks of each control, including what the framework it includes
        asks (CIS IG2 contains IG1); its own rows win.
    """
    merged: dict[str, Requirement] = {}
    if framework.includes is not None:
        merged.update(effective_requirements(get(framework.includes)))
    merged.update(framework.requirements)
    return merged


def check_table() -> list[str]:
    """
    Judge the table itself.

    Returns:
        The problems found: a row for a control that does not exist, a value the
        control does not accept, a row without a clause. Empty when it is sound.
    """
    problems: list[str] = []
    for framework in FRAMEWORKS:
        for control_id, required in framework.requirements.items():
            if not controls.exists(control_id):
                problems.append(f"{framework.id}: no control {control_id}")
                continue
            control = controls.get(control_id)
            if not required.clauses:
                problems.append(f"{framework.id}/{control_id}: no clause")
            try:
                control.coerce(required.value)
            except ValueError as exc:
                problems.append(f"{framework.id}/{control_id}: {exc}")
        if framework.includes is not None and not exists(framework.includes):
            problems.append(f"{framework.id}: includes {framework.includes}, which does not exist")
    return problems


__all__ = [
    "FRAMEWORKS",
    "FRAMEWORK_IDS",
    "LEGACY_PROFILE_FRAMEWORK",
    "Clause",
    "Framework",
    "Requirement",
    "check_table",
    "effective_requirements",
    "exists",
    "get",
]
