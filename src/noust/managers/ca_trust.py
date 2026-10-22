# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How to trust the internal authority on each kind of device.

A certificate the authority signed is only as good as the devices that trust its root, and
installing a root is different on every system. This module is the one place that says how, with
the commands filled in with the file's name and the fingerprint to compare before anything is
trusted: the command line, the API and the console all print it.

Noust changes no device but this one. A step for another device is text for a person to follow on
it; the root is public, the key never leaves the server, and the fingerprint is how the person
knows the file they carry is the one this server made. On the server itself (programs there reach
each other by the private names: curl, git, a health probe, an application calling another) the
root can be put in the system store, through the runner, and taken out again
(:class:`ServerTrust`).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.core.exceptions import CertificateError, ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner

__all__ = [
    "SERVER_STORES",
    "TARGETS",
    "ServerStore",
    "ServerTrust",
    "TrustGuide",
    "guide",
    "guides",
]

#: What the root is called in the system store.
ANCHOR_NAME = "noust-internal-ca.crt"

#: Seconds the tool that rebuilds the bundle may take.
_UPDATE_TIMEOUT = 120


@dataclass(frozen=True)
class TrustGuide:
    """
    The steps to trust the root on one kind of device.

    Attributes:
        id: ``debian``, ``rhel``, ``macos``...
        title: What the person calls it.
        file: Which form of the root it wants: ``pem`` (``ca.crt``) or ``der`` (``ca.cer``).
        steps: What to do, in order; a step that is a command starts with ``$ ``.
        note: What is worth knowing (an application with its own store, a restart).
    """

    id: str
    title: str
    file: str
    steps: tuple[str, ...]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "id": self.id,
            "title": self.title,
            "file": self.file,
            "steps": list(self.steps),
            "note": self.note,
        }


