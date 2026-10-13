# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A signed package of recipes that grows the catalogue between releases of Noust.

Coolify syncs its templates from a CDN every hour and trusts the answer. A
catalogue that changes under a server is a supply chain, so here it is a file the
operator brings and Noust checks, never a poll:

- **A package** is a ``.tar.gz`` of recipes laid out as the catalogue is
  (``<name>.yaml``, ``assets/<name>/...``) with a ``manifest.json`` that lists
  every file with its SHA-256 and says the package's ``serial``, and a
  ``manifest.json.sig``: an Ed25519 signature over the manifest bytes.
- **It is trusted** when a key the operator trusts signed it: the maintainers'
  (:data:`MAINTAINER_KEYS`, added by the release that carries them) or the ones in
  ``/etc/noust/recipe-catalog.keys`` (one ``name hex-public-key`` per line).
- **It is data, never code.** Every recipe in it passes the schema and the lint
  the shipped ones pass, an archive is read into memory under size limits and a
  link or a path outside the layout is refused. Nothing in it runs.
- **It never goes back.** The ``serial`` must be higher than the one in place, and
  a recipe in it replaces the shipped one only while it is newer: when a release
  of Noust ships a newer one, the shipped one is used again.
- **No network is needed to install.** ``import`` reads a file; ``fetch`` is the
  one step that downloads it, over https, and imports what it got.

The imported package is unpacked into ``recipe-catalog/current`` under Noust's
state directory, which :mod:`noust.recipes.sources` reads as the ``catalog``
origin.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import noust
from noust.core import audit, paths
from noust.core.exceptions import DependencyError, NoustError
from noust.core.fs import get_fs
from noust.recipes.assets import MAX_ASSET_BYTES, is_plain_path
from noust.recipes.model import NAME_PATTERN, RecipeError
from noust.recipes.sources import MAX_RECIPE_BYTES
from noust.recipes.versions import is_newer

#: The format this release reads.
FORMAT = 1

#: The signed manifest, and its signature (hex or base64 of 64 bytes).
MANIFEST = "manifest.json"
SIGNATURE = "manifest.json.sig"

#: Limits. A package is a few hundred KiB of text.
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 40 * 1024 * 1024
MAX_ENTRIES = 3000

#: The public keys of whoever signs the catalogue the project publishes, as
#: ``(name, hex)``. Empty in a source tree: the release that publishes a package
#: adds the key that signs it. An operator adds their own to the keys file.
MAINTAINER_KEYS: tuple[tuple[str, str], ...] = ()

#: ``name hex-public-key``, one per line, ``#`` for comments.
KEYS_FILE = "recipe-catalog.keys"

_HEX_KEY = re.compile(r"^[0-9a-fA-F]{64}$")


def catalog_directory() -> Path:
    """
    Say where the imported catalogue package is unpacked.

    Returns:
        ``recipe-catalog/current`` under Noust's state directory.
    """
    return paths.state_dir() / "recipe-catalog" / "current"


def keys_file() -> Path:
    """
    Say where the operator's trusted keys are listed.

    Returns:
        ``recipe-catalog.keys`` under Noust's configuration directory.
    """
    return paths.config_dir() / KEYS_FILE


@dataclass(frozen=True)
class PackageInfo:
    """
    A package that is, or is about to be, in place.

    Attributes:
        serial: Its serial: higher than every package before it.
        created: When it was made, as the manifest says.
        signer: The name of the key that signed it.
        min_noust: The oldest Noust that can read it.
        recipes: Each recipe in it: name to ``{version, revision}``.
    """

    serial: int
    created: str
    signer: str
    min_noust: str | None = None
    recipes: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the package for the API and the command line.

        Returns:
            Every attribute.
        """
        return {
            "serial": self.serial,
            "created": self.created,
            "signer": self.signer,
            "min_noust": self.min_noust,
            "recipes": self.recipes,
        }


@dataclass(frozen=True)
class ImportReport:
    """
    What importing a package did.

    Attributes:
        package: The package now in place.
        added: Recipes the shipped catalogue does not have.
        updated: Recipes that replace a shipped one with a newer version.
        ignored: Recipes in the package that the shipped catalogue already has
            at the same version or newer, so they are not used.
        clashes: Names a private recipe of the operator's also has: the
            private one is left out until it is renamed.
    """

    package: PackageInfo
    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()
    clashes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the import for the API and the command line.

        Returns:
            The package and each list.
        """
        return {
            "package": self.package.to_dict(),
            "added": list(self.added),
            "updated": list(self.updated),
            "ignored": list(self.ignored),
            "clashes": list(self.clashes),
        }


