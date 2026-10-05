# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Changing how SSH lets people in, without locking anyone out.

:class:`SshSecurity` is the only code that changes sshd's configuration or an
``authorized_keys`` file for an operator (rule 4: the guard is here, not in
the endpoints and commands that call it). Every sshd change is one of the
named fixes in :data:`SSH_FIXES` - a request can pick a fix, never write a
directive - and goes through the same steps (``privilege-model.md`` §7.2):

1. The effective values before, with ``sshd -T -C``.
2. The guard: for anything that closes a way in, :func:`prove_key_access`
   must find another account proven to open with a key. No proof, no change:
   the operator gets the guided steps instead.
3. The revert is armed (:class:`ChangeLedger`) before anything changes.
4. Noust's ``00-noust.conf`` is written, and ``sshd -t`` must accept the whole
   configuration; if not, the file goes back and sshd's output is shown.
5. ``reload``, never ``restart``: open sessions stay.
6. ``sshd -T`` again: the value must have changed. When another file wins
   (the main file sets it before its ``Include``), the change is undone at
   once and the winning line is named.
7. Confirm or revert: the change undoes itself after
   :data:`~noust.managers.server.security_pending.CONFIRM_WINDOW` seconds
   unless a *new* SSH login is seen and the operator confirms.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core.exceptions import SecurityError
from noust.core.fs import is_rehearsal
from noust.fleet.authorize import AuthorizedKeys, _locked_for_write
from noust.managers.server.security_access import (
    AccessGuardError,
    AccessProof,
    missing_proof_steps,
    prove_key_access,
)
from noust.managers.server.security_keys import (
    AuthorizedKey,
    parse_file,
    parse_new_key,
    strict_mode_problems,
)
from noust.managers.server.security_pending import (
    CONFIRM_WINDOW,
    ChangeLedger,
    FileRestore,
    PendingChange,
    audit,
    new_change_id,
)
from noust.managers.server.security_probe import AccountKeys, SecurityProbe
from noust.managers.server.security_proof import find_proof
from noust.managers.server.security_sshd import (
    DIRECTIVES,
    DROPIN,
    DROPIN_MODE,
    SshdDropIn,
    SshdEffective,
    include_present,
    read_effective,
    read_unit,
    reload,
    reload_argv,
    test_configuration,
    winning_source,
)

#: Values sshd treats as the same (``without-password`` is the old spelling).
_SYNONYMS = {"without-password": "prohibit-password"}


@dataclass(frozen=True)
class SshFix:
    """
    One named change to sshd.

    Attributes:
        name: What a request asks for: ``disable-passwords``...
        title: What it does, in a sentence.
        check_id: The hardening check it fixes.
        guard: ``key_access`` (another way in must be proven),
            ``key_access_non_root`` (a way in that is not root, with sudo) or
            ``none``.
        values: Keyword to value; :meth:`settings` resolves the placeholders.
    """

    name: str
    title: str
    check_id: str
    guard: str
    values: tuple[tuple[str, str], ...]

    def settings(self, effective: SshdEffective) -> dict[str, str]:
        """
        The directives this fix writes, for this sshd.

        Args:
            effective: sshd's configuration now.

        Returns:
            Lower-case keyword to value.
        """
        resolved: dict[str, str] = {}
        for keyword, value in self.values:
            resolved[effective.kbd_keyword if keyword == "kbd" else keyword] = value
        return resolved