def _guides(fingerprint: str, host: str) -> tuple[TrustGuide, ...]:
    """
    Args:
        fingerprint: The root's SHA-256, colon separated.
        host: What to call the file: the server's name.

    Returns:
        The guides, with the file names and the fingerprint written in.
    """
    base = f"noust-{host}"
    compare = f"Compare the fingerprint it prints with {fingerprint}"
    return (
        TrustGuide(
            "debian",
            "Debian, Ubuntu and their derivatives",
            "pem",
            (
                f"$ sudo cp ca.crt /usr/local/share/ca-certificates/{base}.crt",
                "$ sudo update-ca-certificates",
                f"$ openssl x509 -noout -fingerprint -sha256 -in /usr/local/share/ca-certificates/{base}.crt",
                compare,
            ),
            "curl, wget, git and most programs read this store. Firefox, Java and Node have their own.",
        ),
        TrustGuide(
            "rhel",
            "RHEL, Rocky, AlmaLinux, Fedora and openSUSE",
            "pem",
            (
                f"$ sudo cp ca.crt /etc/pki/ca-trust/source/anchors/{base}.crt",
                "$ sudo update-ca-trust",
                f"$ openssl x509 -noout -fingerprint -sha256 -in /etc/pki/ca-trust/source/anchors/{base}.crt",
                compare,
            ),
            "openSUSE keeps the folder at /etc/pki/trust/anchors/ and runs update-ca-certificates.",
        ),
        TrustGuide(
            "alpine",
            "Alpine Linux",
            "pem",
            (
                "$ apk add ca-certificates",
                f"$ cp ca.crt /usr/local/share/ca-certificates/{base}.crt",
                "$ update-ca-certificates",
                compare,
            ),
        ),
        TrustGuide(
            "arch",
            "Arch Linux",
            "pem",
            ("$ sudo trust anchor --store ca.crt", compare),
        ),
        TrustGuide(
            "macos",
            "macOS",
            "pem",
            (
                "$ sudo security add-trusted-cert -d -r trustRoot "
                "-k /Library/Keychains/System.keychain ca.crt",
                "Or double-click ca.crt: Keychain Access adds it to the System keychain; open it, "
                "expand Trust and set 'When using this certificate' to Always Trust.",
                compare,
            ),
            "Safari, Chrome and every app that uses the system keychain follow. Firefox does not.",
        ),
        TrustGuide(
            "windows",
            "Windows",
            "der",
            (
                "PS> Import-Certificate -FilePath .\\ca.cer -CertStoreLocation Cert:\\LocalMachine\\Root",
                'Or, from an administrator prompt: certutil -addstore -f "ROOT" ca.cer',
                f"Check it: certutil -hashfile ca.cer SHA256 (compare with {fingerprint}, "
                "without the colons).",
            ),
            "Edge and Chrome follow. Firefox does not unless it is told to "
            "(security.enterprise_roots.enabled).",
        ),
        TrustGuide(
            "ios",
            "iPhone and iPad",
            "pem",
            (
                "Send ca.crt to the device (AirDrop, mail, a link) and open it: 'Profile "
                "Downloaded'.",
                "Settings, General, VPN & Device Management: install the profile.",
                "Settings, General, About, Certificate Trust Settings: turn on full trust for it.",
                compare,
            ),
            "The last step is easy to miss: an installed profile is not trusted until then.",
        ),
        TrustGuide(
            "android",
            "Android",
            "der",
            (
                "Send ca.cer to the device and open Settings, Security (or Security & privacy), "
                "Encryption & credentials, Install a certificate, CA certificate.",
                compare,
            ),
            "From Android 7 an app trusts the user's authorities only if it opts in; Chrome "
            "does, many apps do not.",
        ),
        TrustGuide(
            "firefox",
            "Firefox on any system",
            "pem",
            (
                "Settings, Privacy & Security, Certificates, View Certificates, Authorities, "
                "Import, ca.crt, and tick 'Trust this CA to identify websites'.",
                "Or set security.enterprise_roots.enabled to true in about:config to follow the "
                "system store.",
            ),
        ),
        TrustGuide(
            "java",
            "Java",
            "pem",
            (
                f"$ sudo keytool -importcert -trustcacerts -alias {base} -file ca.crt -cacerts "
                "-storepass changeit -noprompt",
                compare,
            ),
            "'changeit' is the default password of Java's own store.",
        ),
        TrustGuide(
            "node",
            "Node.js",
            "pem",
            ("$ export NODE_EXTRA_CA_CERTS=/path/to/ca.crt", "then start the program."),
            "Node reads the variable once, at start.",
        ),
        TrustGuide(
            "curl",
            "One command, nothing installed",
            "pem",
            ("$ curl --cacert ca.crt https://wiki.home.arpa/", compare),
        ),
    )


#: The ids of the guides.
TARGETS: tuple[str, ...] = tuple(item.id for item in _guides("", ""))


def guides(fingerprint: str, host: str) -> list[TrustGuide]:
    """
    Args:
        fingerprint: The root's SHA-256.
        host: This server's name, which names the file on the device.

    Returns:
        A guide per kind of device.
    """
    return list(_guides(fingerprint, host))


def guide(target: str, fingerprint: str, host: str) -> TrustGuide:
    """
    Args:
        target: One of :data:`TARGETS`.
        fingerprint: The root's SHA-256.
        host: This server's name.

    Returns:
        That device's guide.

    Raises:
        ValidationError: There is no guide for it.
    """
    for item in _guides(fingerprint, host):
        if item.id == target:
            return item
    raise ValidationError(
        f"There is no guide for {target!r}",
        details="They are: " + ", ".join(TARGETS) + ".",
        field="target",
    )


# -- this server -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerStore:
    """
    A system's store of authorities, and how to rebuild it.

    Attributes:
        id: ``rhel``, ``suse``, ``debian`` or ``arch``.
        title: What the system is called.
        directory: Where a certificate to trust is put.
        tool: The program that rebuilds the bundle; the store is this system's when it is there.
        install: Its arguments after a certificate was put in.
        uninstall: Its arguments after one was taken out.
    """

    id: str
    title: str
    directory: Path
    tool: str
    install: tuple[str, ...] = ()
    uninstall: tuple[str, ...] = ()