# ------------------------------------------------------------------ trust


def trusted_keys() -> dict[str, bytes]:
    """
    List the public keys whose signature a package is trusted by.

    Returns:
        Name to the 32 raw bytes of each key: the maintainers' and the
        operator's. A line in the keys file that is not ``name hex`` is skipped.
    """
    keys: dict[str, bytes] = {}
    for name, hex_key in MAINTAINER_KEYS:
        keys[name] = bytes.fromhex(hex_key)
    try:
        text = keys_file().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return keys
    for line in text.splitlines():
        words = line.split("#", 1)[0].split()
        if len(words) == 2 and _HEX_KEY.fullmatch(words[1]) and NAME_PATTERN.match(words[0]):
            keys[words[0]] = bytes.fromhex(words[1])
    return keys


def trust_key(name: str, public_key_hex: str) -> None:
    """
    Add a key to the operator's trusted keys.

    Args:
        name: What to call it (lower case letters, digits and dashes).
        public_key_hex: The 32-byte Ed25519 public key as 64 hex digits.

    Raises:
        RecipeError: The name or the key is not valid.
    """
    if not NAME_PATTERN.match(name) or not _HEX_KEY.fullmatch(public_key_hex):
        raise RecipeError(
            "A trusted key is a name and a 64-digit hexadecimal Ed25519 public key",
            details="The name is lower case letters, digits and dashes.",
        )
    existing = keys_file().read_text(encoding="utf-8") if keys_file().exists() else ""
    kept = [
        line for line in existing.splitlines() if not (line.split() and line.split()[0] == name)
    ]
    kept.append(f"{name} {public_key_hex.lower()}")
    fs = get_fs()
    fs.make_dir(keys_file().parent, mode=0o755)
    fs.write_text(keys_file(), "\n".join(kept) + "\n", mode=0o644)
    audit.record("recipe.catalog.trust", target=f"key:{name}")


