# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Proof that somebody can still get in, before Noust closes a way in.

Turning SSH passwords off or refusing root is safe only when another way in
is known to work. Noust does not take the operator's word for it, and it
cannot try the key itself (the private half is on the operator's laptop, which
is the point). What it can do is what no other panel reviewed for 3.1 does:
require both halves of this proof, and refuse the fix when either is missing.

- **Static**: an account that can become root, that sshd lets in with a key
  (``PermitRootLogin``, ``AllowUsers``/``AllowGroups``, ``PubkeyAuthentication``,
  a shell, not locked without PAM), has an operator key in a file
  ``StrictModes`` would accept, and - for ``PermitRootLogin no`` - is not root
  and can actually use sudo (``NOPASSWD`` or a password sudo can ask for).
- **Dynamic**: sshd's own log shows that key logging that account in within
  the last 30 days, fingerprint and all. A central's tunnel key never counts:
  it forwards one port and runs nothing.

When the proof fails the fix is not offered: the operator gets the exact steps
- add a key, log in with it once, apply again - and the manual commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from noust.core.exceptions import SecurityError
from noust.managers.server.security_logins import EVIDENCE_DAYS
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.server.security_sshd import SshdUnavailableError


class AccessGuardError(SecurityError):
    """
    A change to how the server is reached was refused before it was made.

    Either it would cut off a way in that something depends on (the SSH port,
    a session open now, a central's tunnel), or the proof that another way in
    works is missing. ``details`` carries what to do instead: the guided fix.
    """


@dataclass(frozen=True)
class KeyEvidence:
    """
    A login that proves a key opens an account.

    Attributes:
        user: The account.
        fingerprint: The key's ``SHA256:`` fingerprint.
        key_type: ``ED25519``, ``RSA``...
        at: When, epoch seconds.
        source: From where.
        line: sshd's line, verbatim.
    """

    user: str
    fingerprint: str
    key_type: str
    at: float
    source: str
    line: str

    def describe(self) -> str:
        """
        Say what the evidence is, for the operator.

        Returns:
            Such as ``root logged in with ED25519 key SHA256:... from 1.2.3.4 on 2026-09-27 10:02 UTC``.
        """
        when = datetime.fromtimestamp(self.at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return (
            f"{self.user} logged in with {self.key_type} key {self.fingerprint} "
            f"from {self.source} on {when}"
        )


@dataclass(frozen=True)
class AccessProof:
    """
    Whether another way in is proven, and why or why not.

    Attributes:
        proved: At least one account passed both halves.
        evidence: Every login that proves an account, newest first.
        problems: For every account that did not pass, why.
    """

    proved: bool
    evidence: tuple[KeyEvidence, ...] = ()
    problems: tuple[str, ...] = ()

    def summary(self) -> str:
        """
        One paragraph for the operator.

        Returns:
            The best evidence, or every reason it is missing.
        """
        if self.proved:
            return "Proven: " + self.evidence[0].describe() + "."
        return "No other way in is proven. " + " ".join(self.problems)


def prove_key_access(
    probe: SecurityProbe,
    *,
    exclude_root: bool = False,
    require_sudo: bool = True,
) -> AccessProof:
    """
    Look for an account that is proven to open with a key.

    Args:
        probe: This pass's look at the machine.
        exclude_root: Only accounts other than root count (for
            ``PermitRootLogin no``).
        require_sudo: An account other than root must be able to use sudo
            from a key session, or it is a way in but not a way to root.

    Returns:
        The proof, with every reason an account did not count.
    """
    problems: list[str] = []
    evidence: list[KeyEvidence] = []
    try:
        probe.effective()
    except SshdUnavailableError as exc:
        return AccessProof(False, problems=(f"{exc.message}: {exc.output or ''}".strip(),))
    history = probe.logins()
    if not history.readable:
        return AccessProof(
            False,
            problems=(f"sshd's login history cannot be read ({history.error}).",),
        )
    centrals = probe.central_fingerprints()
    candidates = [
        entry for entry in probe.account_keys() if not (exclude_root and entry.account.uid == 0)
    ]
    if not candidates:
        problems.append(
            "No account other than root can become root with sudo."
            if exclude_root
            else "No account that can become root was found."
        )
    for entry in candidates:
        name = entry.account.name
        if not entry.login_allowed:
            problems.append(f"sshd does not let {name} in with a key: {entry.login_refusal}.")
            continue
        if entry.account.uid != 0 and require_sudo and not entry.sudo_usable:
            reason = (
                "no sudoers rule grants it root"
                if not entry.sudo.granted
                else "sudo would ask for a password it does not have"
            )
            problems.append(f"{name} can log in but cannot use sudo: {reason}.")
            continue
        keys = [key for key in entry.usable_operator_keys() if key.fingerprint not in centrals]
        if not keys:
            unusable = [problem for file in entry.files for problem in file.problems]
            detail = f" ({'; '.join(unusable)})" if unusable else ""
            problems.append(f"{name} has no operator key sshd would accept{detail}.")
            continue
        used = [
            login for key in keys if (login := history.last_use(name, key.fingerprint)) is not None
        ]
        if not used:
            problems.append(
                f"None of the {len(keys)} key(s) of {name} logged in during the last "
                f"{EVIDENCE_DAYS} days."
            )
            continue
        evidence.extend(
            KeyEvidence(
                user=name,
                fingerprint=login.fingerprint or "",
                key_type=login.key_type or "",
                at=login.at,
                source=login.source,
                line=login.line,
            )
            for login in used
        )
    evidence.sort(key=lambda item: item.at, reverse=True)
    return AccessProof(bool(evidence), tuple(evidence), tuple(problems))


def missing_proof_steps(user_hint: str = "<you>") -> list[str]:
    """
    What to do when no way in is proven.

    Args:
        user_hint: The account to name in the commands.

    Returns:
        The steps, each a sentence or a command.
    """
    return [
        "On your computer, create a key if you have none: ssh-keygen -t ed25519",
        f"Add its public half here: noust server security ssh add-key --user {user_hint} "
        "--file ~/.ssh/id_ed25519.pub (or Server, Security, SSH in the console).",
        f"Log in once with it, from a new terminal: ssh -i ~/.ssh/id_ed25519 {user_hint}@<this server>",
        "Apply the fix again: the login you just made is the proof Noust looks for.",
    ]