#: Every change Noust makes to sshd. Algorithms are left to OpenSSH's defaults
#: (changing them breaks clients) and AllowTcpForwarding is never touched (a
#: central's tunnel needs it, whatever CIS says).
SSH_FIXES: dict[str, SshFix] = {
    fix.name: fix
    for fix in (
        SshFix(
            "disable-passwords",
            "Turn off SSH password logins",
            "ssh.password_auth",
            "key_access",
            # Both: with UsePAM, keyboard-interactive still takes a password
            # after PasswordAuthentication is off.
            (("passwordauthentication", "no"), ("kbd", "no")),
        ),
        SshFix(
            "root-prohibit-password",
            "Let root log in over SSH with a key only",
            "ssh.root_password",
            "key_access",
            (("permitrootlogin", "prohibit-password"),),
        ),
        SshFix(
            "root-no",
            "Refuse root logins over SSH",
            "ssh.root_login",
            "key_access_non_root",
            (("permitrootlogin", "no"),),
        ),
        SshFix(
            "no-empty-passwords",
            "Refuse logins with an empty password",
            "ssh.empty_passwords",
            "none",
            (("permitemptypasswords", "no"),),
        ),
        SshFix(
            "sensible-defaults",
            "Apply sensible SSH limits",
            "ssh.defaults",
            "none",
            (
                ("maxauthtries", "4"),
                ("logingracetime", "30"),
                ("x11forwarding", "no"),
                ("clientaliveinterval", "300"),
                ("clientalivecountmax", "2"),
            ),
        ),
        SshFix(
            "verbose-logging",
            "Log the key fingerprint of every SSH login",
            "ssh.loglevel",
            "none",
            (("loglevel", "VERBOSE"),),
        ),
    )
}


def _same(expected: str, actual: str) -> bool:
    """
    Compare a value Noust wrote with the one ``sshd -T`` prints.

    Args:
        expected: What was written.
        actual: What sshd uses.

    Returns:
        True when sshd uses it.
    """
    left, right = expected.lower(), actual.lower()
    return _SYNONYMS.get(left, left) == _SYNONYMS.get(right, right)