def _ed25519() -> Any:
    """
    Load the Ed25519 implementation, or say how to get it.

    Returns:
        The ``cryptography`` module for Ed25519 public keys.

    Raises:
        DependencyError: ``cryptography`` is not installed.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise DependencyError(
            "Checking a catalogue package needs the cryptography library, which is not "
            "installed here",
            details="Install python3-cryptography, or pip install 'noust[web]'. Python said: "
            f"{exc}",
        ) from exc
    return ed25519


def _verified_signer(manifest: bytes, signature: bytes, keys: dict[str, bytes]) -> str:
    """
    Find which trusted key signed a manifest.

    Args:
        manifest: The manifest's bytes.
        signature: The 64-byte signature.
        keys: The trusted keys.

    Returns:
        The name of the key that verifies.

    Raises:
        RecipeError: No key trusted here signed it.
    """
    from cryptography.exceptions import InvalidSignature

    ed25519 = _ed25519()
    for name, raw in keys.items():
        try:
            ed25519.Ed25519PublicKey.from_public_bytes(raw).verify(signature, manifest)
        except (InvalidSignature, ValueError):
            continue
        return name
    raise RecipeError(
        "The package is not signed by a key this server trusts",
        details=(
            "Nothing was changed. Trust the publisher's key with: noust recipe catalog trust "
            "NAME PUBLIC_KEY_HEX"
            if keys
            else "This server trusts no key. Trust the publisher's with: noust recipe catalog "
            "trust NAME PUBLIC_KEY_HEX"
        ),
    )


def _signature_bytes(text: bytes) -> bytes:
    """
    Read a signature written as hexadecimal or base64.

    Args:
        text: The signature file's content.

    Returns:
        The 64 raw bytes.

    Raises:
        RecipeError: It is neither, or not 64 bytes.
    """
    stripped = text.strip()
    for decode in (bytes.fromhex, base64.b64decode):
        try:
            raw = decode(stripped.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            continue
        if len(raw) == 64:
            return raw
    raise RecipeError("The package's signature is not 64 bytes of hex or base64")


# ------------------------------------------------------------------ reading


def current_package() -> PackageInfo | None:
    """
    Read the package in place.

    Returns:
        Its manifest's facts, or None when no package was imported. The
        manifest was verified when it was imported and the directory belongs to
        root, so it is read as it is.
    """
    try:
        data = json.loads((catalog_directory() / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return _info(data, str(data.get("signer", "")))


def _info(manifest: dict[str, Any], signer: str) -> PackageInfo:
    """
    Pick the facts out of a manifest.

    Args:
        manifest: The manifest as JSON loaded it.
        signer: The key that signed it.

    Returns:
        The facts, tolerating a manifest field of the wrong type.
    """
    recipes = manifest.get("recipes")
    return PackageInfo(
        serial=int(manifest.get("serial") or 0),
        created=str(manifest.get("created") or ""),
        signer=signer,
        min_noust=str(manifest["min_noust"]) if manifest.get("min_noust") else None,
        recipes=dict(recipes) if isinstance(recipes, dict) else {},
    )


def usable_names(shipped: dict[str, Any], load_shipped: Any) -> set[str]:
    """
    Say which of the imported recipes are used instead of the shipped ones.

    A recipe from the package replaces a shipped one only while it is newer;
    after an upgrade of Noust that ships a newer one, the shipped one wins. The
    versions come from the manifest, which was verified at import.

    Args:
        shipped: The shipped recipe files by name.
        load_shipped: Loads a shipped recipe file into a ``Recipe``.

    Returns:
        The names the package supplies: those the shipped catalogue lacks and
        those it has an older version of.
    """
    package = current_package()
    if package is None:
        return set()
    names: set[str] = set()
    for name, facts in package.recipes.items():
        entry = shipped.get(name)
        if entry is None:
            names.add(name)
            continue
        try:
            ours = load_shipped(entry)
        except RecipeError:
            continue
        if is_newer(
            str(facts.get("version") or ""),
            int(facts.get("revision") or 1),
            than_version=ours.version,
            than_revision=ours.revision,
        ):
            names.add(name)
    return names


# ------------------------------------------------------------------ importing


def _read_archive(source: bytes) -> dict[str, bytes]:
    """
    Read a package into memory, refusing anything that is not a plain file.

    Args:
        source: The ``.tar.gz``'s bytes.

    Returns:
        Path to content.

    Raises:
        RecipeError: It is not a gzip tar, holds a link, a device, an absolute
            or climbing path, too many or too large entries.
    """
    files: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive:
            for count, member in enumerate(archive, start=1):
                if count > MAX_ENTRIES:
                    raise RecipeError(f"The package holds more than {MAX_ENTRIES} entries")
                if member.isdir():
                    continue
                path = PurePosixPath(member.name)
                if (
                    not member.isreg()
                    or path.is_absolute()
                    or ".." in path.parts
                    or not is_plain_path(member.name.removeprefix("./"))
                ):
                    raise RecipeError(
                        f"The package holds {member.name!r}, which is not a plain file inside it",
                        details="Links, devices and paths outside the layout are never read.",
                    )
                if member.name == MANIFEST:
                    limit = 1024 * 1024
                else:
                    limit = MAX_RECIPE_BYTES if member.name.endswith(".yaml") else MAX_ASSET_BYTES
                if member.size > limit:
                    raise RecipeError(f"{member.name} is larger than a recipe's file can be")
                total += member.size
                if total > MAX_UNPACKED_BYTES:
                    raise RecipeError("The package unpacks to more than 40 MiB")
                handle = archive.extractfile(member)
                files[member.name.removeprefix("./")] = handle.read() if handle else b""
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise RecipeError(
            f"The package is not a readable .tar.gz: {exc}", details="Nothing was changed."
        ) from exc
    return files


def import_package(path: Path, *, force: bool = False, actor: str | None = None) -> ImportReport:
    """
    Check a package and put it in place.

    Args:
        path: The ``.tar.gz``.
        force: Accept a package whose serial is not higher than the one in
            place (to go back to an earlier one on purpose).
        actor: Who imports it, for the audit trail.

    Returns:
        What it brought: the recipes it adds, the ones it updates and the ones
        the shipped catalogue is already ahead on.

    Raises:
        RecipeError: Any check fails: size, archive layout, signature,
            manifest, a file whose digest differs, a recipe that is not valid,
            a Noust too old for it, a serial that goes back. Nothing is
            changed.
        DependencyError: The cryptography library is missing.
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_PACKAGE_BYTES + 1)
    except OSError as exc:
        raise RecipeError(f"{path} cannot be read: {exc}", details="Nothing was changed.") from exc
    return import_bytes(data, force=force, actor=actor)


