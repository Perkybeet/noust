#!/usr/bin/env python3
"""
Make and check the signed packages of recipes `noust recipe catalog import` reads.

Usage:
    scripts/recipe_catalog.py keygen KEYFILE
        Make an Ed25519 signing key, write it (PEM, mode 0600) to KEYFILE and print
        its public half as 64 hex digits - the value `noust recipe catalog trust`
        takes, and what a release adds to MAINTAINER_KEYS.
    scripts/recipe_catalog.py pack DIR OUTPUT --key KEYFILE --serial N [--min-noust X.Y.Z]
        Check every recipe of DIR (laid out as the catalogue is: <name>.yaml and
        assets/<name>/...) with the lint the shipped ones pass, then write OUTPUT,
        a .tar.gz with a manifest of every file's SHA-256 and the manifest's
        signature. The same folder gives the same bytes.
    scripts/recipe_catalog.py verify PACKAGE --public-key HEX
        Check a package's signature, digests and layout without importing it.

The private key stays with whoever publishes: nothing here reads the network.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.recipes import catalog, lint  # noqa: E402
from noust.recipes.catalog_package import (  # noqa: E402
    FORMAT,
    MANIFEST,
    SIGNATURE,
    PackageInfo,
    _check_files,
    _manifest,
    _read_archive,
    _signature_bytes,
)
from noust.recipes.model import RecipeError  # noqa: E402
from noust.recipes.sources import folder_files  # noqa: E402


def pack_directory(
    directory: Path,
    output: Path,
    *,
    private_key: Any,
    serial: int,
    min_noust: str | None = None,
    created: str | None = None,
) -> PackageInfo:
    """
    Make a signed package from a folder of recipes.

    The archive is deterministic: sorted entries, no owner, a fixed time.

    Args:
        directory: A folder laid out as the catalogue is.
        output: Where to write the ``.tar.gz``.
        private_key: An ``Ed25519PrivateKey`` of the ``cryptography`` library.
        serial: The package's serial.
        min_noust: The oldest Noust that can read it.
        created: The date to record; today by default.

    Returns:
        The package's facts.

    Raises:
        RecipeError: A file is not a recipe, or a recipe is not valid.
    """
    found, problems = folder_files(directory, "catalog", taken={}, trust_owner=True)
    if problems:
        raise RecipeError(
            "The folder holds a file that is not a recipe: "
            + "; ".join(f"{p.name}: {p.error}" for p in problems)
        )
    errors = [
        finding.render()
        for entry in found.values()
        for finding in lint.lint_file(entry)
        if finding.severity == lint.ERROR
    ]
    if errors:
        raise RecipeError("A recipe is not valid: " + "; ".join(errors[:5]))
    members: dict[str, bytes] = {}
    for name in sorted(found):
        members[f"{name}.yaml"] = (directory / f"{name}.yaml").read_bytes()
        base = directory / "assets" / name
        if base.is_dir():
            for file in sorted(base.rglob("*")):
                if file.is_file() and not file.is_symlink():
                    relative = file.relative_to(base).as_posix()
                    members[f"assets/{name}/{relative}"] = file.read_bytes()
    recipes: dict[str, dict[str, Any]] = {}
    for name, entry in sorted(found.items()):
        recipe = catalog.load_file(entry)
        recipes[name] = {"version": recipe.version, "revision": recipe.revision}
    manifest = {
        "format": FORMAT,
        "serial": serial,
        "created": created or date.today().isoformat(),
        "min_noust": min_noust,
        "recipes": recipes,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(members.items())},
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    members[MANIFEST] = manifest_bytes
    members[SIGNATURE] = private_key.sign(manifest_bytes).hex().encode("ascii") + b"\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in sorted(members):
            info = tarfile.TarInfo(name)
            info.size = len(members[name])
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(members[name]))
    output.write_bytes(buffer.getvalue())
    return PackageInfo(
        serial=serial,
        created=str(manifest["created"]),
        signer="",
        min_noust=min_noust,
        recipes=recipes,
    )


def verify_package(path: Path, public_key_hex: str) -> PackageInfo:
    """
    Check a package against a public key without importing it.

    Args:
        path: The ``.tar.gz``.
        public_key_hex: The signer's public key.

    Returns:
        The package's facts.

    Raises:
        RecipeError: The signature, a digest or the layout is wrong.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    files = _read_archive(path.read_bytes())
    manifest_bytes = files.pop(MANIFEST, b"")
    signature = _signature_bytes(files.pop(SIGNATURE, b""))
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(
            signature, manifest_bytes
        )
    except (InvalidSignature, ValueError) as exc:
        raise RecipeError("The signature does not verify with that key") from exc
    manifest = _manifest(manifest_bytes)
    _check_files(manifest, files)
    return PackageInfo(
        serial=int(manifest["serial"]),
        created=str(manifest.get("created", "")),
        signer="",
        min_noust=manifest.get("min_noust"),
        recipes=dict(manifest.get("recipes") or {}),
    )


def main(argv: list[str] | None = None) -> int:
    """
    Run the tool.

    Args:
        argv: Arguments, without the program name.

    Returns:
        0 on success, 1 on a refusal.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    parser = argparse.ArgumentParser(description="Make and check signed recipe packages.")
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen", help="Make a signing key.")
    keygen.add_argument("keyfile", type=Path)
    pack = commands.add_parser("pack", help="Sign a folder of recipes into a package.")
    pack.add_argument("directory", type=Path)
    pack.add_argument("output", type=Path)
    pack.add_argument("--key", type=Path, required=True)
    pack.add_argument("--serial", type=int, required=True)
    pack.add_argument("--min-noust")
    verify = commands.add_parser("verify", help="Check a package.")
    verify.add_argument("package", type=Path)
    verify.add_argument("--public-key", required=True)
    arguments = parser.parse_args(argv)

    if arguments.command == "keygen":
        key = ed25519.Ed25519PrivateKey.generate()
        arguments.keyfile.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        arguments.keyfile.chmod(0o600)
        public = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        print(public.hex())
        return 0
    try:
        if arguments.command == "pack":
            private = serialization.load_pem_private_key(arguments.key.read_bytes(), password=None)
            if not isinstance(private, ed25519.Ed25519PrivateKey):
                print("The key is not an Ed25519 key.", file=sys.stderr)
                return 1
            info = pack_directory(
                arguments.directory,
                arguments.output,
                private_key=private,
                serial=arguments.serial,
                min_noust=arguments.min_noust,
            )
            print(f"{arguments.output}: serial {info.serial}, {len(info.recipes)} recipe(s)")
            return 0
        info = verify_package(arguments.package, arguments.public_key)
        print(f"{arguments.package}: serial {info.serial}, {len(info.recipes)} recipe(s), signed")
        return 0
    except RecipeError as exc:
        print(f"{exc.message} {exc.details or ''}".strip(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
