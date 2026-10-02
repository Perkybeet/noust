#!/usr/bin/env python3
"""
A stand-in for ``certbot`` that issues self-signed certificates, for the harness.

The integration container has no route to an ACME server and no real domain, so certbot is
deliberately not installed there and every certificate scenario used ``--no-ssl``. A scenario
that needs the certificate paths of a site (an alias on a TLS site, a certificate that must
survive an update) installs this file as ``/usr/local/bin/certbot`` for its own duration. It
speaks the subset of certbot's command line that Noust's CertManager uses, and keeps certbot's
own layout, so what Noust does with the files is the real code path:

    certbot --version
    certbot plugins                 no nginx or apache plugin: Noust falls back to webroot
    certbot certificates            certbot's listing format, parsed by CertManager
    certbot certonly --cert-name N -d a -d b [--expand] [--dry-run] ...
                                    /etc/letsencrypt/live/N/{fullchain,privkey,cert,chain}.pem,
                                    self-signed, with every -d as a subjectAltName
    certbot renew | delete --cert-name N | revoke --cert-name N

A certificate it issues is not trusted by anything: clients that check it (curl) say ``-k``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIVE = Path("/etc/letsencrypt/live")
DAYS = 89


def option_values(argv: list[str], flag: str) -> list[str]:
    """Every value given for a repeatable option, such as ``-d``."""
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == flag]


def issue(name: str, domains: list[str]) -> None:
    """Write a self-signed lineage covering ``domains``, the way certbot lays one out."""
    directory = LIVE / name
    directory.mkdir(parents=True, exist_ok=True)
    san = ",".join(f"DNS:{domain}" for domain in domains)
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            str(DAYS),
            "-subj",
            f"/CN={domains[0]}",
            "-addext",
            f"subjectAltName={san}",
            "-keyout",
            str(directory / "privkey.pem"),
            "-out",
            str(directory / "fullchain.pem"),
        ],
        check=True,
        capture_output=True,
    )
    shutil.copy(directory / "fullchain.pem", directory / "cert.pem")
    shutil.copy(directory / "fullchain.pem", directory / "chain.pem")
    # certbot keeps the names in the renewal configuration; a sidecar is all this needs.
    (directory / ".domains").write_text("\n".join(domains) + "\n")
    (directory / "privkey.pem").chmod(0o600)
    print(f"Successfully received certificate for {name}: {' '.join(domains)}")


def listing() -> None:
    """Print the lineages in certbot's ``certificates`` format."""
    lineages = sorted(path for path in LIVE.glob("*") if (path / "fullchain.pem").exists())
    if not lineages:
        print("No certificates found.")
        return
    print("Found the following certs:")
    expiry = datetime.now(timezone.utc) + timedelta(days=DAYS)
    for path in lineages:
        domains = (path / ".domains").read_text().split() if (path / ".domains").exists() else []
        print(f"  Certificate Name: {path.name}")
        print("    Serial Number: 1")
        print("    Key Type: RSA")
        print(f"    Domains: {' '.join(domains or [path.name])}")
        print(f"    Expiry Date: {expiry:%Y-%m-%d %H:%M:%S}+00:00 (VALID: {DAYS} days)")
        print(f"    Certificate Path: {path}/fullchain.pem")
        print(f"    Private Key Path: {path}/privkey.pem")


def main() -> int:
    """Run the one subcommand certbot was asked for."""
    argv = sys.argv[1:]
    if "--version" in argv:
        print("certbot 2.9.0")
        return 0
    command = next((arg for arg in argv if not arg.startswith("-")), "")
    if command == "plugins":
        print("Currently available plugins:\n* webroot\n* standalone")
    elif command == "certificates":
        listing()
    elif command == "certonly":
        names = option_values(argv, "--cert-name")
        domains = option_values(argv, "-d")
        if not names or not domains:
            print("certbot: certonly needs --cert-name and at least one -d", file=sys.stderr)
            return 2
        if "--dry-run" in argv:
            print("The dry run was successful.")
            return 0
        issue(names[0], domains)
    elif command in ("delete", "revoke"):
        for name in option_values(argv, "--cert-name"):
            shutil.rmtree(LIVE / name, ignore_errors=True)
    elif command == "renew":
        print("No renewals were attempted.")
    else:
        print(f"certbot: unsupported command {command!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