def import_bytes(data: bytes, *, force: bool = False, actor: str | None = None) -> ImportReport:
    """
    Check a package that is already in memory and put it in place.

    Args:
        data: The ``.tar.gz``'s bytes.
        force: Accept a package whose serial is not higher than the one in
            place.
        actor: Who imports it, for the audit trail.

    Returns:
        What it brought.

    Raises:
        RecipeError: Any check fails; nothing is changed.
        DependencyError: The cryptography library is missing.
    """
    if len(data) > MAX_PACKAGE_BYTES:
        raise RecipeError("The package is larger than 20 MiB", details="Nothing was changed.")
    files = _read_archive(data)
    manifest_bytes = files.pop(MANIFEST, None)
    signature_bytes = files.pop(SIGNATURE, None)
    if manifest_bytes is None or signature_bytes is None:
        raise RecipeError(
            f"The package has no {MANIFEST} or no {SIGNATURE}", details="Nothing was changed."
        )
    signer = _verified_signer(manifest_bytes, _signature_bytes(signature_bytes), trusted_keys())
    manifest = _manifest(manifest_bytes)
    package = _info(manifest, signer)
    _check_files(manifest, files)
    if package.min_noust and is_newer(
        package.min_noust, 1, than_version=noust.__version__, than_revision=1
    ):
        raise RecipeError(
            f"The package needs Noust {package.min_noust} or newer; this is Noust "
            f"{noust.__version__}",
            details="Nothing was changed.",
        )
    in_place = current_package()
    if in_place is not None and package.serial <= in_place.serial and not force:
        raise RecipeError(
            f"The package's serial {package.serial} is not higher than the one in place "
            f"({in_place.serial})",
            details="A catalogue only goes forward. Import with --force to go back on purpose.",
        )
    report = _install(files, manifest_bytes, signature_bytes, package, signer)
    audit.record(
        "recipe.catalog.import",
        target=f"catalog:{package.serial}",
        details={
            "signer": signer,
            "added": list(report.added),
            "updated": list(report.updated),
            "actor": actor,
        },
    )
    return report


