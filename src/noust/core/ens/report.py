# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust ens report``: the evidence bundle an ENS auditor asks for.

One report, two renderings: JSON for tools and Markdown for people, written
side by side with a ``SHA256SUMS`` so the copy attached to the Declaración de
Aplicabilidad or to the annual report (art. 32) can be checked later. Its
SHA-256 is also recorded in the audit log (``compliance.read``), which is
shipped off the machine.

The report is the check (:mod:`noust.core.ens.checks`) grouped by the RD
311/2022 measure it evidences, plus what an auditor asks to see next to it:
the profile's baseline, the op.mon.2 indicators, the accounts and their roles
(no secret, no personal data beyond the user name), the API tokens (no hash),
the audit chain and its destinations, the backups, the hardening findings and
accepted risks, the inventory and the fleet. Everything it says, it read; it
states nothing about the organisation's own measures (policy, personnel,
facilities), which ``docs/ENS.md`` lists as the operator's.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core.ens.checks import STATUSES, ComplianceCheck, Facts
from noust.core.ens.profile import baseline
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs

#: The measures the report groups by: code to the RD 311/2022 name (Anexo II).
MEASURES: dict[str, str] = {
    "org.1": "Política de seguridad",
    "op.acc.1": "Identificación",
    "op.acc.3": "Segregación de funciones y tareas",
    "op.acc.4": "Proceso de gestión de derechos de acceso",
    "op.acc.6": "Mecanismo de autenticación (usuarios de la organización)",
    "op.exp.1": "Inventario de activos",
    "op.exp.2": "Configuración de seguridad",
    "op.exp.3": "Gestión de la configuración de seguridad",
    "op.exp.4": "Mantenimiento y actualizaciones de seguridad",
    "op.exp.5": "Gestión de cambios",
    "op.exp.6": "Protección frente a código dañino",
    "op.exp.7": "Gestión de incidentes",
    "op.exp.8": "Registro de la actividad",
    "op.exp.10": "Protección de claves criptográficas",
    "op.ext.4": "Interconexión de sistemas",
    "op.mon.1": "Detección de intrusión",
    "op.mon.2": "Sistema de métricas",
    "op.mon.3": "Vigilancia",
    "mp.eq.2": "Bloqueo de puesto de trabajo",
    "mp.com.1": "Perímetro seguro",
    "mp.com.2": "Protección de la confidencialidad",
    "mp.com.3": "Protección de la integridad y de la autenticidad",
    "mp.si.2": "Criptografía",
    "mp.info.2": "Calificación de la información",
    "mp.info.6": "Copias de seguridad",
    "mp.sw.1": "Desarrollo de aplicaciones",
}

_MEASURE = re.compile(r"^([a-z]+\.(?:[a-z]+\.)?\d+)")


def base_measure(code: str) -> str:
    """
    The measure a requirement or reinforcement belongs to.

    Args:
        code: Such as ``op.acc.6.r2`` or ``mp.eq.2``.

    Returns:
        Such as ``op.acc.6``; the code itself when it has no such prefix.
    """
    match = _MEASURE.match(code)
    return match.group(1) if match else code


def _worst(statuses: list[str]) -> str:
    for status in STATUSES:
        if status in statuses:
            return status
    return "n/a"


def by_measure(check: ComplianceCheck) -> list[dict[str, Any]]:
    """
    Group the findings by the measure they evidence.

    Args:
        check: The result of the check.

    Returns:
        One entry per measure, in :data:`MEASURES` order then any other: its
        code, name, worst status and the findings.
    """
    groups: dict[str, list[str]] = {}
    for finding in check.findings:
        for measure in dict.fromkeys(base_measure(code) for code in finding.measures):
            groups.setdefault(measure, []).append(finding.id)
    status_of = {finding.id: finding.status for finding in check.findings}
    order = list(MEASURES) + sorted(set(groups) - set(MEASURES))
    return [
        {
            "measure": measure,
            "name": MEASURES.get(measure, ""),
            "status": _worst([status_of[i] for i in groups[measure]]),
            "findings": groups[measure],
        }
        for measure in order
        if measure in groups
    ]


def _accounts(facts: Facts) -> list[dict[str, Any]]:
    return [
        {
            "username": account.get("username"),
            "role": account.get("role"),
            "status": account.get("status"),
            "mfa": bool(account.get("mfa_enabled")),
            "passkeys": account.get("passkeys", 0),
            "last_login_at": account.get("last_login_at"),
            "created_at": account.get("created_at"),
        }
        for account in facts.accounts or []
    ]


