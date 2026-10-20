# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A recipe's variables: reading their references, generating them, resolving them.

A variable is an environment variable of the installed application (the ``.env``
of a native one, the Compose project's ``.env`` for a stack). A recipe declares
where each one's value comes from:

- ``generate``: random, in one of the forms of :data:`~noust.recipes.model.GENERATORS`.
  Generated once, at install, and kept: an update never generates it again.
- ``from``: the deployment (``url``, ``fqdn``, ``scheme``, ``https``, ``port``,
  ``app_name``, ``app_path``) or what a requirement provisioned
  (``postgres.url``, ``redis.password``).
- ``value``: a template over other variables, ``${NAME}`` and nothing else.
- ``default`` and ``required``: what the operator is asked for. ``required``
  stops the installation until it is given.

References use the syntax Docker Compose and the shell share, so a Compose
document and a native recipe read the same way: ``${NAME}``, ``${NAME:-default}``
(the default when unset or empty), ``${NAME:?message}`` (an error when unset or
empty), and ``$$`` for a literal dollar sign. A bare ``$NAME`` is refused:
whether it is a variable or text is exactly the doubt this syntax removes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import string
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from noust.core.exceptions import ValidationError
from noust.recipes.model import (
    RESERVED_VARIABLES,
    RecipeError,
    RecipeVariable,
)

#: Letters and digits, as :func:`noust.recipes.render.secret` has always drawn them.
_ALPHANUMERIC = string.ascii_letters + string.digits

#: How long a generated JWT is valid. A signing secret is rotated by the
#: operator, not by an expiry the application cannot renew by itself.
JWT_LIFETIME_SECONDS = 10 * 365 * 24 * 3600

#: ``${NAME}`` and its modifiers; the argument holds neither ``$`` nor ``}``.
_REFERENCE = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])([^${}]*))?\}|\$(?![{$])")

_BARE = re.compile(r"\$(?![{$])")


@dataclass(frozen=True)
class Reference:
    """
    One ``${NAME...}`` in a text.

    Attributes:
        name: The variable.
        operator: ``:-``, ``-``, ``:?``, ``?``, ``:+``, ``+`` or None for a plain reference.
        argument: What follows the operator.
    """

    name: str
    operator: str | None = None
    argument: str = ""

    @property
    def default(self) -> str | None:
        """The value a ``${NAME:-x}`` gives when the variable is unset, or None."""
        return self.argument if self.operator in (":-", "-") else None

    @property
    def requires_value(self) -> bool:
        """Whether this reference is an error when the variable is unset."""
        return self.operator in (":?", "?")


class ReferenceSyntaxError(ValueError):
    """A text uses the reference syntax in a way this module refuses."""


def scan_references(text: str) -> list[Reference]:
    """
    List the variable references in a text, in order.

    Args:
        text: A Compose document, a value template or a note.

    Returns:
        The references, one per occurrence.

    Raises:
        ReferenceSyntaxError: A bare ``$NAME`` (or a lone ``$``), an
            unterminated ``${``, or a modifier this syntax does not have.
    """
    found: list[Reference] = []
    for match in _REFERENCE.finditer(text):
        found_text = match.group(0)
        if found_text == "$$":
            continue
        if found_text == "$":
            context = text[match.start() : match.start() + 24].splitlines()[0]
            raise ReferenceSyntaxError(
                f"{context!r}: a bare $ must be written ${{NAME}}, or $$ for a dollar sign"
            )
        found.append(Reference(match.group(1), match.group(2), match.group(3) or ""))
    if "${" in _REFERENCE.sub("", text.replace("$$", "")):
        raise ReferenceSyntaxError(
            "a ${ is not closed, or holds something other than NAME, NAME:-x or NAME:?x"
        )
    return found


def substitute(text: str, values: Mapping[str, str], *, what: str) -> str:
    """
    Fill the references of a template.

    Args:
        text: The template.
        values: Variable name to value.
        what: What is being filled, for the message.

    Returns:
        The text with every reference replaced and ``$$`` turned into ``$``.

    Raises:
        RecipeError: A ``${NAME:?message}`` whose variable is unset or empty,
            or a plain reference to a variable that has no value.
    """

    def replace(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        name, operator, argument = match.group(1), match.group(2), match.group(3) or ""
        value = values.get(name)
        unset = value is None
        empty = unset or value == ""
        if operator in (":-",):
            return argument if empty else str(value)
        if operator == "-":
            return argument if unset else str(value)
        if operator in (":?",) and empty:
            raise RecipeError(
                f"{what}: {name} is required" + (f" ({argument})" if argument else ""),
                details=f"Give a value for {name} when installing.",
            )
        if operator == "?" and unset:
            raise RecipeError(
                f"{what}: {name} is required" + (f" ({argument})" if argument else ""),
                details=f"Give a value for {name} when installing.",
            )
        if operator in (":+", "+"):
            present = not empty if operator == ":+" else not unset
            return argument if present else ""
        if unset:
            raise RecipeError(
                f"{what}: {name} has no value",
                details=f"Declare {name} under variables in the recipe.",
            )
        return str(value)

    return _REFERENCE.sub(replace, text)


def references_in_order(variables: Iterable[RecipeVariable]) -> list[RecipeVariable]:
    """
    Order variables so that each comes after the ones it is made from.

    Args:
        variables: The recipe's variables.

    Returns:
        The same variables, dependencies first, otherwise in file order.

    Raises:
        RecipeError: Variables depend on each other in a circle, or a
            ``value`` or ``signed_with`` names a variable that is not declared.
    """
    entries = {variable.name: variable for variable in variables}
    ordered: list[RecipeVariable] = []
    state: dict[str, int] = {}

    def depends_on(variable: RecipeVariable) -> list[str]:
        names: list[str] = []
        if variable.value is not None:
            names.extend(ref.name for ref in scan_references(variable.value))
        if variable.signed_with:
            names.append(variable.signed_with)
        return [name for name in names if name not in RESERVED_VARIABLES]

    def visit(variable: RecipeVariable, trail: tuple[str, ...]) -> None:
        if state.get(variable.name) == 2:
            return
        if state.get(variable.name) == 1:
            raise RecipeError(
                f"variables depend on each other in a circle: {' -> '.join((*trail, variable.name))}",
                details="Break the circle: a variable cannot be made from itself.",
            )
        state[variable.name] = 1
        for name in depends_on(variable):
            if name not in entries:
                raise RecipeError(
                    f"{variable.name} uses {name}, which the recipe does not declare",
                    details="Declare it under variables.",
                )
            visit(entries[name], (*trail, variable.name))
        state[variable.name] = 2
        ordered.append(variable)

    for variable in entries.values():
        visit(variable, ())
    return ordered


def generate_value(form: str, *, signing_secret: str | None = None, role: str | None = None) -> str:
    """
    Generate one random value.

    Args:
        form: One of :data:`~noust.recipes.model.GENERATORS`.
        signing_secret: For ``jwt``: the secret that signs it.
        role: For ``jwt``: the ``role`` claim.

    Returns:
        ``user``: 16 lower-case letters and digits starting with a letter.
        ``passwordN``: N letters and digits. ``hexN``: N hexadecimal
        characters. ``base64_N``: the standard base64 of N random bytes.
        ``jwt``: an HS256 token with a ``role`` and ten years of validity.

    Raises:
        ValidationError: The form is not one of the closed list, or a
            ``jwt`` has no secret to sign with.
    """
    if form == "user":
        head = secrets.choice(string.ascii_lowercase)
        tail = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(15))
        return head + tail
    if form in ("password32", "password64"):
        return random_alphanumeric(int(form.removeprefix("password")))
    if form in ("hex32", "hex64"):
        return random_hex_digits(int(form.removeprefix("hex")))
    if form in ("base64_32", "base64_64"):
        return random_base64(int(form.removeprefix("base64_")))
    if form == "jwt":
        if not signing_secret:
            raise ValidationError(
                "A JWT variable has nothing to sign with",
                details="Name the variable that holds the secret with signed_with.",
            )
        return _jwt(signing_secret, role or "anon")
    raise ValidationError(
        f"Unknown form of generated value: {form!r}", details="See noust.recipes.model.GENERATORS."
    )


def random_alphanumeric(length: int, *, lowercase: bool = False) -> str:
    """
    Draw random letters and digits.

    Args:
        length: How many characters.
        lowercase: Digits and lower-case letters only (for a database name or a user, which
            some engines fold or refuse in upper case).

    Returns:
        The string.
    """
    alphabet = string.ascii_lowercase + string.digits if lowercase else _ALPHANUMERIC
    return "".join(secrets.choice(alphabet) for _ in range(length))


def random_hex_digits(length: int) -> str:
    """
    Draw random hexadecimal digits.

    Args:
        length: How many digits (an odd count is allowed).

    Returns:
        The string, lower case.
    """
    return secrets.token_hex((length + 1) // 2)[:length]


def random_base64(byte_count: int) -> str:
    """
    Draw random bytes and write them in standard base64.

    Args:
        byte_count: How many random bytes.

    Returns:
        The base64 text (longer than ``byte_count``).
    """
    return base64.b64encode(secrets.token_bytes(byte_count)).decode()


def signed_jwt(signing_secret: str, claims: Mapping[str, object]) -> str:
    """
    Sign an HS256 JWT with the claims given, exactly as given.

    Args:
        signing_secret: The shared secret.
        claims: The payload; the caller decides ``iat`` and ``exp``.

    Returns:
        The compact token.
    """

    def part(value: Mapping[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    signing_input = ".".join((part({"alg": "HS256", "typ": "JWT"}), part(claims)))
    signature = hmac.new(signing_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _jwt(signing_secret: str, role: str) -> str:
    """
    Sign an HS256 JWT.

    Args:
        signing_secret: The shared secret.
        role: The ``role`` claim.

    Returns:
        The compact token.
    """
    issued = int(time.time())
    return signed_jwt(
        signing_secret,
        {"role": role, "iss": "noust", "iat": issued, "exp": issued + JWT_LIFETIME_SECONDS},
    )


@dataclass(frozen=True)
class DeploymentContext:
    """
    What a deployment itself gives a variable (``from: url``).

    Attributes:
        domain: The application's domain.
        ssl: Whether it is served over TLS.
        port: The port the web server proxies to.
        app_name: The application name (the domain with dashes).
        app_path: The directory it is deployed in.
    """

    domain: str
    ssl: bool
    port: int | None
    app_name: str
    app_path: str

    def value(self, source: str) -> str:
        """
        Read one of :data:`~noust.recipes.model.CONTEXT_SOURCES`.

        Args:
            source: ``url``, ``fqdn``, ``scheme``, ``https``, ``port``,
                ``app_name`` or ``app_path``.

        Returns:
            The value as text. ``https`` is ``true`` or ``false``.

        Raises:
            ValidationError: It is none of those, or ``port`` was not decided.
        """
        scheme = "https" if self.ssl else "http"
        if source == "url":
            return f"{scheme}://{self.domain}"
        if source == "fqdn":
            return self.domain
        if source == "scheme":
            return scheme
        if source == "https":
            return "true" if self.ssl else "false"
        if source == "port":
            if self.port is None:
                raise ValidationError("This deployment has no port yet", details="Choose one.")
            return str(self.port)
        if source == "app_name":
            return self.app_name
        if source == "app_path":
            return self.app_path
        raise ValidationError(f"Unknown deployment value: {source!r}")


@dataclass
class ResolvedVariables:
    """
    The values a recipe's variables resolved to.

    Attributes:
        values: Variable name to value, the recipe's own and the operator's extras.
        secrets: The names whose value is a secret.
        generated: The names whose value was generated now.
    """

    values: dict[str, str] = field(default_factory=dict)
    secrets: set[str] = field(default_factory=set)
    generated: set[str] = field(default_factory=set)


def missing_inputs(
    variables: Iterable[RecipeVariable],
    overrides: Mapping[str, str],
    existing: Mapping[str, str] | None = None,
) -> list[RecipeVariable]:
    """
    List the required variables nobody has given a value.

    Args:
        variables: The recipe's variables.
        overrides: What the operator gave.
        existing: What the application already has (an update).

    Returns:
        The variables, in file order, that are required and have no non-empty
        value in ``overrides`` or ``existing``.
    """
    existing = existing or {}
    return [
        variable
        for variable in variables
        if variable.is_required
        and not (overrides.get(variable.name) or existing.get(variable.name))
    ]


def require_inputs(
    title: str,
    variables: Iterable[RecipeVariable],
    overrides: Mapping[str, str],
    existing: Mapping[str, str] | None = None,
) -> None:
    """
    Stop until every required variable has a value.

    This is what ``${VAR:?required}`` means for a recipe: nothing is
    provisioned, written or deployed before the operator has filled them in.

    Args:
        title: The recipe's title, for the message.
        variables: The recipe's variables.
        overrides: What the operator gave.
        existing: What the application already has (an update).

    Raises:
        RecipeError: Naming every missing variable at once, so the operator
            fixes them in one go.
    """
    missing = missing_inputs(variables, overrides, existing)
    if not missing:
        return
    names = ", ".join(variable.name for variable in missing)
    lines = "\n".join(
        f"  {variable.name}" + (f": {variable.description}" if variable.description else "")
        for variable in missing
    )
    raise RecipeError(
        f"{title} needs a value for {names}",
        details=f"Give each one when installing (--var NAME=VALUE, or variables in the API):\n"
        f"{lines}",
        field="variables",
    )


def refuse_compose_dollar(title: str, values: Mapping[str, str]) -> None:
    """
    Refuse a value a Compose stack would read as a variable.

    Compose interpolates a ``$`` in the values of a stack's ``.env``, so a
    password with one in it reaches the container as something else without a
    word. Everything Noust generates is letters and digits; this is for what
    the operator gives.

    Args:
        title: The recipe's title, for the message.
        values: Variable name to value, as the operator gave them.

    Raises:
        RecipeError: Naming every variable whose value has a ``$``.
    """
    names = sorted(name for name, value in values.items() if "$" in value)
    if names:
        raise RecipeError(
            f"{title} runs as a Compose stack, which reads a $ in {', '.join(names)} as a variable",
            details="Use a value without a dollar sign: Compose would hand the container "
            "something other than what was given.",
            field="variables",
        )


def resolve_variables(
    variables: Iterable[RecipeVariable],
    *,
    context: DeploymentContext,
    provisioned: Mapping[str, Mapping[str, str]],
    overrides: Mapping[str, str],
    existing: Mapping[str, str] | None = None,
) -> ResolvedVariables:
    """
    Give every variable its value.

    Precedence, for each variable: what the operator gave, then what the
    application already has (so an update never regenerates a secret and
    never undoes an edit), then what the recipe says.

    Args:
        variables: The recipe's variables.
        context: What the deployment gives (``from: url``).
        provisioned: Requirement name to attribute to value, for
            ``from: postgres.url``.
        overrides: What the operator gave; names the recipe does not declare
            are kept as they are.
        existing: What the application already has.

    Returns:
        The values and which are secret.

    Raises:
        RecipeError: A required variable has no value, or a reference has
            nothing to refer to.
    """
    existing = existing or {}
    declared = list(variables)
    resolved = ResolvedVariables()
    for variable in references_in_order(declared):
        name = variable.name
        if variable.secret:
            resolved.secrets.add(name)
        if overrides.get(name) is not None:
            resolved.values[name] = overrides[name]
            continue
        if name in existing:
            resolved.values[name] = existing[name]
            continue
        if variable.generate is not None:
            resolved.values[name] = generate_value(
                variable.generate,
                signing_secret=resolved.values.get(variable.signed_with or ""),
                role=variable.role,
            )
            resolved.generated.add(name)
        elif variable.source is not None:
            resolved.values[name] = _from_source(variable, context, provisioned)
        elif variable.value is not None:
            known = dict(resolved.values)
            if context.port is not None:
                known["PORT"] = str(context.port)
            resolved.values[name] = substitute(variable.value, known, what=name)
        elif variable.default is not None:
            resolved.values[name] = variable.default
        else:
            raise RecipeError(
                f"{name} is required",
                details=f"Give it when installing: --var {name}=VALUE.",
                field="variables",
            )
    for name, value in overrides.items():
        resolved.values.setdefault(name, value)
    return resolved


def _from_source(
    variable: RecipeVariable,
    context: DeploymentContext,
    provisioned: Mapping[str, Mapping[str, str]],
) -> str:
    """
    Read a ``from:`` variable.

    Args:
        variable: The variable.
        context: What the deployment gives.
        provisioned: What each requirement provisioned.

    Returns:
        The value.

    Raises:
        RecipeError: The requirement was not provisioned or lacks the attribute.
    """
    source = variable.source or ""
    if "." not in source:
        return context.value(source)
    requirement, attribute = source.split(".", 1)
    values = provisioned.get(requirement)
    if values is None or attribute not in values:
        raise RecipeError(
            f"{variable.name} is {source}, which was not provisioned",
            details=f"Install and start {requirement} on this server, then install the recipe again.",
        )
    return values[attribute]
