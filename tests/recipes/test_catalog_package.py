# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A signed package of recipes that grows the catalogue between releases (checklist D-20)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from noust.cli.app import cli
from noust.recipes import RecipeError, catalog_package, get_recipe, list_recipes
from noust.recipes.catalog import catalogue_problems
from noust.recipes.catalog_package import (
    MANIFEST,
    current_package,
    import_package,
    remove_package,
    trust_key,
    trusted_keys,
)
from tests.recipes.factory import compose_recipe
from tests.recipes.test_operator_recipes import LOGO

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "recipe_catalog.py"


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    """The packing script, loaded as a module."""
    spec = importlib.util.spec_from_file_location("recipe_catalog", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_catalog"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the imported package and the trusted keys are, in a folder of this test's own."""
    root = tmp_path / "state" / "recipe-catalog"
    monkeypatch.setattr(catalog_package, "catalog_directory", lambda: root / "current")
    monkeypatch.setattr(catalog_package, "keys_file", lambda: tmp_path / "recipe-catalog.keys")
    monkeypatch.setenv("NOUST_RECIPES_DIR", str(tmp_path / "private"))
    return root / "current"


@pytest.fixture
def key() -> ed25519.Ed25519PrivateKey:
    """The publisher's signing key."""
    return ed25519.Ed25519PrivateKey.generate()


def public_hex(private: ed25519.Ed25519PrivateKey) -> str:
    """A signing key's public half as 64 hex digits."""
    return (
        private.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )


def write_recipe(directory: Path, name: str, **overrides: Any) -> None:
    """Write a recipe and its logo into a folder."""
    document = compose_recipe(name=name, title=name.title(), **overrides)
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(document, sort_keys=False))
    (directory / "assets" / name).mkdir(parents=True, exist_ok=True)
    (directory / "assets" / name / "logo.svg").write_text(LOGO)


def package(
    tool: ModuleType,
    tmp_path: Path,
    key: ed25519.Ed25519PrivateKey,
    *recipes: tuple[str, dict[str, Any]],
    serial: int = 1,
    min_noust: str | None = None,
    name: str = "package.tar.gz",
) -> Path:
    """Build a signed package of these recipes."""
    folder = tmp_path / f"folder-{serial}-{name}"
    folder.mkdir()
    for recipe_name, overrides in recipes:
        write_recipe(folder, recipe_name, **overrides)
    output = tmp_path / name
    tool.pack_directory(folder, output, private_key=key, serial=serial, min_noust=min_noust)
    return output


def rewrite(source: Path, change: Any) -> Path:
    """Open a package, let ``change`` alter its members, and write it back."""
    members: dict[str, bytes] = {}
    with tarfile.open(source) as archive:
        for member in archive:
            handle = archive.extractfile(member)
            assert handle is not None
            members[member.name] = handle.read()
    change(members)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    target = source.with_name("tampered-" + source.name)
    target.write_bytes(buffer.getvalue())
    return target


