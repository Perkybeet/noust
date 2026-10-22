# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The CI security tooling and the signed release are workflow files, and a
workflow file that silently loses a step is a guarantee that quietly stops
holding (checklist L-29, L-31). These tests read the workflows as data and pin
the properties the security model (docs/SECURITY-MODEL.md) promises: the
analysers exist, they have the least privilege they need, the scanners are
pinned by commit, and a release signs and attests what it publishes.

Nothing here runs a workflow; Actions is the only place that can.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

#: A commit, not a tag: a tag can be moved after the fact.
SHA = re.compile(r"^[0-9a-f]{40}$")

#: Actions whose compromise would hand an attacker the build or its scanners
#: (the Trivy action's tags were rewritten once). Pinned by commit wherever
#: they appear.
PINNED_BY_COMMIT = (
    "aquasecurity/trivy-action",
    "gitleaks/gitleaks-action",
    "github/codeql-action",
    "actions/attest-build-provenance",
    "actions/attest-sbom",
    "anchore/sbom-action",
    "sigstore/cosign-installer",
    "actions/dependency-review-action",
)


def load(name: str) -> dict[str, Any]:
    """Read one workflow; YAML 1.1 reads the key ``on`` as ``True``."""
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    if True in data:
        data["on"] = data.pop(True)
    return data


def steps_of(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps", []))


def uses_of(workflow: dict[str, Any]) -> list[str]:
    """Every ``uses:`` of every step, in the form ``owner/repo[/path]@ref``."""
    return [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in steps_of(job)
        if isinstance(step.get("uses"), str)
    ]


def step_using(job: dict[str, Any], action: str) -> dict[str, Any]:
    for step in steps_of(job):
        if str(step.get("uses", "")).startswith(action + "@"):
            return step
    raise AssertionError(f"no step uses {action}")


def run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step["run"]) for step in steps_of(job) if "run" in step)


@pytest.fixture(scope="module")
def security() -> dict[str, Any]:
    return load("security.yml")


@pytest.fixture(scope="module")
def release() -> dict[str, Any]:
    return load("release.yml")


class TestTheSecurityWorkflow:
    def test_it_runs_on_changes_on_a_schedule_and_on_demand(self, security: dict[str, Any]) -> None:
        triggers = security["on"]

        assert {"push", "pull_request", "schedule", "workflow_dispatch"} <= set(triggers)

    def test_it_holds_no_write_permission_unless_a_job_asks_for_it(
        self, security: dict[str, Any]
    ) -> None:
        assert security["permissions"] == {"contents": "read"}
        for name, job in security["jobs"].items():
            granted = job.get("permissions", {})
            extra = {key: value for key, value in granted.items() if value == "write"}
            assert set(extra) <= {"security-events"}, f"{name} asks for {extra}"

    def test_codeql_reads_the_python_and_the_typescript(self, security: dict[str, Any]) -> None:
        job = security["jobs"]["codeql"]

        assert set(job["strategy"]["matrix"]["language"]) == {
            "python",
            "javascript-typescript",
        }
        init = step_using(job, "github/codeql-action/init")
        assert init["with"]["queries"] == "security-extended"
        assert job["permissions"]["security-events"] == "write"

    def test_gitleaks_reads_the_whole_history_with_the_repository_config(
        self, security: dict[str, Any]
    ) -> None:
        job = security["jobs"]["gitleaks"]

        checkout = step_using(job, "actions/checkout")
        assert checkout["with"]["fetch-depth"] == 0
        scan = step_using(job, "gitleaks/gitleaks-action")
        assert scan["env"]["GITLEAKS_CONFIG"] == ".gitleaks.toml"
        assert (ROOT / ".gitleaks.toml").is_file()

    def test_trivy_scans_the_image_built_the_way_the_release_builds_it(
        self, security: dict[str, Any]
    ) -> None:
        job = security["jobs"]["trivy-image"]

        assert "packaging/container" in run_text(job)
        scan = step_using(job, "aquasecurity/trivy-action")
        assert scan["with"]["image-ref"].startswith("noust-central:")
        # Reports to code scanning: what an advisory database changes overnight
        # must not stop an unrelated change from merging.
        assert str(scan["with"]["exit-code"]) == "0"
        assert scan["with"]["ignore-unfixed"] is True

    def test_a_pull_request_adding_a_vulnerable_dependency_is_refused(
        self, security: dict[str, Any]
    ) -> None:
        job = security["jobs"]["dependency-review"]

        assert job["if"] == "github.event_name == 'pull_request'"
        assert step_using(job, "actions/dependency-review-action")["with"]["fail-on-severity"] == (
            "high"
        )

    def test_nothing_from_the_event_reaches_a_shell(self, security: dict[str, Any]) -> None:
        # A branch name or a pull request title in a `run:` is a command.
        for name, job in security["jobs"].items():
            assert "${{ github.event." not in run_text(job), name
            assert "${{ inputs." not in run_text(job), name

    def test_it_never_runs_a_fork_with_the_repositorys_token(
        self, security: dict[str, Any]
    ) -> None:
        text = (WORKFLOWS / "security.yml").read_text(encoding="utf-8")

        assert "pull_request_target" not in text

    def test_the_configuration_files_it_names_exist(self) -> None:
        assert (ROOT / ".github" / "codeql" / "codeql-config.yml").is_file()
        assert (ROOT / ".trivyignore").is_file()


