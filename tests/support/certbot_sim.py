# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A certbot that keeps lineages, for tests that follow a certificate from the order to the site.

:class:`noust.core.runner.FakeRunner` answers each command with a canned result, which is
what a test of one argv wants. A test of a *change* - a name taking a certificate of its own,
the site written with it, the lineage it left behind removed - needs the answers to follow from
the commands before them: an order makes the files the site is then written with, a listing
names what was ordered, a deletion takes it away. This is that, for the four verbs Noust uses
(``certificates``, ``certonly``, ``renew``, ``delete``) and nothing else: any other command is
answered as the fake runner would.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from noust.core.runner import CommandResult, FakeRunner


class CertbotSim(FakeRunner):
    """
    A fake runner whose certbot remembers what it ordered.

    Attributes:
        live: certbot's ``live`` directory, where an order makes the lineage's files.
        lineages: Each lineage's names, by lineage name.
        refuse: Lineage name to certbot's complaint: an order or renewal for it fails with it.
        orders: Every ``certonly`` that succeeded, as ``(lineage, names)``.
        renewals: Every lineage a ``renew`` named, in order.
    """

    def __init__(self, live: Path) -> None:
        super().__init__()
        self.live = live
        self.lineages: dict[str, list[str]] = {}
        self.refuse: dict[str, str] = {}
        self.orders: list[tuple[str, tuple[str, ...]]] = []
        self.renewals: list[str] = []

    def have(self, lineage: str, names: Sequence[str]) -> None:
        """Make a lineage exist, with its files, as if it had been ordered earlier."""
        self._write(lineage, list(names))

    def _write(self, lineage: str, names: list[str]) -> None:
        directory = self.live / lineage
        directory.mkdir(parents=True, exist_ok=True)
        for file in ("fullchain.pem", "privkey.pem", "cert.pem", "chain.pem"):
            (directory / file).write_text(f"-----BEGIN CERTIFICATE-----\n{lineage}\n")
        self.lineages[lineage] = names

    def _lookup(
        self,
        argv: Sequence[str],
        user: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = tuple(str(a) for a in argv)
        if args[:1] != ("certbot",) or len(args) < 2:
            return result
        verb = args[1]
        if verb == "plugins":
            return replace(result, stdout="* nginx\n* apache\n")
        if verb == "certificates":
            return replace(result, stdout=self._listing())
        if verb == "certonly":
            return self._certonly(args, result)
        if verb == "renew":
            return self._renew(args, result)
        if verb == "delete":
            return self._delete(args, result)
        return result

    @staticmethod
    def _option(args: tuple[str, ...], flag: str) -> str | None:
        return args[args.index(flag) + 1] if flag in args else None

    def _certonly(self, args: tuple[str, ...], result: CommandResult) -> CommandResult:
        lineage = self._option(args, "--cert-name") or ""
        names = [args[i + 1] for i, arg in enumerate(args) if arg == "-d"]
        if lineage in self.refuse:
            return replace(result, exit_code=1, stderr=self.refuse[lineage])
        if "--dry-run" in args:
            return result
        self._write(lineage, names)
        self.orders.append((lineage, tuple(names)))
        return result

    def _renew(self, args: tuple[str, ...], result: CommandResult) -> CommandResult:
        lineage = self._option(args, "--cert-name")
        if lineage is None:
            return result
        self.renewals.append(lineage)
        if lineage in self.refuse:
            return replace(result, exit_code=1, stderr=self.refuse[lineage])
        return result

    def _delete(self, args: tuple[str, ...], result: CommandResult) -> CommandResult:
        lineage = self._option(args, "--cert-name") or ""
        if lineage in self.refuse:
            return replace(result, exit_code=1, stderr=self.refuse[lineage])
        self.lineages.pop(lineage, None)
        directory = self.live / lineage
        if directory.is_dir():
            for child in directory.iterdir():
                child.unlink()
            directory.rmdir()
        return result

    def _listing(self) -> str:
        text = "Found the following certs:\n" if self.lineages else "No certificates found.\n"
        for lineage, names in self.lineages.items():
            text += (
                f"  Certificate Name: {lineage}\n"
                f"    Domains: {' '.join(names)}\n"
                "    Expiry Date: 2026-11-30 10:00:00+00:00 (VALID: 60 days)\n"
                f"    Certificate Path: {self.live / lineage / 'fullchain.pem'}\n"
                f"    Private Key Path: {self.live / lineage / 'privkey.pem'}\n"
            )
        return text

    def certonly_calls(self) -> list[tuple[str, ...]]:
        """Every ``certbot certonly`` that was run, whether it succeeded or not."""
        return [call for call in self.calls if call[:2] == ("certbot", "certonly")]
