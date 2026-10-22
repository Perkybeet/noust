# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust releases bundle`` and ``noust releases receive`` (checklist A-49): what the commands take,
print and refuse. The mechanism is ``tests/test_release_bundles.py``; here the functions are
replaced by recorders, so what is pinned is the line between the terminal and them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.deployers import bundle as bundles
from noust.deployers.bundle import Bundle, Manifest
from noust.deployers.release_bundles import Reception

DIGEST = "a" * 64
MANIFEST = Manifest(
    format=1,
    app_type="nodejs",
    domain="shop.example.com",
    release_id="20261013-100000-abc1234",
    commit="abc1234",
    branch="main",
    app_root="/var/www/apps/shop-example-com",
    built_at="2026-10-17T10:00:00+00:00",
    builder="builder-1",
    noust_version="3.4.0",
    files=12,
    bytes=3456,
)


@pytest.fixture(autouse=True)
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()


def invoke(*args: str) -> Any:
    return CliRunner().invoke(root_cli, ["releases", *args])


class TestBundle:
    @pytest.fixture
    def builds(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def fake(domain: str, destination: Path, **kwargs: Any) -> Bundle:
            calls.append({"domain": domain, "destination": destination, **kwargs})
            return Bundle(destination, DIGEST, 2048, MANIFEST)

        monkeypatch.setattr("noust.cli.commands.releases.build_and_pack", fake)
        return calls

    def test_it_prints_the_checksum_and_the_line_to_type_on_the_other_server(
        self, builds: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        out = tmp_path / "shop.noust-release.tar.gz"

        result = invoke("bundle", "shop.example.com", "-o", str(out))

        assert result.exit_code == 0, result.output
        assert f"Packed release {MANIFEST.release_id} of shop.example.com" in result.output
        assert DIGEST in result.output and "12 files" in result.output
        assert (
            f"noust releases receive shop.example.com {out.name} --sha256 {DIGEST}" in result.output
        )
        assert builds[0]["destination"] == out and builds[0]["keep"] is False

    def test_the_options_reach_the_function(
        self, builds: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        result = invoke(
            "bundle",
            "shop.example.com",
            "-o",
            str(tmp_path / "x.tar.gz"),
            "--commit",
            "abc1234",
            "--keep",
        )

        assert result.exit_code == 0, result.output
        assert (builds[0]["commit"], builds[0]["release_id"], builds[0]["keep"]) == (
            "abc1234",
            None,
            True,
        )
        packed = invoke(
            "bundle", "shop.example.com", "-o", str(tmp_path / "y.tar.gz"), "--release", "r1"
        )
        assert packed.exit_code == 0 and builds[1]["release_id"] == "r1"

    def test_without_a_file_name_it_is_named_by_the_application_and_the_time(
        self, builds: list[dict[str, Any]]
    ) -> None:
        invoke("bundle", "shop.example.com")

        name = builds[0]["destination"].name
        assert name.startswith("shop.example.com-") and name.endswith(bundles.BUNDLE_SUFFIX)

    def test_json_carries_everything_a_script_needs(
        self, builds: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        result = invoke("bundle", "shop.example.com", "-o", str(tmp_path / "x.tar.gz"), "--json")

        shown = json.loads(result.output)
        assert shown["sha256"] == DIGEST and shown["size"] == 2048
        assert shown["manifest"]["release_id"] == MANIFEST.release_id
        assert shown["path"] == str(tmp_path / "x.tar.gz")

    def test_a_rehearsal_builds_nothing(self, builds: list[dict[str, Any]]) -> None:
        result = CliRunner().invoke(
            root_cli, ["--dry-run", "releases", "bundle", "shop.example.com"]
        )

        assert result.exit_code == 0, result.output
        assert "Would build shop.example.com without activating it" in result.output
        assert builds == []


class TestReceive:
    @pytest.fixture
    def receives(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def fake(domain: str, path: Path, **kwargs: Any) -> Reception:
            calls.append({"domain": domain, "path": path, **kwargs})
            return Reception(domain, MANIFEST.release_id, "abc1234", 42, MANIFEST, 0)

        monkeypatch.setattr("noust.cli.commands.releases.receive", fake)
        return calls

    @pytest.fixture
    def file(self, tmp_path: Path) -> Path:
        path = tmp_path / "shop.noust-release.tar.gz"
        path.write_bytes(b"bundle")
        return path

    def test_it_says_where_it_runs_and_how_to_go_back(
        self, receives: list[dict[str, Any]], file: Path
    ) -> None:
        result = invoke("receive", "serve.example.com", str(file), "--sha256", DIGEST)

        assert result.exit_code == 0, result.output
        assert (
            f"serve.example.com runs release {MANIFEST.release_id}, built on builder-1"
            in result.output
        )
        assert "noust releases rollback serve.example.com" in result.output
        assert receives[0]["sha256"] == DIGEST and receives[0]["schema_changed_ok"] is False

    def test_the_checksum_is_required(self, receives: list[dict[str, Any]], file: Path) -> None:
        result = invoke("receive", "serve.example.com", str(file))

        assert result.exit_code != 0 and "--sha256" in result.output
        assert receives == []

    def test_a_sha256_file_is_read_the_way_sha256sum_writes_it(
        self, receives: list[dict[str, Any]], file: Path, tmp_path: Path
    ) -> None:
        sidecar = tmp_path / "shop.sha256"
        sidecar.write_text(f"{DIGEST}  {file.name}\n")

        result = invoke("receive", "serve.example.com", str(file), "--sha256", str(sidecar))

        assert result.exit_code == 0, result.output
        assert receives[0]["sha256"] == DIGEST

    def test_a_digest_is_never_taken_for_a_file(
        self,
        receives: list[dict[str, Any]],
        file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A file named like a digest in the working directory must not replace what was typed.
        (tmp_path / DIGEST).write_text("b" * 64)
        monkeypatch.chdir(tmp_path)

        invoke("receive", "serve.example.com", str(file), "--sha256", DIGEST)

        assert receives[0]["sha256"] == DIGEST

    def test_going_back_past_a_schema_change_is_said_with_a_flag(
        self, receives: list[dict[str, Any]], file: Path
    ) -> None:
        invoke("receive", "serve.example.com", str(file), "--sha256", DIGEST, "--schema-changed-ok")

        assert receives[0]["schema_changed_ok"] is True

    def test_json(self, receives: list[dict[str, Any]], file: Path) -> None:
        result = invoke("receive", "serve.example.com", str(file), "--sha256", DIGEST, "--json")

        shown = json.loads(result.output)
        assert shown["release"] == MANIFEST.release_id and shown["deployment_id"] == 42
        assert shown["manifest"]["builder"] == "builder-1"

    def test_a_file_that_is_not_there(self, receives: list[dict[str, Any]], tmp_path: Path) -> None:
        result = invoke("receive", "serve.example.com", str(tmp_path / "nope"), "--sha256", DIGEST)

        assert result.exit_code != 0 and receives == []

    def test_a_rehearsal_receives_nothing(self, receives: list[dict[str, Any]], file: Path) -> None:
        result = CliRunner().invoke(
            root_cli,
            [
                "--dry-run",
                "releases",
                "receive",
                "serve.example.com",
                str(file),
                "--sha256",
                DIGEST,
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Would check" in result.output and receives == []