def _tokens(facts: Facts) -> list[dict[str, Any]]:
    return [
        {
            key: token.get(key)
            for key in (
                "id",
                "name",
                "scope",
                "owner_account_id",
                "created_at",
                "expires_at",
                "last_used_at",
                "revoked_at",
            )
        }
        for token in facts.tokens or []
    ]


def build_report(check: ComplianceCheck, facts: Facts) -> dict[str, Any]:
    """
    Assemble the evidence bundle.

    Args:
        check: The result of the check.
        facts: The facts it judged.

    Returns:
        The report, JSON-ready. ``sha256`` is the digest of the report without
        that field, canonically encoded.
    """
    report: dict[str, Any] = {
        "format": "noust-ens-report",
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "category": "MEDIA",
        "regulation": "Real Decreto 311/2022 (Esquema Nacional de Seguridad), Anexo II",
        "host": check.host,
        "noust_version": check.version,
        "profile": check.profile,
        "verdict": check.verdict,
        "counts": check.counts(),
        "measures": by_measure(check),
        "findings": [finding.to_dict() for finding in check.findings],
        "indicators": check.indicators,
        "baseline": [item.to_dict() for item in baseline()],
        "accounts": _accounts(facts),
        "separation_of_duties": {
            "conflicts": facts.sod_conflicts,
            "exceptions": facts.sod_exceptions,
        },
        "tokens": _tokens(facts),
        "audit": {
            "verify": facts.audit_verify,
            "retention_days": facts.audit_retention_days,
            "journald": facts.audit_journald,
            "syslog_receivers": facts.audit_syslog,
            "destinations": (facts.audit_health or {}).get("sinks", []),
            "events": facts.audit_events,
        },
        "backups": {
            "applications": facts.backups,
            "destinations": facts.destinations,
            "schedules": facts.schedules,
            "encryption_required": facts.backup_encryption_required,
        },
        "hardening": facts.hardening,
        "console": {
            "exposure": asdict(facts.exposure) if facts.exposure else None,
            "certificate": facts.certificate,
        },
        "inventory": facts.inventory,
        "fleet": {"role": facts.role, "nodes": facts.nodes, "ceiling": facts.fleet_ceiling},
        "allowed_sources": facts.allowed_sources,
        "lockdown": facts.lockdown,
        "errors": check.errors,
        "operator_responsibilities": (
            "The organisation's own measures (org.*, op.pl.*, mp.if.*, mp.per.*, op.ext.1-2, "
            "op.nub.1) are outside this report: see docs/ENS.md."
        ),
    }
    report["sha256"] = report_digest(report)
    return report