class TestImporting:
    def test_a_signed_package_adds_its_recipes_to_the_catalogue(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        path = package(tool, tmp_path, key, ("acme-wiki", {}), ("acme-blog", {}))

        report = import_package(path)

        assert report.added == ("acme-blog", "acme-wiki")
        recipe = get_recipe("acme-wiki")
        assert recipe.origin == "catalog" and recipe.title == "Acme-Wiki"
        assert current_package() is not None and current_package().signer == "acme"  # type: ignore[union-attr]
        assert (place / "acme-wiki.yaml").is_file()
        assert (place / "assets" / "acme-wiki" / "logo.svg").is_file()

    def test_a_package_nobody_trusted_signed_changes_nothing(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        path = package(tool, tmp_path, key, ("acme-wiki", {}))

        with pytest.raises(RecipeError, match="trusts"):
            import_package(path)

        assert not place.exists() and current_package() is None
        assert "acme-wiki" not in {recipe.name for recipe in list_recipes()}

    def test_a_package_signed_by_someone_else_is_refused(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(ed25519.Ed25519PrivateKey.generate()))
        path = package(tool, tmp_path, key, ("acme-wiki", {}))

        with pytest.raises(RecipeError, match="not signed by a key this server trusts"):
            import_package(path)

    def test_a_changed_file_does_not_match_the_digest_that_was_signed(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        path = package(tool, tmp_path, key, ("acme-wiki", {}))
        tampered = rewrite(
            path,
            lambda members: members.update(
                {"acme-wiki.yaml": members["acme-wiki.yaml"] + b"# x\n"}
            ),
        )

        with pytest.raises(RecipeError, match="digest"):
            import_package(tampered)

        assert not place.exists()

    def test_a_changed_manifest_breaks_the_signature(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        path = package(tool, tmp_path, key, ("acme-wiki", {}))

        def raise_serial(members: dict[str, bytes]) -> None:
            manifest = json.loads(members[MANIFEST])
            manifest["serial"] = 99
            members[MANIFEST] = json.dumps(manifest, sort_keys=True).encode()

        with pytest.raises(RecipeError, match="not signed by a key"):
            import_package(rewrite(path, raise_serial))

    def test_a_file_the_manifest_does_not_list_is_refused(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        path = package(tool, tmp_path, key, ("acme-wiki", {}))
        extra = rewrite(path, lambda members: members.update({"evil.yaml": b"x: 1\n"}))

        with pytest.raises(RecipeError, match="does not hold the files its manifest lists"):
            import_package(extra)

    def test_a_link_in_the_archive_is_never_followed(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            link = tarfile.TarInfo("acme-wiki.yaml")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            archive.addfile(link)
        evil = tmp_path / "evil.tar.gz"
        evil.write_bytes(buffer.getvalue())

        with pytest.raises(RecipeError, match="not a plain file"):
            import_package(evil)

    def test_a_path_that_climbs_out_is_refused(
        self, tool: ModuleType, tmp_path: Path, place: Path
    ) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            info = tarfile.TarInfo("../outside.yaml")
            info.size = 2
            archive.addfile(info, io.BytesIO(b"x\n"))
        evil = tmp_path / "evil.tar.gz"
        evil.write_bytes(buffer.getvalue())

        with pytest.raises(RecipeError, match="not a plain file"):
            import_package(evil)

    def test_something_that_is_not_an_archive_is_an_error_not_a_crash(
        self, tmp_path: Path, place: Path
    ) -> None:
        junk = tmp_path / "junk.tar.gz"
        junk.write_bytes(b"not a tarball")

        with pytest.raises(RecipeError, match="not a readable"):
            import_package(junk)

    def test_a_recipe_that_would_not_pass_the_lint_stops_the_whole_package(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        folder = tmp_path / "folder"
        folder.mkdir()
        write_recipe(folder, "acme-wiki")
        write_recipe(folder, "acme-blog")
        text = (folder / "acme-blog.yaml").read_text().replace("1.2.3@sha256", "latest@sha256")
        (folder / "acme-blog.yaml").write_text(text)

        with pytest.raises(RecipeError, match="not valid"):
            tool.pack_directory(folder, tmp_path / "p.tar.gz", private_key=key, serial=1)

    def test_a_noust_too_old_for_the_package_refuses_it(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        path = package(tool, tmp_path, key, ("acme-wiki", {}), min_noust="99.0.0")

        with pytest.raises(RecipeError, match=r"needs Noust 99\.0\.0"):
            import_package(path)


class TestItNeverGoesBack:
    def test_a_lower_serial_is_refused_unless_forced(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        import_package(
            package(tool, tmp_path, key, ("acme-wiki", {}), serial=5, name="five.tar.gz")
        )
        older = package(tool, tmp_path, key, ("acme-blog", {}), serial=4, name="four.tar.gz")

        with pytest.raises(RecipeError, match="not higher"):
            import_package(older)
        assert current_package().serial == 5  # type: ignore[union-attr]
        import_package(older, force=True)
        assert current_package().serial == 4  # type: ignore[union-attr]

    def test_a_newer_package_replaces_the_older_one_whole(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        import_package(package(tool, tmp_path, key, ("acme-wiki", {}), serial=1, name="a.tar.gz"))

        import_package(package(tool, tmp_path, key, ("acme-blog", {}), serial=2, name="b.tar.gz"))

        names = {recipe.name for recipe in list_recipes()}
        assert "acme-blog" in names and "acme-wiki" not in names

    def test_a_shipped_recipe_is_replaced_only_by_a_newer_one(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        shipped = get_recipe("vaultwarden")
        path = package(
            tool,
            tmp_path,
            key,
            ("vaultwarden", {"version": "99.0.0"}),
            ("memos", {"version": "0.0.1"}),
        )

        report = import_package(path)

        assert report.updated == ("vaultwarden",) and report.ignored == ("memos",)
        assert get_recipe("vaultwarden").origin == "catalog"
        assert get_recipe("vaultwarden").version == "99.0.0"
        assert get_recipe("memos").origin == "shipped"
        assert shipped.version != "99.0.0"

    def test_a_release_that_ships_something_newer_wins_again(
        self,
        tool: ModuleType,
        tmp_path: Path,
        place: Path,
        key: ed25519.Ed25519PrivateKey,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        trust_key("acme", public_hex(key))
        shipped = get_recipe("vaultwarden")
        import_package(package(tool, tmp_path, key, ("vaultwarden", {"version": "99.0.0"})))
        assert get_recipe("vaultwarden").origin == "catalog"

        # The package's version is now the older one.
        monkeypatch.setattr(
            catalog_package,
            "current_package",
            lambda: catalog_package.PackageInfo(
                1, "", "acme", None, {"vaultwarden": {"version": "0.0.1", "revision": 1}}
            ),
        )

        assert get_recipe("vaultwarden").origin == "shipped"
        assert get_recipe("vaultwarden").version == shipped.version

    def test_remove_goes_back_to_what_the_release_ships(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        import_package(package(tool, tmp_path, key, ("acme-wiki", {})))

        assert remove_package() is True
        assert remove_package() is False

        assert "acme-wiki" not in {recipe.name for recipe in list_recipes()}

    def test_a_private_recipe_with_a_name_the_package_brings_is_named_as_a_problem(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        private = tmp_path / "private"
        private.mkdir()
        private.chmod(0o755)
        write_recipe(private, "acme-wiki")
        (private / "acme-wiki.yaml").chmod(0o644)

        report = import_package(package(tool, tmp_path, key, ("acme-wiki", {})))

        assert report.clashes == ("acme-wiki",)
        assert get_recipe("acme-wiki").origin == "catalog"
        assert [problem.name for problem in catalogue_problems()] == ["acme-wiki"]


class TestTrust:
    def test_trusting_a_key_is_remembered_and_replaced_by_name(
        self, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        trust_key("acme", public_hex(key))
        other = ed25519.Ed25519PrivateKey.generate()
        trust_key("acme", public_hex(other))
        trust_key("second", public_hex(key))

        keys = trusted_keys()

        assert set(keys) == {"acme", "second"}
        assert keys["acme"].hex() == public_hex(other)

    @pytest.mark.parametrize(
        ("name", "digits"), [("Bad Name", "ab" * 32), ("ok", "xyz"), ("ok", "ab" * 31)]
    )
    def test_a_name_or_key_that_is_not_one_is_refused(
        self, place: Path, name: str, digits: str
    ) -> None:
        with pytest.raises(RecipeError):
            trust_key(name, digits)


class TestTheTool:
    def test_pack_is_deterministic_and_verify_checks_it(
        self, tool: ModuleType, tmp_path: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        first = package(tool, tmp_path, key, ("acme-wiki", {}), name="one.tar.gz")
        folder = tmp_path / "folder-1-one.tar.gz"
        second = tmp_path / "two.tar.gz"
        tool.pack_directory(folder, second, private_key=key, serial=1)

        assert first.read_bytes() == second.read_bytes()
        assert tool.verify_package(first, public_hex(key)).serial == 1
        with pytest.raises(RecipeError, match="does not verify"):
            tool.verify_package(first, public_hex(ed25519.Ed25519PrivateKey.generate()))

    def test_the_command_line_makes_a_key_packs_and_verifies(
        self, tool: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        keyfile = tmp_path / "signing.pem"
        assert tool.main(["keygen", str(keyfile)]) == 0
        public = capsys.readouterr().out.strip()
        assert len(public) == 64 and keyfile.stat().st_mode & 0o777 == 0o600
        folder = tmp_path / "recipes"
        folder.mkdir()
        write_recipe(folder, "acme-wiki")
        output = tmp_path / "out.tar.gz"

        assert (
            tool.main(["pack", str(folder), str(output), "--key", str(keyfile), "--serial", "3"])
            == 0
        )
        assert tool.main(["verify", str(output), "--public-key", public]) == 0
        assert tool.main(["verify", str(output), "--public-key", "00" * 32]) == 1


class TestTheCommandLine:
    def run(self, *args: str) -> Any:
        return CliRunner().invoke(cli, ["recipe", "catalog", *args])

    def test_trust_import_and_show(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        path = package(tool, tmp_path, key, ("acme-wiki", {}), serial=7)

        trusted = self.run("trust", "acme", public_hex(key))
        imported = self.run("import", str(path))
        shown = self.run("show", "--json")

        assert trusted.exit_code == 0, trusted.output
        assert imported.exit_code == 0, imported.output
        assert "acme-wiki" in imported.output
        payload = json.loads(shown.output)
        assert payload["package"]["serial"] == 7 and payload["trusted_keys"] == ["acme"]

    def test_an_untrusted_package_exits_non_zero_and_changes_nothing(
        self, tool: ModuleType, tmp_path: Path, place: Path, key: ed25519.Ed25519PrivateKey
    ) -> None:
        result = self.run("import", str(package(tool, tmp_path, key, ("acme-wiki", {}))))

        assert result.exit_code != 0 and not place.exists()

    def test_remove_and_the_empty_show(self, place: Path) -> None:
        assert "No package" in self.run("show").output
        assert self.run("remove").exit_code == 0


class TestFetching:
    @pytest.fixture
    def served(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
        """What the network answers, by URL."""
        pages: dict[str, bytes] = {}
        monkeypatch.setattr(
            "noust.managers.source_manager._open_url", lambda url, **kw: io.BytesIO(pages[url])
        )
        return pages

    def test_a_package_over_https_goes_through_every_check_import_has(
        self,
        tool: ModuleType,
        tmp_path: Path,
        place: Path,
        key: ed25519.Ed25519PrivateKey,
        served: dict[str, bytes],
    ) -> None:
        trust_key("acme", public_hex(key))
        served["https://packages.example.com/catalog.tar.gz"] = package(
            tool, tmp_path, key, ("acme-wiki", {})
        ).read_bytes()

        report = catalog_package.fetch_package("https://packages.example.com/catalog.tar.gz")

        assert report.added == ("acme-wiki",)
        assert get_recipe("acme-wiki").origin == "catalog"

    def test_a_package_nobody_trusted_signed_is_refused_after_the_download(
        self,
        tool: ModuleType,
        tmp_path: Path,
        place: Path,
        key: ed25519.Ed25519PrivateKey,
        served: dict[str, bytes],
    ) -> None:
        served["https://packages.example.com/catalog.tar.gz"] = package(
            tool, tmp_path, key, ("acme-wiki", {})
        ).read_bytes()

        with pytest.raises(RecipeError, match="trusts"):
            catalog_package.fetch_package("https://packages.example.com/catalog.tar.gz")

        assert not place.exists()

    def test_plain_http_is_not_used(self, place: Path, served: dict[str, bytes]) -> None:
        with pytest.raises(RecipeError, match="https"):
            catalog_package.fetch_package("http://packages.example.com/catalog.tar.gz")


class TestTheApi:
    @pytest.fixture
    def client(self, recipe_store: Any) -> Any:
        from fastapi.testclient import TestClient

        from noust.web.api.deps import require_elevated
        from tests.recipes.test_api import small_app

        api = small_app()
        api.dependency_overrides[require_elevated] = lambda: {
            "type": "session",
            "session_id": "s",
            "scope": "admin",
            "name": "alice",
        }
        return TestClient(api)

    def test_the_state_names_the_package_and_the_keys(
        self,
        client: Any,
        tool: ModuleType,
        tmp_path: Path,
        place: Path,
        key: ed25519.Ed25519PrivateKey,
    ) -> None:
        assert client.get("/api/recipes/catalog").json() == {"package": None, "trusted_keys": []}
        trust_key("acme", public_hex(key))
        import_package(package(tool, tmp_path, key, ("acme-wiki", {}), serial=4))

        body = client.get("/api/recipes/catalog").json()

        assert body["package"]["serial"] == 4 and body["package"]["signer"] == "acme"
        assert body["trusted_keys"] == ["acme"]

    def test_fetch_imports_and_remove_goes_back(
        self,
        client: Any,
        tool: ModuleType,
        tmp_path: Path,
        place: Path,
        key: ed25519.Ed25519PrivateKey,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        trust_key("acme", public_hex(key))
        data = package(tool, tmp_path, key, ("acme-wiki", {})).read_bytes()
        monkeypatch.setattr(
            "noust.managers.source_manager._open_url", lambda url, **kw: io.BytesIO(data)
        )

        fetched = client.post(
            "/api/recipes/catalog/fetch", json={"url": "https://packages.example.com/c.tar.gz"}
        )
        removed = client.delete("/api/recipes/catalog")

        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["added"] == ["acme-wiki"]
        assert removed.status_code == 204 and current_package() is None

    def test_an_untrusted_package_is_a_400_and_changes_nothing(
        self,
        client: Any,
        tool: ModuleType,
        tmp_path: Path,
        place: Path,
        key: ed25519.Ed25519PrivateKey,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data = package(tool, tmp_path, key, ("acme-wiki", {})).read_bytes()
        monkeypatch.setattr(
            "noust.managers.source_manager._open_url", lambda url, **kw: io.BytesIO(data)
        )

        response = client.post(
            "/api/recipes/catalog/fetch", json={"url": "https://packages.example.com/c.tar.gz"}
        )

        assert response.status_code == 400 and "trusts" in response.text
        assert current_package() is None

    def test_the_routes_declare_their_permissions(self) -> None:
        from noust.web.permissions.routes_recipes import ROUTES

        assert ("GET", "/api/recipes/catalog") in ROUTES
        assert ("POST", "/api/recipes/catalog/fetch") in ROUTES
        assert ("DELETE", "/api/recipes/catalog") in ROUTES
