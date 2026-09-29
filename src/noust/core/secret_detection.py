# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one classifier for whether an environment variable's value is a secret.

Before this module WASM had three independent opinions on the question, and
each missed what the others caught: :func:`wasm.core.config.is_secret_key`
split a name into words and matched markers such as ``password`` or ``auth``;
:data:`wasm.deployers.helpers.env_manager.EnvManager.SECRET_PATTERNS` matched
substrings such as ``_PASS`` or ``API_KEY``; and
:func:`wasm.deployers.helpers.env_manager.redact_url_credentials` caught a
password embedded in a connection string, but only that shape. None of the
three ever looked at a value that did not sit inside a URL, so
``STRIPE_SK=sk_live_...`` and a random session token both passed as harmless,
and none of them let an operator say "this one is different" - ``PUBLIC_KEY``
and ``NEXT_PUBLIC_API_KEY`` were redacted every time despite carrying nothing
secret.

:func:`classify` replaces all three call sites (masking a ``.env`` for the
console, scrubbing deploy logs, ``EnvManager``'s own masking) with one
decision, in one order of precedence:

1. **An operator's own mark** on this variable, if there is one. It always
   wins, in both directions: a value that looks exactly like a Stripe key but
   is not one, or a value that looks harmless but is a customer's password,
   is the operator's call to make once they have made it.
2. **What the value looks like.** A Stripe, GitHub, GitLab, Slack, AWS,
   Google, SendGrid or Twilio token, a private key, a JWT, a URL with
   credentials embedded, or a string random enough that it is unlikely to be
   anything else, is a secret whatever its variable happens to be called.
3. **What the name looks like** - the two heuristics above, merged.
4. **A name that says "this is public"** lowers a name-only verdict (never a
   value-based one: a public-looking name carrying an actual Stripe secret
   key is still a Stripe secret key). ``NEXT_PUBLIC_``, ``VITE_``,
   ``PUBLIC_``, ``NUXT_PUBLIC_`` and ``REACT_APP_`` are the prefixes several
   frameworks bake straight into the client bundle, so a name matching one of
   them is public by the framework's own contract. A name ending
   ``PUBLIC_KEY`` is treated the same way unless the value is itself a
   private key - it is the ``KEY`` in the name that trips the generic
   heuristic, not anything about what a public key actually is. The
   relaxation itself never fires when the rest of the name still reads as a
   password or a private credential - ``PASSWORD``, ``PASSWD``, ``PASS``,
   ``SECRET_KEY``, ``PRIVATE_KEY`` or ``TOKEN``: ``PUBLIC_DB_PASSWORD`` is
   still a password whatever prefix precedes it. Decided conservatively on
   purpose, the same way as :func:`_looks_like_high_entropy_secret`: a
   developer's lazy or mistaken name does not change what the value
   protects, and showing a credential in clear is a worse failure than
   masking one that did not need it.

This only ever governs what a name-only verdict *displays* as. Scrubbing
build and job logs (:func:`wasm.core.redact.secret_env_values`) does not use
this relaxation at all: it must remain a superset of the name-only heuristic
regardless of a public-looking prefix, so over-scrubbing a log is preferred
to a secret shaped like ``NEXT_PUBLIC_API_KEY`` appearing in one verbatim.

Nothing here is perfect: a high-entropy value with no other signal is the
last resort, not the first, and stays conservative on purpose (see
:func:`_looks_like_high_entropy_secret`) because a false positive here means
an operator's non-secret value is masked or scrubbed, which is an annoyance,
while a false negative means a real secret is shown or logged in clear.
"""

from __future__ import annotations

import base64
import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from wasm.core.config import REDACTED, is_secret_key

#: A credential embedded in a connection string. ``DATABASE_URL`` is the
#: canonical example: nothing in the *name* marks it as a secret, yet the
#: value carries the database password in clear.
#:
#: The user name is optional on purpose. ``redis://:password@host:6379`` is
#: the form redis-py, Heroku Redis and docker-compose all produce, and a
#: pattern that demands a user before the colon lets exactly that one through
#: in clear. The password may not contain ``/``, ``?`` or ``#``: those end
#: the authority in RFC 3986, so refusing them keeps
#: ``http://host:8080/a@b`` from being read as a credential and redacted.
URL_CREDENTIALS = re.compile(r"(?P<prefix>[A-Za-z][A-Za-z0-9+.\-]*://[^:/?#@\s]*:)[^@\s/?#]*@")


def redact_url_credentials(value: str, placeholder: str = REDACTED) -> str:
    """
    Replace the password inside every connection string in a value.

    Args:
        value: Text that may contain one or more URLs.
        placeholder: What to put where the password was.

    Returns:
        The value with every embedded password replaced. Text carrying no
        credential is returned unchanged.
    """
    return URL_CREDENTIALS.sub(lambda match: f"{match.group('prefix')}{placeholder}@", value)


#: Substrings that mark a variable's *name* as a secret, matched
#: case-insensitively against the whole name. Moved out of ``EnvManager``,
#: which now imports this list rather than keeping its own copy - see the
#: module docstring.
NAME_PATTERNS: tuple[str, ...] = (
    "PASSWORD",
    "_PASS",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "PRIVATE_KEY",
    "ENCRYPTION_KEY",
    "SIGNING_KEY",
    "ACCESS_KEY",
    "SECRET_KEY",
    "CLIENT_SECRET",
    "WEBHOOK_SECRET",
)


def name_looks_secret(name: str) -> bool:
    """
    Decide whether a variable's name alone marks its value as a secret.

    :data:`NAME_PATTERNS` matches substrings (``ADMIN_PASS``,
    ``STRIPE_API_KEY``); :func:`~wasm.core.config.is_secret_key` matches
    whole words the configuration redacts (``AUTH``, ``SLACK_WEBHOOK``,
    ``apiKey``). Each misses names the other catches, so a name is a secret
    when either says so. This is the name-only step of :func:`classify`;
    call it directly only when there is no value to look at.

    Args:
        name: Variable name.

    Returns:
        True if the name alone suggests the value behind it is a secret.
    """
    upper = name.upper()
    return any(pattern in upper for pattern in NAME_PATTERNS) or is_secret_key(name)


#: A PEM private key of any common kind. Matching the block header is enough:
#: nothing else legitimately starts a line this way.
_PRIVATE_KEY_PEM = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")

#: A PEM *public* key or certificate: not a secret whatever it is named or
#: how random its base64 body looks, so it is excluded before the
#: high-entropy check ever sees it - the same reasoning as a git commit hash
#: or a UUID, just specific to this shape.
_PUBLIC_KEY_PEM = re.compile(
    r"-----BEGIN (?:(?:RSA |EC |DSA |OPENSSH )?PUBLIC KEY|CERTIFICATE)-----"
)

#: Vendor token shapes, checked in order from most to least specific. Each
#: pattern is deliberately narrow: it exists to catch a value no name-based
#: heuristic would ever flag (``STRIPE_SK`` looks secret by name already;
#: the point of these is the value itself, wherever it turns up).
_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("stripe", re.compile(r"\b(?:sk_live_|sk_test_|rk_live_|whsec_)[A-Za-z0-9]+")),
    ("github token", re.compile(r"\b(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]+")),
    ("gitlab token", re.compile(r"\bglpat-[A-Za-z0-9_\-]+")),
    ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]+")),
    ("aws access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("sendgrid api key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b")),
    ("twilio credential", re.compile(r"\b(?:SK|AC)[0-9a-fA-F]{32}\b")),
    # OpenAI- and Anthropic-style keys: "sk-" followed by at least 32 more
    # characters. Checked after Stripe, which uses "sk_" (underscore) rather
    # than "sk-" (hyphen), so the two shapes cannot collide.
    ("api key", re.compile(r"\bsk-[A-Za-z0-9_\-]{32,}\b")),
)

#: A value random enough, or with no other explanation, that it is treated as
#: a secret on its shape alone. Deliberately conservative: see
#: :func:`_looks_like_high_entropy_secret`.
_MIN_ENTROPY_LENGTH = 24
_MIN_ENTROPY_BITS = 4.0
_MIN_CHAR_CLASSES = 3

_UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_HEX_SHA_RE = re.compile(r"\A[0-9a-fA-F]{40}\Z")
#: A value that reads as a filesystem path rather than a credential: starts
#: with a root, a relative prefix or a home directory, or a drive letter.
_FS_PATH_RE = re.compile(r"\A(?:[A-Za-z]:[\\/]|\.{1,2}/|~/|/)\S*\Z")
#: A value with a URL scheme and no embedded credential (one is caught by
#: :data:`URL_CREDENTIALS` before this is ever consulted): an ordinary link,
#: not a token, however random its path segment looks.
_URL_SCHEME_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9+.\-]*://")


def _looks_like_jwt(value: str) -> bool:
    """
    Decide whether a value is a JSON Web Token.

    A JWT is three base64url segments separated by dots, and the first one -
    the header - decodes to a JSON object naming its algorithm. Checking that
    the header actually decodes and names ``alg`` is what tells a real token
    apart from three dot-separated words that happen to look similar.

    Args:
        value: Candidate value.

    Returns:
        True if the value has the shape of a JWT.
    """
    parts = value.split(".")
    if len(parts) != 3 or not all(parts):
        return False
    header = parts[0]
    padded = header + "=" * (-len(header) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded)
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and "alg" in payload


def _char_class_count(value: str) -> int:
    """
    Count how many character classes a value mixes.

    Args:
        value: Candidate value.

    Returns:
        How many of lower case, upper case, digit and "everything else" are
        present at least once.
    """
    classes = 0
    if any(c.islower() for c in value):
        classes += 1
    if any(c.isupper() for c in value):
        classes += 1
    if any(c.isdigit() for c in value):
        classes += 1
    if any(not c.isalnum() for c in value):
        classes += 1
    return classes


def _shannon_entropy(value: str) -> float:
    """
    Compute the Shannon entropy of a value, in bits per character.

    Args:
        value: Candidate value; must be non-empty.

    Returns:
        The entropy: low for repetitive or structured text, high for
        something close to random.
    """
    length = len(value)
    counts = Counter(value)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _looks_like_high_entropy_secret(value: str) -> bool:
    """
    Decide whether a value is random enough to be treated as a secret.

    The last and broadest of the value patterns, so it stays conservative:
    long enough that a short word or a version string cannot qualify, mixing
    enough character classes that a plain sentence or a single hex run
    cannot either, and excluding the shapes that are random-looking for a
    reason that has nothing to do with being a secret - a UUID, a git commit
    hash, a filesystem path, a URL with no credential in it, or a PEM public
    key or certificate, whose base64 body is exactly as random as a private
    key's and is not a secret either.

    Args:
        value: Candidate value.

    Returns:
        True if the value looks like a secret on its shape alone.
    """
    if len(value) < _MIN_ENTROPY_LENGTH:
        return False
    if _UUID_RE.match(value) or _HEX_SHA_RE.match(value):
        return False
    if _FS_PATH_RE.match(value) or _URL_SCHEME_RE.match(value):
        return False
    if _PUBLIC_KEY_PEM.search(value):
        return False
    if _char_class_count(value) < _MIN_CHAR_CLASSES:
        return False
    return _shannon_entropy(value) >= _MIN_ENTROPY_BITS


def _value_pattern_kind(value: str) -> str | None:
    """
    Classify a value by its own shape, ignoring the variable's name.

    Checked in order from most to least specific: a private key block, each
    vendor's token shape, a JWT, a URL with credentials, and finally a
    generic high-entropy string.

    Args:
        value: Candidate value.

    Returns:
        ``"url credentials"``, a vendor kind such as ``"stripe"`` or
        ``"jwt"``, ``"high entropy"``, or None when nothing matches.
    """
    if not value:
        return None
    if _PRIVATE_KEY_PEM.search(value):
        return "private key"
    for kind, pattern in _VALUE_PATTERNS:
        if pattern.search(value):
            return kind
    if _looks_like_jwt(value):
        return "jwt"
    if URL_CREDENTIALS.search(value):
        return "url credentials"
    if _looks_like_high_entropy_secret(value):
        return "high entropy"
    return None


#: Name prefixes several frameworks bake straight into the client bundle -
#: anything named this way is public by the framework's own contract, so a
#: name-only "secret" verdict on it is a false positive, not a finding.
_PUBLIC_NAME_PREFIXES: tuple[str, ...] = (
    "NEXT_PUBLIC_",
    "VITE_",
    "PUBLIC_",
    "NUXT_PUBLIC_",
    "REACT_APP_",
)


def _looks_like_private_key_pem(value: str) -> bool:
    """
    Report whether a value is a PEM private key block.

    Args:
        value: Candidate value.

    Returns:
        True if the value contains a private key block.
    """
    return bool(_PRIVATE_KEY_PEM.search(value))


#: Words that keep the public-prefix relaxation below from ever firing,
#: matched as substrings against the whole name (case-insensitive), the same
#: way :data:`NAME_PATTERNS` is. ``PASSWORD``, ``PASSWD`` and ``PASS`` overlap
#: on purpose - the full word, the common abbreviation, and the bare stem a
#: short name uses (``DB_PASS``) - so the list reads the same as the
#: reasoning in the module docstring rather than relying on one substring to
#: catch all three. ``SECRET_KEY`` and ``PRIVATE_KEY`` are the compound forms
#: an encryption or signing key is named with, deliberately narrower than the
#: bare ``SECRET`` :data:`NAME_PATTERNS` already matches: a framework-prefixed
#: ``VITE_API_SECRET`` is, if the framework's contract holds, genuinely baked
#: into the client bundle, so it stays eligible for the relaxation.
_NEVER_PUBLIC_NAME_WORDS: tuple[str, ...] = (
    "PASSWORD",
    "PASSWD",
    "PASS",
    "SECRET_KEY",
    "PRIVATE_KEY",
    "TOKEN",
)


def _is_public_looking(name: str, value: str) -> bool:
    """
    Decide whether a name-only "secret" verdict should be overridden to public.

    Only ever asked once the value itself has already been checked against
    every value pattern and found nothing (see :func:`classify`), so this
    never overrides a value that actually looks like a Stripe key, a private
    key or the like - only the generic ``KEY``/``TOKEN``/``SECRET`` word
    match on a name that says it is meant to be public.

    The relaxation is refused outright when the rest of the name still reads
    as a password or a private credential (:data:`_NEVER_PUBLIC_NAME_WORDS`):
    ``PUBLIC_DB_PASSWORD`` must stay hidden even though ``PUBLIC_`` is one of
    the framework prefixes, because a name that contradicts itself this way
    is far more likely a careless name on a real secret than a framework
    variable that happens to mention a password.

    Args:
        name: Variable name.
        value: Its value, to tell an actual private key apart from a name
            merely ending ``PUBLIC_KEY``.

    Returns:
        True if the name marks this variable as intentionally public.
    """
    upper = name.upper()
    if any(word in upper for word in _NEVER_PUBLIC_NAME_WORDS):
        return False
    if upper.startswith(_PUBLIC_NAME_PREFIXES):
        return True
    if upper.endswith("PUBLIC_KEY"):
        return not _looks_like_private_key_pem(value)
    return False


@dataclass(frozen=True)
class Secrecy:
    """
    The verdict on whether a value must be treated as a secret, and why.

    Attributes:
        secret: Whether the value must not be shown or logged in clear.
        reason: One of ``"marked secret"``, ``"marked not secret"``,
            ``"name"``, ``"value: <kind>"``, ``"url credentials"`` or
            ``"plain"``.
        marked: Whether this verdict came from an explicit operator mark
            rather than from the value or the name.
    """

    secret: bool
    reason: str
    marked: bool = False


def classify(name: str, value: str, marks: Mapping[str, bool] | None = None) -> Secrecy:
    """
    Decide whether one environment variable's value is a secret, and why.

    The one classifier described in the module docstring. Precedence, each
    step consulted only when the previous one had nothing to say:

    1. ``marks[name]``, if present - the operator's own call, in either
       direction.
    2. What the value itself looks like (:func:`_value_pattern_kind`).
    3. What the name looks like (:func:`name_looks_secret`), unless the name
       says the variable is meant to be public
       (:func:`_is_public_looking`), which only ever lowers this step - a
       value that already matched step 2 is never reconsidered here.

    Args:
        name: Variable name.
        value: Its value.
        marks: Operator overrides for this application, name to True
            (secret) or False (not secret). None or a name absent from it
            falls through to the value and the name.

    Returns:
        The verdict.
    """
    if marks is not None and name in marks:
        mark = marks[name]
        return Secrecy(
            secret=bool(mark), reason="marked secret" if mark else "marked not secret", marked=True
        )

    kind = _value_pattern_kind(value)
    if kind is not None:
        reason = kind if kind == "url credentials" else f"value: {kind}"
        return Secrecy(secret=True, reason=reason)

    if name_looks_secret(name):
        if _is_public_looking(name, value):
            return Secrecy(secret=False, reason="plain")
        return Secrecy(secret=True, reason="name")

    return Secrecy(secret=False, reason="plain")


def classify_all(
    values: Mapping[str, str], marks: Mapping[str, bool] | None = None
) -> dict[str, Secrecy]:
    """
    Classify every variable of an environment at once.

    Args:
        values: Variable name to value.
        marks: Operator overrides, as for :func:`classify`.

    Returns:
        Variable name to its verdict.
    """
    return {name: classify(name, value, marks) for name, value in values.items()}