def _manifest(raw: bytes) -> dict[str, Any]:
    """
    Read a verified manifest.

    Args:
        raw: Its bytes.

    Returns:
        The manifest.

    Raises:
        RecipeError: It is not JSON, not this format, or lacks what it needs.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RecipeError(
            f"The manifest is not JSON: {exc}", details="Nothing was changed."
        ) from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise RecipeError(f"The manifest is not format {FORMAT}", details="Nothing was changed.")
    if not isinstance(data.get("serial"), int) or not isinstance(data.get("files"), dict):
        raise RecipeError("The manifest needs a serial and a list of files")
    return data


def _check_files(manifest: dict[str, Any], files: dict[str, bytes]) -> None:
    """
    Check that the archive holds exactly the files the manifest signs.

    Args:
        manifest: The verified manifest.
        files: Path to content, without the manifest and its signature.

    Raises:
        RecipeError: A file is missing, extra, has another digest, is not
            UTF-8 text, or sits outside ``<name>.yaml`` and ``assets/<name>/``.
    """
    listed: dict[str, str] = manifest["files"]
    if set(listed) != set(files):
        extra = sorted(set(files) - set(listed))
        missing = sorted(set(listed) - set(files))
        raise RecipeError(
            "The package does not hold the files its manifest lists",
            details=f"Extra: {extra or 'none'}; missing: {missing or 'none'}. Nothing was changed.",
        )
    for name, content in files.items():
        if hashlib.sha256(content).hexdigest() != str(listed[name]).lower():
            raise RecipeError(
                f"{name} does not match the digest the manifest signs",
                details="Nothing was changed.",
            )
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RecipeError(f"{name} is not text", details="Nothing was changed.") from exc
        parts = PurePosixPath(name).parts
        top_level = len(parts) == 1 and name.endswith(".yaml")
        asset = len(parts) >= 3 and parts[0] == "assets"
        if not (top_level or asset) or (
            top_level and not NAME_PATTERN.match(name.removesuffix(".yaml"))
        ):
            raise RecipeError(
                f"{name} is not part of the layout (<name>.yaml and assets/<name>/...)",
                details="Nothing was changed.",
            )


def _install(
    files: dict[str, bytes],
    manifest: bytes,
    signature: bytes,
    package: PackageInfo,
    signer: str,
) -> ImportReport:
    """
    Validate the recipes of a verified package and put them in place.

    Args:
        files: The package's files.
        manifest: The manifest's bytes.
        signature: The signature's bytes.
        package: The facts of the manifest.
        signer: The key that signed it.

    Returns:
        What it brought.

    Raises:
        RecipeError: A recipe does not pass the schema and the lint.
    """
    from noust.recipes import catalog, lint
    from noust.recipes.sources import folder_files, shipped_files

    fs = get_fs()
    root = catalog_directory().parent
    fs.make_dir(root, mode=0o755)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        fs.make_dir(staging, mode=0o755)
        for name, content in files.items():
            fs.make_dir((staging / name).parent, mode=0o755)
            fs.write_text(staging / name, content.decode("utf-8"), mode=0o644)
        # Written as the package says, so the manifest read later is the signed one.
        stamped = json.loads(manifest.decode("utf-8"))
        stamped["signer"] = signer
        fs.write_text(staging / MANIFEST, json.dumps(stamped, indent=2, sort_keys=True), mode=0o644)
        fs.write_text(staging / SIGNATURE, signature.hex() + "\n", mode=0o644)
        found, problems = folder_files(staging, "catalog", taken={}, trust_owner=True)
        if problems:
            raise RecipeError(
                "The package holds a file that is not a recipe: "
                + "; ".join(f"{p.name}: {p.error}" for p in problems),
                details="Nothing was changed.",
            )
        errors = [
            finding.render()
            for entry in found.values()
            for finding in lint.lint_file(entry)
            if finding.severity == lint.ERROR
        ]
        if errors:
            raise RecipeError(
                "A recipe in the package is not valid: " + "; ".join(errors[:5]),
                details="Nothing was changed.",
            )
        mismatched = _mismatched_versions(package, found)
        if mismatched:
            raise RecipeError(
                f"The manifest's versions differ from the recipes': {', '.join(mismatched)}",
                details="Nothing was changed.",
            )
        shipped = shipped_files()
        report = _classify(package, found, shipped, catalog)
        _swap(staging)
    finally:
        if staging.exists():
            fs.remove_tree(staging)
    # The loader re-registers every recipe's assets the next time it looks.
    return report


def _mismatched_versions(package: PackageInfo, found: dict[str, Any]) -> list[str]:
    """
    Compare what the manifest says about each recipe with the recipe.

    Args:
        package: The manifest's facts.
        found: The recipe files staged.

    Returns:
        The names whose version or revision differ, or that are not listed.
    """
    from noust.recipes import catalog

    bad: list[str] = []
    for name, entry in found.items():
        facts = package.recipes.get(name)
        recipe = catalog.load_file(entry)
        if (
            facts is None
            or str(facts.get("version")) != recipe.version
            or int(facts.get("revision") or 1) != recipe.revision
        ):
            bad.append(name)
    return bad


def _classify(
    package: PackageInfo, found: dict[str, Any], shipped: dict[str, Any], catalog: Any
) -> ImportReport:
    """
    Say what each recipe of a package does to the catalogue.

    Args:
        package: The manifest's facts.
        found: The staged recipe files.
        shipped: The shipped recipe files.
        catalog: The catalogue module, to load a shipped recipe.

    Returns:
        The report.
    """
    added: list[str] = []
    updated: list[str] = []
    ignored: list[str] = []
    for name in sorted(found):
        facts = package.recipes[name]
        if name not in shipped:
            added.append(name)
            continue
        ours = catalog.load_file(shipped[name])
        if is_newer(
            str(facts.get("version")),
            int(facts.get("revision") or 1),
            than_version=ours.version,
            than_revision=ours.revision,
        ):
            updated.append(name)
        else:
            ignored.append(name)
    from noust.recipes.sources import operator_directory

    clashes = tuple(
        name for name in sorted(found) if (operator_directory() / f"{name}.yaml").exists()
    )
    return ImportReport(package, tuple(added), tuple(updated), tuple(ignored), clashes)


def _swap(staging: Path) -> None:
    """
    Put a staged package where the catalogue reads it.

    The old package is moved aside first and put back if the new one cannot be
    moved in, so a failure leaves the catalogue as it was.

    Args:
        staging: The staged package.
    """
    fs = get_fs()
    current = catalog_directory()
    parked = current.with_name(".previous")
    if parked.exists():
        fs.remove_tree(parked)
    had_one = current.exists()
    if had_one:
        fs.rename(current, parked)
    try:
        fs.rename(staging, current)
    except OSError:
        if had_one:
            fs.rename(parked, current)
        raise
    if had_one:
        fs.remove_tree(parked)


def remove_package(*, actor: str | None = None) -> bool:
    """
    Go back to the recipes the release ships.

    Args:
        actor: Who removes it, for the audit trail.

    Returns:
        Whether there was a package.
    """
    if not catalog_directory().exists():
        return False
    fs = get_fs()
    fs.remove_tree(catalog_directory())
    audit.record("recipe.catalog.remove", target="catalog", details={"actor": actor})
    return True


def fetch_package(url: str, *, force: bool = False, actor: str | None = None) -> ImportReport:
    """
    Download a package over https and import it.

    Args:
        url: An https URL of a ``.tar.gz``.
        force: Accept a package whose serial is not higher.
        actor: Who fetches it, for the audit trail.

    Returns:
        What the import did.

    Raises:
        RecipeError: The address is not https, the download fails or is too
            large, or the package does not pass.
    """
    from noust.managers.source_manager import _open_url, validate_archive_url

    try:
        address = validate_archive_url(url)
        if not address.lower().startswith("https://"):
            raise RecipeError(
                "A package is fetched over https",
                details="Download it yourself and import the file if the publisher has no https "
                "address; the signature is what is trusted, but the transport should not leak "
                "which catalogue you follow.",
            )
        with _open_url(address) as response:
            data = response.read(MAX_PACKAGE_BYTES + 1)
    except NoustError as exc:
        raise RecipeError(f"The package could not be fetched: {exc.message}") from exc
    except OSError as exc:
        raise RecipeError(f"The package could not be fetched: {exc}") from exc
    return import_bytes(data, force=force, actor=actor)


__all__ = [
    "FORMAT",
    "MAINTAINER_KEYS",
    "ImportReport",
    "PackageInfo",
    "catalog_directory",
    "current_package",
    "fetch_package",
    "import_bytes",
    "import_package",
    "keys_file",
    "remove_package",
    "trust_key",
    "trusted_keys",
    "usable_names",
]
