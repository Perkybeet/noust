# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sign-in policy: timeouts, lockout, password length, tokens, the notice.

Read from the configuration (``security.profile`` and the ``auth`` section)
every time it is asked for, so a change made with ``noust config set`` or the
console takes effect without a restart, in the CLI and the console alike.

The ``ens-medium`` profile (ENS category MEDIUM, RD 311/2022) does not add
settings; it tightens the ones there are. Each is capped at the profile's
value, so an operator can be stricter than the profile but never looser while
it is on: idle 15 minutes, 8 hours per session, 5 failures, 14-character
passwords, 90-day tokens, the master token only for account recovery.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

# The profiles and their numbers are stated once, in noust.core.ens.profile,
# which every area reads (rule 3); the names below are kept for the callers
# that import them from here.
from noust.core.ens.profile import (
    ENS_MEDIUM,
    PROFILE_ENS_MEDIUM,
    PROFILE_STANDARD,
    PROFILES,
    STANDARD,
    normalise_profile,
)

__all__ = [
    "PROFILES",
    "PROFILE_ENS_MEDIUM",
    "PROFILE_STANDARD",
    "AuthPolicy",
    "build_policy",
    "load_policy",
    "normalise_profile",
]

#: What each setting is outside the profile, and what the profile caps it at.
STANDARD_IDLE_MINUTES = STANDARD.idle_minutes
STANDARD_ABSOLUTE_HOURS = STANDARD.absolute_hours
STANDARD_LOCKOUT_THRESHOLD = STANDARD.lockout_threshold
STANDARD_LOCKOUT_MINUTES = STANDARD.lockout_minutes
STANDARD_PASSWORD_MIN_LENGTH = STANDARD.password_min_length
ENS_IDLE_MINUTES = ENS_MEDIUM.idle_minutes
ENS_ABSOLUTE_HOURS = ENS_MEDIUM.absolute_hours
ENS_LOCKOUT_THRESHOLD = ENS_MEDIUM.lockout_threshold
ENS_LOCKOUT_MINUTES = ENS_MEDIUM.lockout_minutes
ENS_PASSWORD_MIN_LENGTH = ENS_MEDIUM.password_min_length
ENS_TOKEN_MAX_DAYS: int = ENS_MEDIUM.token_max_days or 90


@dataclass(frozen=True)
class AuthPolicy:
    """
    The sign-in policy in force.

    Attributes:
        profile: ``standard`` or ``ens-medium``.
        idle_minutes: A session unused for this long is over.
        absolute_hours: A session is over this long after sign-in, however
            active it is.
        lockout_threshold: Consecutive refused sign-ins that lock an account.
        lockout_minutes: How long that lock lasts.
        password_min_length: Shortest password accepted.
        token_max_days: Longest life of an API token, and its default, under
            the profile; None outside it, where an expiry is optional.
        notice_text: The rights-and-obligations notice shown after sign-in,
            or empty for none.
        login_label: What the sign-in page shows before anyone is signed in,
            chosen by the operator; empty shows nothing (no hostname).
    """

    profile: str = PROFILE_STANDARD
    idle_minutes: int = STANDARD_IDLE_MINUTES
    absolute_hours: int = STANDARD_ABSOLUTE_HOURS
    lockout_threshold: int = STANDARD_LOCKOUT_THRESHOLD
    lockout_minutes: int = STANDARD_LOCKOUT_MINUTES
    password_min_length: int = STANDARD_PASSWORD_MIN_LENGTH
    token_max_days: int | None = None
    notice_text: str = ""
    login_label: str = ""

    @property
    def ens(self) -> bool:
        """Whether the ENS category MEDIUM profile is on."""
        return self.profile == PROFILE_ENS_MEDIUM

    @property
    def notice_version(self) -> str | None:
        """
        The version of the notice: a digest of its text, so editing the text
        asks everybody to accept it again. None when there is no notice.
        """
        text = self.notice_text.strip()
        if not text:
            return None
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The policy as the API shows it.
        """
        return {
            "profile": self.profile,
            "idle_minutes": self.idle_minutes,
            "absolute_hours": self.absolute_hours,
            "lockout_threshold": self.lockout_threshold,
            "lockout_minutes": self.lockout_minutes,
            "password_min_length": self.password_min_length,
            "token_max_days": self.token_max_days,
            "notice_version": self.notice_version,
        }


def _positive_int(value: Any, default: int) -> int:
    """
    Args:
        value: A configured number, possibly a string or garbage.
        default: What to use when it is not a positive integer.

    Returns:
        The number, or the default.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def build_policy(settings: dict[str, Any]) -> AuthPolicy:
    """
    Turn configuration values into the policy, applying the profile's caps.

    Args:
        settings: ``profile`` and the ``auth`` section's values, flattened as
            :func:`load_policy` reads them.

    Returns:
        The policy.
    """
    profile = normalise_profile(settings.get("profile"))
    idle = _positive_int(settings.get("idle_minutes"), STANDARD_IDLE_MINUTES)
    absolute = _positive_int(settings.get("absolute_hours"), STANDARD_ABSOLUTE_HOURS)
    threshold = _positive_int(settings.get("lockout_threshold"), STANDARD_LOCKOUT_THRESHOLD)
    lock_minutes = _positive_int(settings.get("lockout_minutes"), STANDARD_LOCKOUT_MINUTES)
    min_length = _positive_int(settings.get("password_min_length"), STANDARD_PASSWORD_MIN_LENGTH)
    token_days: int | None = None
    if profile == PROFILE_ENS_MEDIUM:
        idle = min(idle, ENS_IDLE_MINUTES)
        absolute = min(absolute, ENS_ABSOLUTE_HOURS)
        threshold = min(threshold, ENS_LOCKOUT_THRESHOLD)
        lock_minutes = max(lock_minutes, ENS_LOCKOUT_MINUTES)
        min_length = max(min_length, ENS_PASSWORD_MIN_LENGTH)
        token_days = min(
            _positive_int(settings.get("token_max_days"), ENS_TOKEN_MAX_DAYS), ENS_TOKEN_MAX_DAYS
        )
    return AuthPolicy(
        profile=profile,
        idle_minutes=idle,
        absolute_hours=absolute,
        lockout_threshold=threshold,
        lockout_minutes=lock_minutes,
        password_min_length=max(8, min_length),
        token_max_days=token_days,
        notice_text=str(settings.get("notice_text") or ""),
        login_label=str(settings.get("login_label") or "").strip()[:64],
    )


def load_policy() -> AuthPolicy:
    """
    Read the policy in force from the configuration.

    Returns:
        The policy; the standard one when the configuration cannot say.
    """
    from noust.core.config import Config

    config = Config()
    return build_policy(
        {
            "profile": config.get("security.profile", PROFILE_STANDARD),
            "idle_minutes": config.get("auth.session.idle_minutes"),
            "absolute_hours": config.get("auth.session.absolute_hours"),
            "lockout_threshold": config.get("auth.lockout.threshold"),
            "lockout_minutes": config.get("auth.lockout.minutes"),
            "password_min_length": config.get("auth.password.min_length"),
            "token_max_days": config.get("auth.tokens.max_days"),
            "notice_text": config.get("auth.notice.text"),
            "login_label": config.get("auth.login_label"),
        }
    )