def report_digest(report: dict[str, Any]) -> str:
    """
    Args:
        report: A report.

    Returns:
        The SHA-256 of the report without its ``sha256`` field, canonical JSON.
    """
    body = {key: value for key, value in report.items() if key != "sha256"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_ICON = {"ok": "OK", "warning": "AVISO", "fail": "FALLO", "n/a": "N/A"}


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    """
    Render the report for people.

    Args:
        report: What :func:`build_report` returned.

    Returns:
        Markdown: summary, measures, findings with evidence, indicators,
        baseline, accounts, tokens, backups, inventory.
    """
    lines = [
        f"# Informe ENS (categoría MEDIA) - {report['host']}",
        "",
        f"- Generado: {report['generated_at']}",
        f"- Noust {report['noust_version']}, perfil `{report['profile']}`",
        f"- Resultado: **{_ICON.get(report['verdict'], report['verdict'])}** "
        + ", ".join(f"{_ICON[s]} {report['counts'].get(s, 0)}" for s in STATUSES),
        f"- SHA-256 del informe (JSON canónico): `{report['sha256']}`",
        "",
        "Evidencias técnicas de un componente (Noust); las medidas organizativas, de personal, "
        "de instalaciones y de proveedores son de la organización (ver docs/ENS.md).",
        "",
        "## Medidas (RD 311/2022, Anexo II)",
        "",
        "| Medida | Nombre | Estado | Comprobaciones |",
        "|---|---|:-:|---|",
    ]
    for entry in report["measures"]:
        lines.append(
            f"| {entry['measure']} | {_cell(entry['name'])} | {_ICON.get(entry['status'])} | "
            f"{', '.join(entry['findings'])} |"
        )
    lines += ["", "## Comprobaciones", ""]
    for finding in report["findings"]:
        lines += [
            f"### {finding['id']} - {finding['title']}: {_ICON.get(finding['status'])}",
            "",
            f"Medidas: {', '.join(finding['measures'])}. {finding['summary']}",
            "",
        ]
        if finding["evidence"]:
            lines += ["```", *[str(item) for item in finding["evidence"]], "```", ""]
        if finding["remediation"]:
            lines += [f"Qué hacer: {finding['remediation']}", ""]
    lines += ["## Indicadores (op.mon.2)", "", "| Indicador | Valor |", "|---|---|"]
    for key, value in report["indicators"].items():
        lines.append(
            f"| {key} | {_cell(json.dumps(value) if isinstance(value, dict) else value)} |"
        )
    lines += [
        "",
        "## Línea base del perfil",
        "",
        "| Valor | Estándar | ens-medium | Medidas |",
        "|---|---|---|---|",
    ]
    for item in report["baseline"]:
        lines.append(
            f"| {_cell(item['key'])} | {_cell(item['standard_value'])} | "
            f"{_cell(item['ens_value'])} | {', '.join(item['measures'])} |"
        )
    lines += [
        "",
        "## Cuentas (op.acc.1)",
        "",
        "| Usuario | Rol | Estado | MFA | Último acceso |",
        "|---|---|---|:-:|---|",
    ]
    for account in report["accounts"]:
        last = account.get("last_login_at")
        when = (
            datetime.fromtimestamp(float(last), tz=timezone.utc).strftime("%Y-%m-%d")
            if isinstance(last, (int, float))
            else "nunca"
        )
        lines.append(
            f"| {_cell(account['username'])} | {account['role']} | {account['status']} | "
            f"{'sí' if account['mfa'] else 'no'} | {when} |"
        )
    lines += [
        "",
        "## Tokens de API",
        "",
        "| Nombre | Alcance | Dueño | Caduca | Revocado |",
        "|---|---|---|---|---|",
    ]
    for token in report["tokens"]:
        lines.append(
            f"| {_cell(token['name'])} | {token['scope']} | {token['owner_account_id']} | "
            f"{token['expires_at'] or 'nunca'} | {token['revoked_at'] or ''} |"
        )
    lines += [
        "",
        "## Copias (mp.info.6)",
        "",
        "| Aplicación | Última copia | Última verificación | Copias |",
        "|---|---|---|--:|",
    ]
    for entry in report["backups"].get("applications") or []:
        lines.append(
            f"| {entry['domain']} | {entry.get('newest') or 'ninguna'} | "
            f"{entry.get('verified') or 'nunca'} | {entry.get('count', 0)} |"
        )
    lines += [
        "",
        "## Inventario (op.exp.1)",
        "",
        "| Aplicación | Responsable | Criticidad | Clasificación |",
        "|---|---|---|---|",
    ]
    for entry in report["inventory"] or []:
        lines.append(
            f"| {entry['domain']} | {_cell(entry.get('owner'))} | {entry.get('criticality') or ''} | "
            f"{entry.get('classification') or ''} |"
        )
    if report["errors"]:
        lines += ["", "## Áreas que no se pudieron leer", ""]
        lines += [f"- {area}: `{_cell(error)}`" for area, error in report["errors"].items()]
    return "\n".join(lines) + "\n"


def write_bundle(
    report: dict[str, Any], directory: Path, *, fs: FileSystem | None = None
) -> dict[str, Path]:
    """
    Write the report as JSON and Markdown with a ``SHA256SUMS``, owner-only.

    Args:
        report: What :func:`build_report` returned.
        directory: Where to write; created 0700.
        fs: The filesystem seam; the process's own by default.

    Returns:
        ``json``, ``markdown`` and ``sums`` paths.
    """
    fs = fs or get_fs()
    fs.make_dir(directory, mode=SECRET_DIR_MODE, parents=True)
    stamp = str(report["generated_at"]).replace(":", "").replace("-", "")[:15]
    base = f"ens-report-{report['host']}-{stamp}"
    json_path = directory / f"{base}.json"
    markdown_path = directory / f"{base}.md"
    json_text = json.dumps(report, indent=2, sort_keys=True, default=str)
    markdown_text = render_markdown(report)
    fs.write_text(json_path, json_text, mode=SECRET_MODE)
    fs.write_text(markdown_path, markdown_text, mode=SECRET_MODE)
    sums = directory / f"{base}.SHA256SUMS"
    fs.write_text(
        sums,
        "".join(
            f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}  {path.name}\n"
            for path, text in ((json_path, json_text), (markdown_path, markdown_text))
        ),
        mode=SECRET_MODE,
    )
    return {"json": json_path, "markdown": markdown_path, "sums": sums}