class TestPinning:
    @pytest.mark.parametrize("name", ["security.yml", "release.yml", "ci.yml", "supply-chain.yml"])
    def test_the_actions_that_guard_the_build_are_pinned_by_commit(self, name: str) -> None:
        for use in uses_of(load(name)):
            action, _, ref = use.partition("@")
            if any(
                action == pinned or action.startswith(pinned + "/") for pinned in PINNED_BY_COMMIT
            ):
                assert SHA.match(ref), f"{name}: {use} is pinned by a tag, not a commit"

    def test_dependabot_keeps_the_pins_current(self) -> None:
        config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))

        ecosystems = {entry["package-ecosystem"] for entry in config["updates"]}
        assert "github-actions" in ecosystems


class TestTheReleaseIsSignedAndAttested:
    def test_a_release_with_a_fixable_critical_vulnerability_in_the_image_does_not_ship(
        self, release: dict[str, Any]
    ) -> None:
        gate = release["jobs"]["container-image"]
        scan = step_using(gate, "aquasecurity/trivy-action")

        assert scan["with"]["severity"] == "CRITICAL"
        assert str(scan["with"]["exit-code"]) == "1"
        assert scan["with"]["ignore-unfixed"] is True
        assert "container-image" in release["jobs"]["release"]["needs"]

    def test_the_image_is_signed_by_digest_and_attested(self, release: dict[str, Any]) -> None:
        job = release["jobs"]["publish-container"]

        assert job["permissions"]["id-token"] == "write"
        assert job["permissions"]["attestations"] == "write"
        push = next(step for step in steps_of(job) if step.get("id") == "push")
        assert push["uses"].startswith("docker/build-push-action@")
        signing = "\n".join(
            str(step["run"]) for step in steps_of(job) if "cosign sign " in str(step.get("run", ""))
        )
        assert "@${DIGEST}" in signing and "steps.push.outputs.digest" in str(
            [s for s in steps_of(job) if "cosign sign " in str(s.get("run", ""))]
        )
        provenance = step_using(job, "actions/attest-build-provenance")
        assert provenance["with"]["subject-digest"] == "${{ steps.push.outputs.digest }}"
        assert provenance["with"]["push-to-registry"] is True
        sbom = step_using(job, "actions/attest-sbom")
        assert sbom["with"]["subject-digest"] == "${{ steps.push.outputs.digest }}"

    def test_the_image_signature_is_checked_before_the_job_ends(
        self, release: dict[str, Any]
    ) -> None:
        text = run_text(release["jobs"]["publish-container"])

        assert "cosign verify" in text
        assert "gh attestation verify" in text
        assert "release\\.yml@refs/" in text

    def test_every_file_the_release_carries_is_checksummed_signed_and_attested(
        self, release: dict[str, Any]
    ) -> None:
        job = release["jobs"]["attest-assets"]

        # After every job that attaches a file, or a file would go unsigned.
        assert {"release", "build-sdist", "build-deb", "installer"} <= set(job["needs"])
        assert job["permissions"] == {
            "contents": "write",
            "id-token": "write",
            "attestations": "write",
        }
        text = run_text(job)
        for pattern in ("*.tar.gz", "*.whl", "*.deb", "*.cdx.json", "install.sh"):
            assert pattern in text, pattern
        assert "sha256sum" in text and "cosign sign-blob" in text
        assert "cosign verify-blob" in text and "gh attestation verify" in text
        assert step_using(job, "actions/attest-build-provenance")["with"]["subject-path"] == (
            "release-files/*"
        )
        assert step_using(job, "actions/attest-sbom")["with"]["subject-path"] == (
            "release-files/*.whl"
        )

    def test_the_signature_files_are_attached_to_the_release(self, release: dict[str, Any]) -> None:
        upload = step_using(release["jobs"]["attest-assets"], "softprops/action-gh-release")

        files = upload["with"]["files"]
        for suffix in (".SHA256SUMS", ".SHA256SUMS.sig", ".SHA256SUMS.pem"):
            assert suffix in files

    def test_the_installers_own_checksums_keep_their_name(self, release: dict[str, Any]) -> None:
        # docs/PLATFORMS.md tells people to verify install.sh with a file called
        # SHA256SUMS; the release-wide checksums must not take that name over.
        job = release["jobs"]["installer"]

        assert (
            "dist-installer/SHA256SUMS"
            in step_using(job, "softprops/action-gh-release")["with"]["files"]
        )
        assert "noust-${VERSION}.SHA256SUMS" in run_text(release["jobs"]["attest-assets"])

    def test_pypi_publishes_with_attestations(self, release: dict[str, Any]) -> None:
        job = release["jobs"]["publish-pypi"]

        publish = next(step for step in steps_of(job) if step.get("name") == "Publish noust")
        assert publish["with"]["attestations"] is True
        assert job["permissions"]["id-token"] == "write"

    def test_only_the_jobs_that_sign_hold_the_identity_token(self, release: dict[str, Any]) -> None:
        assert release["permissions"] == {"contents": "read"}
        holders = {
            name
            for name, job in release["jobs"].items()
            if isinstance(job.get("permissions"), dict)
            and job["permissions"].get("id-token") == "write"
        }

        assert holders == {"installer", "attest-assets", "publish-pypi", "publish-container"}


class TestASecurityReleaseSaysSo:
    def test_a_security_bullet_puts_the_marker_the_update_check_reads_in_the_title(
        self, release: dict[str, Any]
    ) -> None:
        job = release["jobs"]["release"]
        changelog = next(s for s in steps_of(job) if s.get("id") == "changelog")
        create = step_using(job, "softprops/action-gh-release")

        # `scripts/release.py -m "SECURITY: ..."` is how a maintainer asks for it.
        assert "^\\* SECURITY:" in changelog["run"]
        assert "(security release)" in changelog["run"]
        assert create["with"]["name"].endswith("${{ steps.changelog.outputs.title_suffix }}")

    def test_the_marker_is_the_one_the_installation_looks_for(
        self, release: dict[str, Any]
    ) -> None:
        from noust.core import package_index

        run = next(s for s in steps_of(release["jobs"]["release"]) if s.get("id") == "changelog")[
            "run"
        ]
        suffix = re.search(r'title_suffix=(\(security release\)|[^"]*\(security release\))', run)

        assert suffix is not None
        assert package_index.is_security_release(f"Noust v1.0.0{suffix.group(1)}")
