"""
A JavaScript monorepo as one application per workspace (checklist A-53).

The monorepo deployer was a deployer of its own, in place, for a repository shape that the rest
of Noust now handles as several applications of one repository: each with a base directory, its
watch paths and its own releases. These tests pin what turns a monorepo into them: which tool's
workspace globs are read, which workspaces are applications and which are libraries, what a
workspace is told to watch (itself, what it depends on, and the files that change every
workspace), and the document that creates the application.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.source_rules import watch_paths_trigger
from noust.deployers.app_export import plan_import
from noust.deployers.workspaces import detect_workspaces, workspace_document

NEXT = {"name": "web", "dependencies": {"next": "15", "react": "19", "@acme/ui": "workspace:*"}}
API = {
    "name": "@acme/api",
    "scripts": {"start": "node dist/main.js"},
    "dependencies": {"express": "4", "@acme/ui": "workspace:*", "@acme/db": "workspace:*"},
}
UI = {"name": "@acme/ui", "main": "index.js", "dependencies": {"react": "19"}}
DB = {"name": "@acme/db", "main": "index.js", "dependencies": {"@acme/config": "workspace:*"}}
CONFIG = {"name": "@acme/config", "main": "index.js"}


def write_json(root: Path, name: str, data: object) -> None:
    """
    Args:
        root: The repository.
        name: A path inside it.
        data: What the JSON file holds.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def write(root: Path, name: str, text: str = "") -> None:
    """
    Args:
        root: The repository.
        name: A path inside it.
        text: Its content.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def turbo(tmp_path: Path) -> Path:
    """
    Returns:
        A pnpm workspace with Turborepo: two applications and three libraries.
    """
    write_json(
        tmp_path,
        "package.json",
        {"name": "acme", "private": True, "devDependencies": {"turbo": "2"}},
    )
    write(tmp_path, "pnpm-workspace.yaml", "packages:\n  - apps/*\n  - packages/*\n")
    write(tmp_path, "pnpm-lock.yaml", "lockfileVersion: 9\n")
    write_json(tmp_path, "turbo.json", {"tasks": {"build": {}}})
    write_json(tmp_path, "apps/web/package.json", NEXT)
    write_json(tmp_path, "apps/api/package.json", API)
    write_json(tmp_path, "packages/ui/package.json", UI)
    write_json(tmp_path, "packages/db/package.json", DB)
    write_json(tmp_path, "packages/config/package.json", CONFIG)
    return tmp_path


class TestDetection:
    def test_a_turborepo_with_pnpm(self, turbo: Path) -> None:
        report = detect_workspaces(turbo)

        assert report.tool == "turborepo" and report.package_manager == "pnpm"
        assert [w.directory for w in report.workspaces] == [
            "apps/api",
            "apps/web",
            "packages/config",
            "packages/db",
            "packages/ui",
        ]
        assert {w.name for w in report.deployable} == {"web", "@acme/api"}
        web = report.named("web")
        assert web is not None and web.app_type == "nextjs"
        api = report.named("apps/api")
        assert api is not None and api.app_type == "nodejs"

    def test_libraries_say_why_they_are_not_applications(self, turbo: Path) -> None:
        ui = detect_workspaces(turbo).named("@acme/ui")

        assert ui is not None and not ui.deployable
        assert "library" in ui.reason
        assert ui.watch_paths == () and ui.build is None

    def test_a_workspace_watches_itself_what_it_depends_on_and_the_root_files(
        self, turbo: Path
    ) -> None:
        report = detect_workspaces(turbo)
        web, api = report.named("web"), report.named("@acme/api")

        assert web is not None and api is not None
        assert web.watch_paths[:2] == ("apps/web/**", "packages/ui/**")
        assert "pnpm-lock.yaml" in web.watch_paths and "turbo.json" in web.watch_paths
        # Through @acme/db to @acme/config: the whole chain.
        assert {p for p in api.watch_paths if p.endswith("/**")} == {
            "apps/api/**",
            "packages/config/**",
            "packages/db/**",
            "packages/ui/**",
        }

    def test_what_a_push_touches_decides_which_applications_it_deploys(self, turbo: Path) -> None:
        report = detect_workspaces(turbo)
        web, api = report.named("web"), report.named("@acme/api")
        assert web is not None and api is not None

        assert watch_paths_trigger(web.watch_paths, ["apps/web/page.tsx"])
        assert not watch_paths_trigger(web.watch_paths, ["apps/api/main.ts"])
        assert watch_paths_trigger(web.watch_paths, ["packages/ui/button.tsx"])
        assert watch_paths_trigger(api.watch_paths, ["packages/ui/button.tsx"])
        assert not watch_paths_trigger(web.watch_paths, ["packages/db/x.ts"])
        assert watch_paths_trigger(web.watch_paths, ["pnpm-lock.yaml"])
        assert not watch_paths_trigger(web.watch_paths, ["README.md"])

    def test_turborepo_builds_the_workspace_with_what_it_depends_on(self, turbo: Path) -> None:
        web = detect_workspaces(turbo).named("web")

        assert web is not None
        assert web.build == ("pnpm", "exec", "turbo", "run", "build", "--filter=web...")

    @pytest.mark.parametrize(
        ("lock", "expected"),
        [
            ("yarn.lock", ("yarn", "turbo", "run", "build", "--filter=web...")),
            ("package-lock.json", ("npx", "turbo", "run", "build", "--filter=web...")),
            ("bun.lockb", ("bunx", "turbo", "run", "build", "--filter=web...")),
        ],
    )
    def test_the_turbo_command_follows_the_package_manager(
        self, turbo: Path, lock: str, expected: tuple[str, ...]
    ) -> None:
        (turbo / "pnpm-lock.yaml").unlink()
        write(turbo, lock, "")

        web = detect_workspaces(turbo).named("web")

        assert web is not None and web.build == expected

    def test_without_turbo_the_types_own_build_applies(self, turbo: Path) -> None:
        (turbo / "turbo.json").unlink()

        report = detect_workspaces(turbo)

        assert report.tool == "pnpm"
        assert all(workspace.build is None for workspace in report.workspaces)

    def test_npm_and_yarn_workspaces_in_package_json(self, tmp_path: Path) -> None:
        write_json(tmp_path, "package.json", {"workspaces": ["apps/*"]})
        write_json(tmp_path, "apps/site/package.json", NEXT)
        assert detect_workspaces(tmp_path).tool == "npm"

        write(tmp_path, "yarn.lock", "")
        assert detect_workspaces(tmp_path).tool == "yarn"

    def test_the_object_form_of_workspaces(self, tmp_path: Path) -> None:
        write_json(tmp_path, "package.json", {"workspaces": {"packages": ["services/*"]}})
        write_json(tmp_path, "services/api/package.json", API)

        report = detect_workspaces(tmp_path)

        assert [w.directory for w in report.workspaces] == ["services/api"]

    def test_lerna_defaults_to_packages(self, tmp_path: Path) -> None:
        write_json(tmp_path, "lerna.json", {"version": "1.0.0"})
        write_json(tmp_path, "package.json", {"name": "root"})
        write_json(tmp_path, "packages/api/package.json", API)

        report = detect_workspaces(tmp_path)

        assert report.tool == "lerna" and [w.directory for w in report.workspaces] == [
            "packages/api"
        ]

    def test_nx_is_named_when_nx_json_is_there(self, tmp_path: Path) -> None:
        write_json(tmp_path, "package.json", {"workspaces": ["apps/*"]})
        write_json(tmp_path, "nx.json", {})
        write_json(tmp_path, "apps/web/package.json", NEXT)

        assert detect_workspaces(tmp_path).tool == "nx"

    def test_exclusions_and_exact_names(self, tmp_path: Path) -> None:
        write_json(
            tmp_path,
            "package.json",
            {"workspaces": ["packages/*", "!packages/legacy", "tools/cli"]},
        )
        write_json(tmp_path, "packages/api/package.json", API)
        write_json(tmp_path, "packages/legacy/package.json", API | {"name": "legacy"})
        write_json(tmp_path, "tools/cli/package.json", {"name": "cli", "scripts": {"start": "x"}})
        write_json(tmp_path, "tools/cli/fixtures/package.json", {"name": "fixture"})

        report = detect_workspaces(tmp_path)

        assert [w.directory for w in report.workspaces] == ["packages/api", "tools/cli"]

    def test_a_pattern_that_climbs_or_is_absolute_names_nothing(self, tmp_path: Path) -> None:
        write_json(tmp_path, "package.json", {"workspaces": ["../outside", "/etc", "apps/*"]})
        write_json(tmp_path, "apps/web/package.json", NEXT)

        assert [w.directory for w in detect_workspaces(tmp_path).workspaces] == ["apps/web"]

    def test_a_repository_without_workspaces(self, tmp_path: Path) -> None:
        write_json(tmp_path, "package.json", {"name": "single", "scripts": {"start": "x"}})

        report = detect_workspaces(tmp_path)

        assert report.tool == "none" and report.workspaces == ()

    def test_nothing_is_followed_through_a_link_and_a_huge_file_is_not_read(
        self, turbo: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        write_json(outside, "package.json", API | {"name": "leak"})
        (turbo / "apps" / "linked").symlink_to(outside, target_is_directory=True)
        (turbo / "packages" / "big").mkdir()
        (turbo / "packages" / "big" / "package.json").write_text(
            '{"name": "big", "x": "' + "a" * 300_000 + '"}'
        )

        report = detect_workspaces(turbo)

        assert report.named("leak") is None and report.named("apps/linked") is None
        assert any("packages/big/package.json could not be read" in p for p in report.problems)

    def test_a_dependency_cycle_ends(self, tmp_path: Path) -> None:
        write_json(tmp_path, "package.json", {"workspaces": ["packages/*"]})
        write_json(
            tmp_path,
            "packages/a/package.json",
            {"name": "a", "scripts": {"start": "x"}, "dependencies": {"b": "*"}},
        )
        write_json(tmp_path, "packages/b/package.json", {"name": "b", "dependencies": {"a": "*"}})

        a = detect_workspaces(tmp_path).named("a")

        assert a is not None and a.watch_paths[:2] == ("packages/a/**", "packages/b/**")

    def test_a_sparse_checkout_is_read_from_the_listing(self, turbo: Path, tmp_path: Path) -> None:
        sparse = tmp_path / "sparse"
        for name in (
            "package.json",
            "pnpm-workspace.yaml",
            "turbo.json",
            "pnpm-lock.yaml",
            "apps/web/package.json",
            "apps/api/package.json",
            "packages/ui/package.json",
            "packages/db/package.json",
            "packages/config/package.json",
        ):
            write(sparse, name, (turbo / name).read_text())
        listing = [*(f"apps/web/src/{n}.ts" for n in "ab"), "node_modules/x/package.json"]
        listing += [
            "package.json",
            "apps/web/package.json",
            "apps/api/package.json",
            "packages/ui/package.json",
            "packages/db/package.json",
            "packages/config/package.json",
        ]

        report = detect_workspaces(sparse, listing)

        assert {w.name for w in report.deployable} == {"web", "@acme/api"}
        assert report.named("node_modules/x") is None


class TestTheApplicationsItCreates:
    def test_a_document_that_deploys_one_workspace_on_releases(self, turbo: Path) -> None:
        report = detect_workspaces(turbo)
        web = report.named("web")
        assert web is not None

        document = workspace_document(
            report,
            web,
            domain="web.example.com",
            source="https://github.com/acme/mono.git",
            branch="main",
        )

        app = document["app"]
        assert app["domain"] == "web.example.com" and app["layout"] == "releases"
        assert app["app_type"] == "nextjs" and app["branch"] == "main"
        assert "base_directory: apps/web" in app["build"]
        assert "turbo" in app["build"] and "--filter=web..." in app["build"]
        assert document["source_options"]["watch_paths"][:2] == ["apps/web/**", "packages/ui/**"]

    def test_the_document_is_one_the_import_already_plans(self, turbo: Path) -> None:
        report = detect_workspaces(turbo)
        api = report.named("@acme/api")
        assert api is not None

        document = workspace_document(
            report, api, domain="api.example.com", source="https://github.com/acme/mono.git"
        )
        plan = plan_import(document, domain="api.example.com")

        assert plan.create.domain == "api.example.com" and plan.create.layout == "releases"
        assert plan.create.build_document is not None
        assert "base_directory: apps/api" in plan.create.build_document

    def test_a_library_is_not_deployed(self, turbo: Path) -> None:
        report = detect_workspaces(turbo)
        ui = report.named("@acme/ui")
        assert ui is not None

        with pytest.raises(ValidationError, match="not an application"):
            workspace_document(report, ui, domain="ui.example.com", source="x")