#: The systems Noust installs on, the most specific first: openSUSE has the Debian tool and its
#: own folder, so its folder decides.
SERVER_STORES: tuple[ServerStore, ...] = (
    ServerStore(
        "rhel",
        "RHEL, Rocky, AlmaLinux and Fedora",
        Path("/etc/pki/ca-trust/source/anchors"),
        "update-ca-trust",
    ),
    ServerStore(
        "suse",
        "openSUSE",
        Path("/etc/pki/trust/anchors"),
        "update-ca-certificates",
        (),
        ("--fresh",),
    ),
    ServerStore(
        "debian",
        "Debian, Ubuntu and Alpine",
        Path("/usr/local/share/ca-certificates"),
        "update-ca-certificates",
        (),
        ("--fresh",),
    ),
)


class ServerTrust:
    """
    Trust the internal authority on this server, and stop trusting it.

    Args:
        runner: What the rebuild of the bundle runs through; the process-wide runner by default.
        fs: What the certificate is written through; the process-wide filesystem by default.
        stores: The systems to recognise; :data:`SERVER_STORES` by default.
        which: Finds a program (tests give a table).
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        stores: Sequence[ServerStore] | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._runner = runner
        self._fs = fs
        self._stores = tuple(stores) if stores is not None else None
        self._which = which

    @property
    def runner(self) -> CommandRunner:
        """What the bundle is rebuilt through."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """What the certificate is written through."""
        return self._fs or get_fs()

    def store(self) -> ServerStore | None:
        """
        Returns:
            This system's store, or None when it has none Noust knows (a system without the tool
            that rebuilds the bundle).
        """
        for candidate in self._stores if self._stores is not None else SERVER_STORES:
            if candidate.directory.is_dir() and self._which(candidate.tool):
                return candidate
        return None

    def installed(self, root_pem: str) -> bool:
        """
        Args:
            root_pem: The root certificate.

        Returns:
            Whether this server's store holds exactly this root.
        """
        found = self.store()
        if found is None:
            return False
        path = found.directory / ANCHOR_NAME
        try:
            return path.read_text(encoding="utf-8").strip() == root_pem.strip()
        except OSError:
            return False

    def install(self, root_pem: str) -> ServerStore:
        """
        Put the root in the system store and rebuild the bundle.

        Args:
            root_pem: The root certificate (public).

        Returns:
            The store it was put in.

        Raises:
            CertificateError: This system has no store Noust knows, or rebuilding it failed.
        """
        found = self._require_store()
        self.fs.write_text(found.directory / ANCHOR_NAME, root_pem, mode=0o644)
        self._rebuild(found, found.install, "trust")
        return found

    def remove(self) -> bool:
        """
        Take the root out of the system store and rebuild the bundle.

        Returns:
            Whether it was there.

        Raises:
            CertificateError: Rebuilding the bundle failed.
        """
        found = self.store()
        if found is None or not (found.directory / ANCHOR_NAME).exists():
            return False
        self.fs.remove(found.directory / ANCHOR_NAME)
        self._rebuild(found, found.uninstall, "stop trusting")
        return True

    def _require_store(self) -> ServerStore:
        found = self.store()
        if found is None:
            raise CertificateError(
                "This system has no store of authorities Noust knows how to update",
                details="Install the ca-certificates package, or follow the steps for this system: "
                "noust cert ca trust TARGET",
            )
        return found

    def _rebuild(self, found: ServerStore, arguments: tuple[str, ...], what: str) -> None:
        result = self.runner.run([found.tool, *arguments], timeout=_UPDATE_TIMEOUT)
        if not result.success:
            raise CertificateError(
                f"{found.tool} could not {what} the internal authority",
                details="The certificate is in " + str(found.directory) + ".",
                output=(result.stderr or result.stdout).strip()[-600:] or None,
            )
