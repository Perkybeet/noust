# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sign-in policy: timeouts, lockout, password length, tokens, the notice.

Read from the configuration (``security.profile`` and the ``auth`` section)
every time it is asked for, so a change made with ``noust config set`` or the
console takes effect without a restart, in the CLI and the console alike.

A compliance framework (:mod:`noust.core.compliance`) does not add settings; it
tightens the ones there are. Each is capped at the strictest value any enabled
framework asks, so an operator can be stricter than a framework but never looser
while it is on. With ENS MEDIA (what ``security.profile: ens-medium`` means) that
is: idle 15 minutes, 8 hours per session, 5 failures, 14-character passwords,
90-day tokens, and sudo mode that stays open 10 minutes while used, 30 at most,
asking the password again.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

# The profiles and their numbers are stated once, in noust.core.ens.profile,
# which every area reads (rule 3); the names below are kept for the callers
# that import them from here.
from noust.core.compliance import resolve
from noust.core.ens.profile import (
    ENS_MEDIUM,
    PROFILE_ENS_MEDIUM,
    PROFILE_STANDARD,
    PROFILES,
    STANDARD,
    SUDO_IDLE_RANGE,
    SUDO_MAX_RANGE,
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
ENS_PASSWORD_MIN_LENGTH = ENS_MEDIUM.password_min_length
STANDARD_SUDO_IDLE_MINUTES = STANDARD.sudo_idle_minutes
STANDARD_SUDO_MAX_MINUTES = STANDARD.sudo_max_minutes


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
        sudo_idle_minutes: Sudo mode stays open this long after the last
            elevated action performed in it.
        sudo_max_minutes: The longest sudo mode lasts from the moment it was
            confirmed, however busy the session is; never below the idle
            window.
        sudo_require_password: Re-confirming asks for the account's password
            as well as one second factor. Always true where a framework asks.
        frameworks: The compliance frameworks that are on.
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
    sudo_idle_minutes: int = STANDARD_SUDO_IDLE_MINUTES
    sudo_max_minutes: int = STANDARD_SUDO_MAX_MINUTES
    sudo_require_password: bool = STANDARD.sudo_require_password
    frameworks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """A policy built by hand with the profile name alone still has its framework."""
        if self.profile == PROFILE_ENS_MEDIUM and not self.frameworks:
            object.__setattr__(self, "frameworks", ("ens-media",))

    def requires(self, control_id: str) -> bool:
        """
        Whether a compliance control is on for this policy.

        A policy is built from the frameworks that were on when it was read, so the
        question is asked of it rather than of the configuration: an account manager
        given a policy judges by that policy, as it does for every other limit.
        The operator's own setting for the control counts on top, as it does
        everywhere (:mod:`noust.core.compliance.resolve`).

        Args:
            control_id: A control's id.

        Returns:
            True when a framework of the policy asks for it, or the operator did.
        """
        from noust.core.compliance import controls

        control = controls.get(control_id)
        resolution = resolve.resolve_for(
            control_id,
            self.frameworks,
            resolve.operator_value(control, None, forced=bool(self.frameworks)),
            has_operator=True,
        )
        return resolution.on

    @property
    def ens(self) -> bool:
        """
        Whether a framework of the ENS family is on.

        For what is about the ENS itself. What a server refuses or requires is a
        control's, asked of :mod:`noust.core.compliance`.
        """
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
            "frameworks": list(self.frameworks),
            "idle_minutes": self.idle_minutes,
            "absolute_hours": self.absolute_hours,
            "lockout_threshold": self.lockout_threshold,
            "lockout_minutes": self.lockout_minutes,
            "password_min_length": self.password_min_length,
            "token_max_days": self.token_max_days,
            "notice_version": self.notice_version,
            "sudo_idle_minutes": self.sudo_idle_minutes,
            "sudo_max_minutes": self.sudo_max_minutes,
            "sudo_require_password": self.sudo_require_password,
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


def _clamped_int(value: Any, default: int, bounds: tuple[int, int]) -> int:
    """
    Args:
        value: A configured number, possibly a string or garbage.
        default: What to use when it is not a positive integer.
        bounds: The least and the most accepted, inclusive.

    Returns:
        The number, or the default, held inside the bounds.
    """
    low, high = bounds
    return min(max(_positive_int(value, default), low), high)


def _flag(value: Any, default: bool) -> bool:
    """
    Read a yes/no setting, failing towards the stricter reading.

    Args:
        value: What the configuration holds: a boolean, or text typed into a
            file by hand.
        default: What an unset key means.

    Returns:
        The flag. Text that is neither plainly yes nor plainly no reads as
        yes: the setting asks for more checking, so a typo must not turn it off.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("", "0", "false", "no", "off"):
        return False
    return True


def build_policy(settings: dict[str, Any]) -> AuthPolicy:
    """
    Turn configuration values into the policy, applying what the frameworks ask.

    Args:
        settings: ``profile`` (``security.profile``), ``frameworks``
            (``security.frameworks``) and the ``auth`` section's values,
            flattened as :func:`load_policy` reads them.

    Returns:
        The policy: every limit held at the strictest of the operator's value and
        what each enabled framework asks.
    """
    enabled = resolve.enabled_from(settings.get("frameworks"), settings.get("profile"))
    # The name this policy has always carried: an ENS framework is what "ens-medium" meant.
    profile = (
        PROFILE_ENS_MEDIUM if any(name.startswith("ens-") for name in enabled) else PROFILE_STANDARD
    )
    idle = _positive_int(settings.get("idle_minutes"), STANDARD_IDLE_MINUTES)
    absolute = _positive_int(settings.get("absolute_hours"), STANDARD_ABSOLUTE_HOURS)
    threshold = _positive_int(settings.get("lockout_threshold"), STANDARD_LOCKOUT_THRESHOLD)
    lock_minutes = _positive_int(settings.get("lockout_minutes"), STANDARD_LOCKOUT_MINUTES)
    min_length = _positive_int(settings.get("password_min_length"), STANDARD_PASSWORD_MIN_LENGTH)
    sudo_idle = _clamped_int(
        settings.get("sudo_idle_minutes"), STANDARD_SUDO_IDLE_MINUTES, SUDO_IDLE_RANGE
    )
    sudo_max = _clamped_int(
        settings.get("sudo_max_minutes"), STANDARD_SUDO_MAX_MINUTES, SUDO_MAX_RANGE
    )
    sudo_password = _flag(settings.get("sudo_require_password"), STANDARD.sudo_require_password)
    token_days: int | None = None
    if enabled:
        idle = resolve.apply_floor("session_idle_minutes", enabled, idle)
        absolute = resolve.apply_floor("session_absolute_hours", enabled, absolute)
        threshold = resolve.apply_floor("lockout_threshold", enabled, threshold)
        lock_minutes = resolve.apply_floor("lockout_minutes", enabled, lock_minutes)
        min_length = resolve.apply_floor("password_min_length", enabled, min_length)
        limit = resolve.floor_for("token_max_days", enabled)
        if limit is not None:
            token_days = min(_positive_int(settings.get("token_max_days"), limit), limit)
        sudo_idle = resolve.apply_floor("sudo_idle_minutes", enabled, sudo_idle)
        sudo_max = resolve.apply_floor("sudo_max_minutes", enabled, sudo_max)
        sudo_password = sudo_password or bool(resolve.floor_for("sudo_password", enabled))
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
        sudo_idle_minutes=sudo_idle,
        # The confirmation can never be shorter than the window it is made of.
        sudo_max_minutes=max(sudo_max, sudo_idle),
        sudo_require_password=sudo_password,
        frameworks=tuple(enabled),
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
            "frameworks": config.get("security.frameworks"),
            "idle_minutes": config.get("auth.session.idle_minutes"),
            "absolute_hours": config.get("auth.session.absolute_hours"),
            "lockout_threshold": config.get("auth.lockout.threshold"),
            "lockout_minutes": config.get("auth.lockout.minutes"),
            "password_min_length": config.get("auth.password.min_length"),
            "token_max_days": config.get("auth.tokens.max_days"),
            "notice_text": config.get("auth.notice.text"),
            "login_label": config.get("auth.login_label"),
            "sudo_idle_minutes": config.get("auth.sudo.idle_minutes"),
            "sudo_max_minutes": config.get("auth.sudo.max_minutes"),
            "sudo_require_password": config.get("auth.sudo.require_password"),
        }
    )
