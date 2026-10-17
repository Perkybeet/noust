# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a preview is called: the one place its domain is worked out.

A preview answers at ``pr-<n>-<app-name>.<base domain>`` unless its application
chose a template (checklist B-34): the domain written with placeholders, as Coolify
spells them -

- ``{{pr_id}}``: the pull request's number (``x1`` for a preview made by hand from a
  branch or a tag, counted from 1),
- ``{{random}}``: eight letters and digits made when the preview is made, for a name
  nobody can guess from the pull request's number,
- ``{{domain}}``: the application's own domain (``shop.example.com``),
- ``{{name}}``: the application's name, its domain with dashes (``shop-example-com``),
- ``{{base}}``: the base domain previews answer under.

``{{pr_id}}-{{name}}.{{base}}`` is a template; so is ``{{pr_id}}.{{domain}}``, which
needs a wildcard record under the application's own domain. A template has to hold
``{{pr_id}}`` or ``{{random}}`` (two previews must never share a name) and has to give a
domain. The name is bounded as a whole, because it becomes the preview's
application name and unit name.

This module is pure: the domain a preview gets, from what is known when it is
made. :mod:`noust.managers.previews` uses it to name previews and
:mod:`noust.core.variables.resolver` to point a preview at the preview of a sibling
application of the same pull request (``{{peer:api.example.com/URL}}``).
"""

from __future__ import annotations

import hashlib
import re
import secrets

from noust.core.exceptions import NoustError, ValidationError
from noust.core.preview_store import MANUAL_BASE
from noust.core.utils import domain_to_app_name
from noust.validators.domain import validate_domain
from noust.validators.names import MAX_APP_NAME_LENGTH

#: The placeholders a template may hold.
PLACEHOLDERS: tuple[str, ...] = ("pr_id", "random", "domain", "name", "base")

#: The largest pull request number a base domain is checked against when
#: previews are turned on, so a long base domain is refused then rather than
#: at the first busy repository's pull request.
LARGEST_NUMBER_CHECKED = 999999

#: Characters of the application's name a preview domain keeps at least.
#: Below this the name is a hash with a letter in front, which still tells
#: previews of different applications apart but no longer reads as a name.
MIN_NAME_ROOM = 8

#: Length of the hash that keeps two truncated names apart.
HASH_LENGTH = 6

#: Characters of ``{{random}}``.
RANDOM_LENGTH = 8

#: Longest template an application may have.
MAX_TEMPLATE = 200

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")


def label_prefix(number: int) -> str:
    """
    Say how a preview's first label begins.

    Args:
        number: The preview's number.

    Returns:
        ``pr-<n>-`` for a pull request; ``x-<n>-`` for one made by hand from a
        branch or a tag, counted from 1 (its number is above every pull request's).
    """
    if number >= MANUAL_BASE:
        return f"x-{number - MANUAL_BASE}-"
    return f"pr-{number}-"


def pr_id(number: int) -> str:
    """
    Args:
        number: The preview's number.

    Returns:
        What ``{{pr_id}}`` stands for: the pull request's number, or ``x<n>`` for a
        preview made by hand.
    """
    return f"x{number - MANUAL_BASE}" if number >= MANUAL_BASE else str(number)


def name_room(base_domain: str, number: int) -> int:
    """
    Say how many characters of the application's name fit in a preview domain.

    The whole domain is bounded, not just its first label: it becomes the
    preview's application name and unit name, dots turned into dashes, and
    those are at most :data:`MAX_APP_NAME_LENGTH` characters.

    Args:
        base_domain: The base domain previews answer under.
        number: The preview's number.

    Returns:
        The room left for the name.
    """
    return MAX_APP_NAME_LENGTH - len(base_domain) - 1 - len(label_prefix(number))


def _fit_name(name: str, room: int) -> str:
    """
    Args:
        name: The application's name.
        room: The characters it may take.

    Returns:
        The name, cut and given a short hash of the whole when it does not fit, so
        two long names that share a beginning still give different domains.
    """
    if len(name) <= room:
        return name
    digest = hashlib.sha256(name.encode()).hexdigest()[:HASH_LENGTH]
    return f"{name[: room - HASH_LENGTH - 1].rstrip('-')}-{digest}"


def new_random() -> str:
    """
    Returns:
        What ``{{random}}`` stands for in a preview made now.
    """
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(RANDOM_LENGTH))


def preview_domain_for(
    parent_domain: str,
    number: int,
    base_domain: str,
    *,
    template: str | None = None,
    random: str | None = None,
) -> str:
    """
    Name the preview of a pull request.

    Without a template: ``pr-<n>-<app-name>.<base domain>`` (``x-<n>-...`` for one made
    by hand from a branch or a tag), a single label under the base domain, so one
    wildcard record (and one wildcard certificate, if the operator has one) covers every
    preview. A name that does not fit is cut and given a short hash of the whole name, so
    two long names that share a beginning still give different domains.

    Args:
        parent_domain: The application previewed.
        number: The pull request number, or the number of a preview made by hand.
        base_domain: Where previews answer, such as ``previews.example.com``.
        template: The application's own template; see the module's docstring.
        random: What ``{{random}}`` stands for; made when None.

    Returns:
        The preview's domain.

    Raises:
        ValidationError: The base domain leaves no room for a name, or the template
            gives something that is not a domain.
    """
    if template:
        return render_template(
            template,
            parent_domain=parent_domain,
            number=number,
            base_domain=base_domain,
            random=random,
        )
    name = domain_to_app_name(parent_domain)
    room = name_room(base_domain, number)
    if room < MIN_NAME_ROOM:
        raise ValidationError(
            f"No room for the preview {label_prefix(number)[:-1]} under {base_domain}",
            details=f"Preview domains are at most {MAX_APP_NAME_LENGTH} characters. "
            "Use a shorter base domain.",
        )
    return validate_domain(f"{label_prefix(number)}{_fit_name(name, room)}.{base_domain}")


def check_template(template: str) -> str:
    """
    Check a template as it is typed, before anything is made from it.

    Args:
        template: The template.

    Returns:
        It, trimmed.

    Raises:
        ValidationError: Too long, a placeholder that does not exist, none that tells two
            previews apart, or something that cannot become a domain.
    """
    text = (template or "").strip()
    if not text:
        raise ValidationError(
            "The domain template is empty",
            details="Write it like pr-{{pr_id}}-{{name}}.{{base}}, or leave the default.",
            field="domain_template",
        )
    if len(text) > MAX_TEMPLATE:
        raise ValidationError(
            f"A domain template is at most {MAX_TEMPLATE} characters", field="domain_template"
        )
    used = {match.group(1) for match in _PLACEHOLDER.finditer(text)}
    unknown = sorted(used - set(PLACEHOLDERS))
    if unknown:
        raise ValidationError(
            f"Not a placeholder of a domain template: {{{{{unknown[0]}}}}}",
            details="Use " + ", ".join(f"{{{{{name}}}}}" for name in PLACEHOLDERS) + ".",
            field="domain_template",
        )
    if not used & {"pr_id", "random"}:
        raise ValidationError(
            "A domain template must hold {{pr_id}} or {{random}}",
            details="Without one, every preview would get the same name.",
            field="domain_template",
        )
    # The worst case: the longest number, the longest name that fits, a long random.
    render_template(
        text,
        parent_domain="a-long-application-name.example.com",
        number=LARGEST_NUMBER_CHECKED,
        base_domain="previews.example.com",
        random="x" * RANDOM_LENGTH,
    )
    return text


def render_template(
    template: str,
    *,
    parent_domain: str,
    number: int,
    base_domain: str,
    random: str | None = None,
) -> str:
    """
    Fill in a domain template.

    Args:
        template: The template.
        parent_domain: The application previewed.
        number: The pull request number, or the number of a preview made by hand.
        base_domain: Where previews answer.
        random: What ``{{random}}`` stands for; made when None.

    Returns:
        The domain, lower case.

    Raises:
        ValidationError: A placeholder that does not exist, or a result that is not a
            domain of a length an application can be named after.
    """
    values = {
        "pr_id": pr_id(number),
        "random": random or new_random(),
        "domain": parent_domain,
        "name": domain_to_app_name(parent_domain),
        "base": base_domain,
    }

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValidationError(
                f"Not a placeholder of a domain template: {{{{{key}}}}}",
                details="Use " + ", ".join(f"{{{{{name}}}}}" for name in PLACEHOLDERS) + ".",
                field="domain_template",
            )
        return values[key]

    rendered = _PLACEHOLDER.sub(fill, template).strip().lower()
    try:
        domain = validate_domain(rendered)
    except NoustError as exc:
        raise ValidationError(
            f"The domain template gives {rendered!r}, which is not a domain",
            details=f"{exc.message}. Check the placeholders and the dots.",
            field="domain_template",
        ) from exc
    labels = domain.split(".")
    if len(labels) < 2 or all(label.isdigit() for label in labels):
        raise ValidationError(
            f"The domain template gives {domain}, which is not a domain name",
            details="A preview needs a name of at least two labels, such as "
            "pr-{{pr_id}}.{{base}}; a number or an address is not one.",
            field="domain_template",
        )
    if len(domain_to_app_name(domain)) > MAX_APP_NAME_LENGTH:
        raise ValidationError(
            f"The domain template gives {domain}, which is too long",
            details=f"A preview's domain becomes its application's name: at most "
            f"{MAX_APP_NAME_LENGTH} characters with dots as dashes. Shorten the template or "
            "the base domain.",
            field="domain_template",
        )
    return domain


__all__ = [
    "HASH_LENGTH",
    "LARGEST_NUMBER_CHECKED",
    "MAX_TEMPLATE",
    "MIN_NAME_ROOM",
    "PLACEHOLDERS",
    "check_template",
    "label_prefix",
    "name_room",
    "new_random",
    "pr_id",
    "preview_domain_for",
    "render_template",
]