@dataclass
class FixPlan:
    """
    What a fix would change, and whether it may.

    Attributes:
        fix: The fix.
        changes: Keyword to the value it would set.
        before: Keyword to the value sshd uses now.
        proof: The access proof, when the fix needs one.
        blockers: Why it may not be applied now; empty when it may.
        guidance: The steps that make it possible, or the manual way.
    """

    fix: SshFix
    changes: dict[str, str]
    before: dict[str, str]
    proof: AccessProof | None = None
    blockers: list[str] = field(default_factory=list)
    guidance: list[str] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        """Whether sshd does not already use every value."""
        return any(
            not _same(value, self.before.get(keyword, ""))
            for keyword, value in self.changes.items()
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the plan for the API and ``--json``.

        Returns:
            The fix, before and after, the proof and what blocks it.
        """
        return {
            "fix": self.fix.name,
            "title": self.fix.title,
            "check_id": self.fix.check_id,
            "changes": [
                {
                    "directive": DIRECTIVES.get(keyword, keyword),
                    "before": self.before.get(keyword, ""),
                    "after": value,
                }
                for keyword, value in self.changes.items()
            ],
            "needed": self.needed,
            "allowed": not self.blockers,
            "blockers": list(self.blockers),
            "guidance": list(self.guidance),
            "proof": self.proof.summary() if self.proof else None,
            "evidence": [item.describe() for item in self.proof.evidence] if self.proof else [],
        }


def manual_steps(changes: dict[str, str]) -> list[str]:
    """
    The by-hand way of applying sshd directives, for a guided fix.

    Args:
        changes: Keyword to value.

    Returns:
        The steps, commands verbatim.
    """
    lines = " ".join(f"{DIRECTIVES.get(k, k)} {v}" for k, v in changes.items())
    return [
        f"Add these lines at the top of /etc/ssh/sshd_config.d/00-noust.conf: {lines}",
        "Check the configuration: sshd -t",
        "Reload sshd, which keeps open sessions: systemctl reload ssh (sshd on RHEL and SUSE)",
        "Keep this session open and log in from a new terminal before closing it.",
    ]


def plan_fix(probe: SecurityProbe, name: str) -> FixPlan:
    """
    Work out what an sshd fix would change and whether its guard holds now.

    Shared by :meth:`SshSecurity.apply` and the hardening checks, which offer a
    fix as automatic only when this finds nothing blocking it.

    Args:
        probe: This pass's look at the machine.
        name: One of :data:`SSH_FIXES`.

    Returns:
        The plan: before and after, the proof, what blocks it and the steps.

    Raises:
        SecurityError: There is no such fix.
        SshdUnavailableError: sshd cannot report its configuration.
    """
    fix = SSH_FIXES.get(name)
    if fix is None:
        raise SecurityError(
            f"There is no SSH fix named {name!r}",
            details=f"Use one of: {', '.join(SSH_FIXES)}.",
        )
    effective = probe.effective("root")
    changes = fix.settings(effective)
    plan = FixPlan(fix, changes, {k: effective.first(k) for k in changes})
    if fix.guard in ("key_access", "key_access_non_root"):
        non_root = fix.guard == "key_access_non_root"
        plan.proof = prove_key_access(probe, exclude_root=non_root)
        if not plan.proof.proved:
            plan.blockers.append(plan.proof.summary())
            plan.guidance.extend(missing_proof_steps())
    if fix.name == "root-no":
        root_keys = probe.keys_of("root")
        if root_keys and root_keys.keys("central"):
            plan.blockers.append(
                "A Noust central's tunnel logs in as root on this server, and would be "
                "refused. Move it to the tunnel account first: 'noust node migrate-tunnel' "
                "on the central."
            )
    if not include_present(probe.host):
        plan.blockers.append(
            "/etc/ssh/sshd_config does not include /etc/ssh/sshd_config.d/*.conf, where "
            "Noust keeps its settings."
        )
        plan.guidance.append(
            "Add 'Include /etc/ssh/sshd_config.d/*.conf' as the first line of "
            "/etc/ssh/sshd_config, run 'sshd -t', then apply again."
        )
    if plan.blockers:
        plan.guidance.extend(manual_steps(changes))
    return plan


class SshSecurity:
    """
    Every change to SSH access, guarded.

    Args:
        probe: This pass's look at the machine.
        ledger: Where changes wait for confirmation.
        actor: Who is asking, for the record.
        on_output: Receives every command and its output, verbatim.
    """

    def __init__(
        self,
        probe: SecurityProbe,
        ledger: ChangeLedger,
        *,
        actor: str,
        on_output: Callable[[str], None] | None = None,
    ) -> None:
        self.probe = probe
        self.ledger = ledger
        self.actor = actor
        self.on_output = on_output

    def _say(self, line: str) -> None:
        if self.on_output:
            self.on_output(line)

    # sshd -------------------------------------------------------------------

    def plan(self, name: str) -> FixPlan:
        """
        Work out what a fix would change and whether it may be applied.

        Args:
            name: One of :data:`SSH_FIXES`.

        Returns:
            The plan.
        """
        return plan_fix(self.probe, name)

    def apply(self, name: str) -> PendingChange:
        """
        Apply a fix, pending confirmation, and record the attempt either way.

        Args:
            name: One of :data:`SSH_FIXES`.

        Returns:
            The change, waiting for :meth:`confirm`.
        """
        try:
            change = self._apply(name)
        except AccessGuardError as exc:
            audit(
                "server.ssh", "sshd", outcome="denied", action="fix", fix=name, reason=exc.message
            )
            raise
        except SecurityError as exc:
            audit(
                "server.ssh", "sshd", outcome="failure", action="fix", fix=name, reason=exc.message
            )
            raise
        audit(
            "server.ssh",
            "sshd",
            action="fix",
            fix=name,
            change=change.id,
            before=change.before,
            after=change.after,
        )
        return change

    def _apply(self, name: str) -> PendingChange:
        """
        Apply a fix, pending confirmation.

        Args:
            name: One of :data:`SSH_FIXES`.

        Returns:
            The change, waiting for :meth:`confirm`; it undoes itself after
            :data:`CONFIRM_WINDOW` seconds otherwise.

        Raises:
            AccessGuardError: The guard refused it; ``details`` has the steps.
            SecurityError: sshd refused the configuration, it did not take
                effect, another change is pending, or the revert could not be armed.
        """
        plan = self.plan(name)
        if plan.blockers:
            raise AccessGuardError(
                f"Not applied: {plan.fix.title.lower()}",
                details="\n".join([*plan.blockers, *plan.guidance]),
            )
        if not plan.needed:
            raise SecurityError(
                f"Nothing to do: sshd already uses {', '.join(DIRECTIVES[k] + ' ' + v for k, v in plan.changes.items())}"
            )
        unit = read_unit(self.probe.runner)
        reload_command = reload_argv(unit)
        dropin = SshdDropIn(self.probe.host, self.probe.fs)
        with self.ledger.locked():
            waiting = self.ledger.pending()
            if waiting:
                raise SecurityError(
                    f"Another change is waiting for confirmation: {waiting[0].title} ({waiting[0].id})",
                    details="Confirm it or revert it first: one change at a time can be undone safely.",
                )
            previous, text = dropin.merged(plan.changes)
            now = self.probe.now()
            change = PendingChange(
                id=new_change_id(),
                kind="sshd",
                title=plan.fix.title,
                actor=self.actor,
                applied_at=now,
                expires_at=now + CONFIRM_WINDOW,
                files=[FileRestore(DROPIN, previous, DROPIN_MODE)],
                validate=["sshd", "-t"],
                # Nothing to reload until sshd has accepted the file; the
                # reload joins the undo once it is about to happen.
                undo=[],
                before=dict(plan.before),
                proof="operator",
            )
            # Armed before the file changes: there is no moment in which the
            # configuration is new and nothing would put it back.
            self.ledger.open(change)
            self._say(f"Armed {change.unit}: it undoes this change in {CONFIRM_WINDOW} s")
            self._say(f"Writing {DROPIN}")
            dropin.write(text)
            test = test_configuration(self.probe.runner)
            self._say("$ sshd -t")
            for line in (test.stdout + test.stderr).splitlines():
                self._say(line)
            if not test.success:
                self.ledger.revert(change.id, by=self.actor, on_output=self.on_output)
                raise SecurityError(
                    "sshd rejected the new configuration, so nothing was changed",
                    details="Its own words are below.",
                    output=(test.stderr or test.stdout).strip() or None,
                )
            if reload_command:
                change.undo = [reload_command]
                self.ledger.save(change)
            result = reload(unit, self.probe.runner, self.on_output)
            if result is not None and not result.success:
                self.ledger.revert(change.id, by=self.actor, on_output=self.on_output)
                raise SecurityError(
                    "sshd could not be reloaded, so the change was undone",
                    output=(result.stderr or result.stdout).strip() or None,
                )
            if is_rehearsal():
                return change
            self._verify(change, plan)
            return change

    def _verify(self, change: PendingChange, plan: FixPlan) -> None:
        """
        Check that sshd now uses every value, or undo the change.

        Args:
            change: The pending change.
            plan: What it was meant to set.

        Raises:
            SecurityError: A value did not take effect; the change was undone.
        """
        after = read_effective(self.probe.runner, user="root")
        change.after = {keyword: after.first(keyword) for keyword in plan.changes}
        missed = [
            keyword
            for keyword, value in plan.changes.items()
            if not _same(value, change.after.get(keyword, ""))
        ]
        if not missed:
            self.ledger.save(change)
            return
        sources = [
            f"{DIRECTIVES[k]} is still {change.after.get(k) or 'unset'}: "
            + (winning_source(k, self.probe.host) or "no file sets it, so another default wins")
            for k in missed
        ]
        self.ledger.revert(change.id, by=self.actor, on_output=self.on_output)
        raise SecurityError(
            "The change did not take effect, so it was undone",
            details="sshd uses the first value it reads. Move or remove the line that wins, "
            "then apply again:\n" + "\n".join(sources),
        )

    # Confirm and revert -------------------------------------------------------

    def confirm(self, change_id: str) -> PendingChange:
        """
        Keep a change, once a new SSH login shows it did not lock anyone out.

        Args:
            change_id: The pending change.

        Returns:
            The confirmed change.

        Raises:
            AccessGuardError: No new login was seen since the change.
            SecurityError: It is not pending any more.
        """
        change = self.ledger.load(change_id)
        if change.status != "pending":
            raise SecurityError(f"Change {change_id} is already {change.status}")
        found = find_proof(self.probe, change)
        deadline = datetime.fromtimestamp(change.expires_at, tz=timezone.utc).strftime(
            "%H:%M:%S UTC"
        )
        if not found.readable:
            raise AccessGuardError(
                "Noust cannot read sshd's login history, so it cannot see a new session",
                details=f"{found.error}. The change undoes itself at {deadline}; to keep it, "
                "make it by hand while a second session stays open.",
            )
        if found.login is None:
            raise AccessGuardError(
                "No new SSH login since the change",
                details="Keep this session open, open a NEW SSH session to this server and "
                "confirm again once it has logged in. If it cannot log in, do nothing: the "
                f"change undoes itself at {deadline}.",
            )
        proof = f"New login after the change: {found.login.line}"
        return self.ledger.confirm(change_id, proof=proof, by=self.actor)

    def revert(self, change_id: str) -> PendingChange:
        """
        Undo a pending change now.

        Args:
            change_id: The change.

        Returns:
            The change, reverted.
        """
        return self.ledger.revert(change_id, by=self.actor, on_output=self.on_output)

    # Keys -------------------------------------------------------------------

    def add_key(self, user: str, public_key: str) -> dict[str, Any]:
        """
        Let a key log in as an administrator.

        Args:
            user: root, or an account that can become root.
            public_key: The ``.pub`` line.

        Returns:
            The file, the fingerprint, whether it was added (False when it was
            already there) and what would make StrictModes ignore it.

        Raises:
            SecurityError: The key is invalid, or the account is not one whose
                keys Noust manages.
        """
        key = parse_new_key(public_key)
        entry = self._admin(user)
        if not entry.files:
            raise SecurityError(
                f"sshd reads no authorized_keys file for {user}",
                details=entry.login_refusal or "Set AuthorizedKeysFile in sshd_config.",
            )
        target = entry.files[0].path
        present = [k for f in entry.files for k in f.keys if k.blob == key.blob]
        result: dict[str, Any] = {
            "user": user,
            "file": target,
            "fingerprint": key.fingerprint,
            "added": False,
        }
        if not present:
            editor = self._editor(entry.account.uid, entry.account.gid, entry.account.home, target)
            with _locked_for_write(editor.path):
                existing = editor.read() or ""
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                editor.write(existing + key.line + "\n")
            self._say(f"Added {key.fingerprint} to {target}")
            result["added"] = True
            audit(
                "server.ssh",
                f"user:{user}",
                action="key.add",
                fingerprint=key.fingerprint,
                file=target,
            )
        account = entry.account
        result["strict_mode_problems"] = strict_mode_problems(self.probe.host, account, target)
        self.probe.invalidate()
        return result

    def check_removal(
        self, user: str, fingerprint: str, *, force: bool = False
    ) -> tuple[AccountKeys, list[tuple[str, AuthorizedKey]], list[str]]:
        """
        Work out removing a key, and refuse it when the guard says so.

        Refused, unless ``force``, when the key is a central's tunnel key, when
        a session open now logged in with it, or when it is the last operator
        key of every administrator while passwords are off.

        Args:
            user: The account.
            fingerprint: ``SHA256:...``.
            force: Accept what the guard says instead of refusing.

        Returns:
            The account, the lines holding the key (file and key), and what
            the guard said (empty when nothing).

        Raises:
            AccessGuardError: The guard refused it.
            SecurityError: No such key.
        """
        entry = self._admin(user)
        matches = [
            (file.path, key)
            for file in entry.files
            for key in file.keys
            if key.fingerprint == fingerprint
        ]
        if not matches:
            raise SecurityError(
                f"{user} has no key {fingerprint}",
                details="List the keys with 'noust server security ssh keys'.",
            )
        key = matches[0][1]
        blockers: list[str] = []
        if key.kind == "central":
            blockers.append(
                "It is a Noust central's tunnel key: removing it cuts the central off this "
                "server. Use 'noust fleet deauthorize --name <name>' here, or 'noust node "
                "remove' on the central."
            )
        in_use = [
            session
            for session in self.probe.sessions()
            if session.login
            and session.login.user == user
            and session.login.fingerprint == fingerprint
        ]
        for session in in_use:
            blockers.append(
                f"The SSH session open now from {session.connection.peer_address} logged in with "
                "this key; it stays open, but could not log in again."
            )
        if key.kind == "operator":
            others = sum(
                len([k for k in each.usable_operator_keys() if k.fingerprint != fingerprint])
                for each in self.probe.account_keys()
                if each.login_allowed
            )
            if others == 0 and not self.probe.effective().passwords_accepted:
                blockers.append(
                    "It is the last operator key of every administrator and SSH passwords are "
                    "off: nobody could log in over SSH."
                )
        if blockers and not force:
            audit(
                "server.ssh",
                f"user:{user}",
                outcome="denied",
                action="key.remove",
                fingerprint=fingerprint,
                reason=" ".join(blockers),
            )
            raise AccessGuardError(
                f"Refusing to remove {fingerprint} from {user}",
                details="\n".join([*blockers, "Override only if you are sure (--force)."]),
            )
        return entry, matches, blockers

    def remove_key(self, user: str, fingerprint: str, *, force: bool = False) -> dict[str, Any]:
        """
        Stop a key from logging in as an administrator, guarded (:meth:`check_removal`).

        Args:
            user: The account.
            fingerprint: ``SHA256:...``.
            force: Remove it despite the guard.

        Returns:
            What was removed, and what the guard said when it was overridden.
        """
        entry, matches, blockers = self.check_removal(user, fingerprint, force=force)
        removed: list[str] = []
        for path, _key in matches:
            editor = self._editor(entry.account.uid, entry.account.gid, entry.account.home, path)
            with _locked_for_write(editor.path):
                existing = editor.read() or ""
                lines = existing.splitlines()
                kept = []
                for line in lines:
                    parsed = parse_file(line)
                    if parsed and parsed[0].fingerprint == fingerprint:
                        removed.append(line)
                    else:
                        kept.append(line)
                if len(kept) != len(lines):
                    editor.write("\n".join(kept) + "\n" if kept else "")
            self._say(f"Removed {fingerprint} from {path}")
        self.probe.invalidate()
        audit(
            "server.ssh",
            f"user:{user}",
            action="key.remove",
            fingerprint=fingerprint,
            overridden=blockers if force else None,
        )
        return {
            "user": user,
            "fingerprint": fingerprint,
            "removed": removed,
            "overridden": blockers if force else [],
        }

    def _admin(self, user: str) -> AccountKeys:
        """
        Find an administrator's keys.

        Args:
            user: The account.

        Returns:
            Its :class:`~noust.managers.server.security_probe.AccountKeys`.

        Raises:
            SecurityError: It is not root nor an account that can become root.
        """
        entry = self.probe.keys_of(user)
        if entry is None:
            names = ", ".join(each.account.name for each in self.probe.account_keys())
            raise SecurityError(
                f"Noust manages the keys of root and of the accounts that can become root; "
                f"{user} is not one of them",
                details=f"Accounts: {names or 'none found'}.",
            )
        return entry

    def _editor(self, uid: int, gid: int, home: str, path: str) -> AuthorizedKeys:
        """
        Build the editor of one key file.

        Args:
            uid: The account's user id.
            gid: Its group id.
            home: Its home.
            path: The file on the server.

        Returns:
            The editor; the file belongs to the account when it is in its home.
        """
        local = self.probe.host.at(path)
        owned = Path(home) in Path(path).parents
        return AuthorizedKeys(
            local, (uid, gid) if owned else None, fs=self.probe.fs, runner=self.probe.runner
        )
