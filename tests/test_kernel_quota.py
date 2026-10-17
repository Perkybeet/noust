# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's disk limit, given to the kernel as a project quota (checklist B-63).

The commands and what the tools print here were taken from real XFS and ext4 filesystems mounted
with ``prjquota`` (``tests/integration/run.py``, scenario ``kernel_project_quota_stops_the_writes``,
which also proves the kernel then refuses the writes). These tests pin the argv Noust builds and
the rules around it: it never mounts or tunes a filesystem, never touches ``/etc/projects``, and
refuses a site it adopted, a directory it did not make and a filesystem without project quotas.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_disk_store
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, NoustStore, get_store
from noust.managers import app_disk, kernel_quota

DOMAIN = "shop.example.com"
MOUNT = "/var/www/apps"

#: What ``findmnt -no TARGET,FSTYPE,OPTIONS -T`` printed on the real filesystems.
FINDMNT_XFS = "/var/www/apps xfs    rw,relatime,attr2,inode64,logbufs=8,logbsize=32k,prjquota"
FINDMNT_EXT4 = "/var/www/apps ext4   rw,relatime,prjquota"
FINDMNT_PLAIN_EXT4 = "/var/www/apps ext4   rw,relatime"

#: What the report commands printed for a project holding 4 KiB under a 64 MiB hard limit.
XFS_REPORT = """#0                  0          0          0     00 [--------]
#3000000001          4          0      65536     00 [--------]
"""
EXT4_REPORT = """Project,BlockStatus,FileStatus,BlockUsed,BlockSoftLimit,BlockHardLimit,BlockGrace,FileUsed,FileSoftLimit,FileHardLimit,FileGrace
#0,ok,ok,28,0,0,,4,0,0,
#3000000001,ok,ok,4,0,65536,,3,0,0,
"""


@pytest.fixture
def store() -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    yield get_store()
    NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> App:
    """An application Noust made, in a directory Noust made, with a 64 MB limit."""
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True)
    monkeypatch.setattr("noust.deployers.lifecycle.is_in_apps_directory", lambda path: True)
    created = store.create_app(
        App(domain=DOMAIN, app_type="nodejs", layout="releases", app_path=str(root))
    )
    app_disk_store.set_limit(DOMAIN, 64, changed_by="ana", store=store)
    return created


class Scripted(FakeRunner):
    """A runner whose quota commands can be made to fail, by what the command says."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_on: str | None = None

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv
        if (
            self.fail_on is not None
            and args[:1] == ("xfs_quota",)
            and args[3].startswith(self.fail_on)
        ):
            return replace(result, exit_code=1, stderr="xfs_quota: cannot set limits")
        return result


def runner(findmnt: str = FINDMNT_XFS, report: str = XFS_REPORT) -> Iterator[Scripted]:
    fake = Scripted()
    fake.script(["findmnt"], stdout=findmnt + "\n")
    fake.script(["xfs_quota", "-x", "-c", "report -p -N -b -n"], stdout=report)
    fake.script(["repquota"], stdout=report)
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def xfs() -> Iterator[Scripted]:
    yield from runner(FINDMNT_XFS, XFS_REPORT)


@pytest.fixture
def ext4() -> Iterator[Scripted]:
    yield from runner(FINDMNT_EXT4, EXT4_REPORT)


def project(application: App) -> int:
    return kernel_quota.PROJECT_ID_BASE + (application.id or 0)


# -- The commands ----------------------------------------------------------------------------------------


def test_xfs_gets_a_project_on_the_tree_and_a_hard_limit_by_a_number_of_noust_own(
    app: App,
) -> None:
    plan = kernel_quota.commands_for("xfs", MOUNT, Path("/var/www/apps/shop"), 3000000001, 64)

    assert plan["apply"] == [
        ["xfs_quota", "-x", "-c", "project -s -p /var/www/apps/shop 3000000001", MOUNT],
        ["xfs_quota", "-x", "-c", "limit -p bhard=64m 3000000001", MOUNT],
    ]
    assert plan["remove"] == [
        ["xfs_quota", "-x", "-c", "limit -p bsoft=0 bhard=0 3000000001", MOUNT],
        ["xfs_quota", "-x", "-c", "project -C -p /var/www/apps/shop 3000000001", MOUNT],
    ]
    assert plan["limit"] == [plan["apply"][1]]
    assert plan["report"] == [["xfs_quota", "-x", "-c", "report -p -N -b -n", MOUNT]]


def test_ext4_gets_the_project_on_everything_and_the_inherit_flag_on_directories_only(
    app: App,
) -> None:
    plan = kernel_quota.commands_for("ext4", MOUNT, Path("/var/www/apps/shop"), 3000000001, 64)

    find = ["find", "/var/www/apps/shop", "-xdev"]
    assert plan["apply"] == [
        [*find, "!", "-type", "l", "-exec", "chattr", "-p", "3000000001", "{}", "+"],
        [*find, "-type", "d", "-exec", "chattr", "+P", "{}", "+"],
        ["setquota", "-P", "3000000001", "0", "65536", "0", "0", MOUNT],
    ]
    # Links are skipped: chattr reads through one, and a repository is untrusted input.
    assert all("-type" in argv and "l" in argv for argv in plan["apply"][:1])
    assert plan["remove"][0] == ["setquota", "-P", "3000000001", "0", "0", "0", "0", MOUNT]
    assert plan["report"] == [["repquota", "-P", "-n", "-O", "csv", MOUNT]]


def test_no_command_is_a_shell_and_none_names_etc_projects(app: App) -> None:
    for fstype in ("xfs", "ext4"):
        plans = kernel_quota.commands_for(fstype, MOUNT, Path("/var/www/apps/shop"), 3000000001, 64)
        for argv in (argv for plan in plans.values() for argv in plan):
            assert argv[0] not in {"sh", "bash", "mount", "tune2fs", "mkfs.xfs"}
            assert not any("/etc/projects" in part or "/etc/projid" in part for part in argv)


def test_a_filesystem_that_is_neither_is_not_one_it_knows() -> None:
    with pytest.raises(ValidationError, match="not set up on btrfs"):
        kernel_quota.commands_for("btrfs", MOUNT, Path("/x"), 1, 20)


@pytest.mark.parametrize("path", ["/var/www/my apps/shop", "/var/www/apps/shop;rm", "/var/www/a'b"])
def test_xfs_quota_is_not_given_a_path_it_would_read_as_more_than_a_path(path: str) -> None:
    with pytest.raises(ValidationError, match="cannot be given"):
        kernel_quota.commands_for("xfs", MOUNT, Path(path), 1, 20)


def test_the_projects_are_numbers_noust_picks_and_never_one_docker_hands_out() -> None:
    assert kernel_quota.project_id_for(1) == 3000000001
    assert kernel_quota.PROJECT_ID_BASE > 2**31


def test_what_the_tools_report_is_read_as_the_kernel_counts_it() -> None:
    assert kernel_quota.parse_report("xfs", XFS_REPORT, 3000000001) == (4, 65536)
    assert kernel_quota.parse_report("ext4", EXT4_REPORT, 3000000001) == (4, 65536)
    assert kernel_quota.parse_report("xfs", XFS_REPORT, 3000000009) == (None, None)
    assert kernel_quota.parse_report("ext4", "", 3000000001) == (None, None)


# -- Applying it ---------------------------------------------------------------------------------------------


def test_applying_on_xfs_sets_the_project_and_the_limit_and_says_what_the_kernel_holds(
    app: App, xfs: FakeRunner
) -> None:
    state = kernel_quota.apply(app, actor="ana")

    root = Path(app.app_path)
    assert (
        "xfs_quota",
        "-x",
        "-c",
        f"project -s -p {root} {project(app)}",
        MOUNT,
    ) in xfs.calls
    assert ("xfs_quota", "-x", "-c", f"limit -p bhard=64m {project(app)}", MOUNT) in xfs.calls
    assert (state.applied, state.project, state.mount, state.hard_mb) == (
        True,
        project(app),
        MOUNT,
        64,
    )
    record = app_disk_store.get_record(DOMAIN)
    assert record is not None and (record.kernel_project, record.kernel_mount) == (
        project(app),
        MOUNT,
    )


def test_applying_on_ext4_walks_the_tree_before_it_sets_the_limit(
    app: App, ext4: FakeRunner
) -> None:
    kernel_quota.apply(app, actor="ana")

    chattr = [c for c in ext4.calls if "chattr" in c]
    setquota = [c for c in ext4.calls if c[0] == "setquota"]
    assert len(chattr) == 2 and len(setquota) == 1
    assert ext4.calls.index(chattr[1]) < ext4.calls.index(setquota[0])


def test_the_limit_is_what_the_application_has_and_follows_it(app: App, xfs: FakeRunner) -> None:
    kernel_quota.apply(app, actor="ana")
    xfs.calls.clear()

    app_disk.set_disk_limit(app, 64, actor="ana")  # same number: still keeps the kernel in step
    app_disk_store.set_limit(DOMAIN, 100, changed_by="ana")
    state = kernel_quota.refresh_limit(app)

    limit = ("xfs_quota", "-x", "-c", f"limit -p bhard=100m {project(app)}", MOUNT)
    assert limit in xfs.calls
    assert not [c for c in xfs.calls if c[3].startswith("project")]  # no second walk
    assert state.applied


def test_removing_the_limit_takes_the_kernels_off_too(app: App, xfs: FakeRunner) -> None:
    kernel_quota.apply(app, actor="ana")
    xfs.calls.clear()

    app_disk.set_disk_limit(app, None, actor="ana")

    assert ("xfs_quota", "-x", "-c", f"limit -p bsoft=0 bhard=0 {project(app)}", MOUNT) in xfs.calls
    record = app_disk_store.get_record(DOMAIN)
    assert record is None or record.kernel_project is None


def test_taking_it_off_removes_the_limit_before_the_project(app: App, xfs: FakeRunner) -> None:
    kernel_quota.apply(app, actor="ana")
    xfs.calls.clear()

    state = kernel_quota.remove(app, actor="ana")

    argvs = [c[3] for c in xfs.calls if c[0] == "xfs_quota"]
    assert argvs[:2] == [
        f"limit -p bsoft=0 bhard=0 {project(app)}",
        f"project -C -p {app.app_path} {project(app)}",
    ]
    assert not state.applied
    record = app_disk_store.get_record(DOMAIN)
    assert record is not None and record.kernel_project is None


def test_taking_off_what_was_never_applied_does_nothing(app: App, xfs: FakeRunner) -> None:
    assert not kernel_quota.remove(app).applied
    assert [c for c in xfs.calls if c[0] == "xfs_quota"] == []


def test_deleting_the_application_takes_its_quota_off_the_filesystem(
    app: App, xfs: FakeRunner
) -> None:
    from noust.deployers.lifecycle import _forget_kernel_quota

    kernel_quota.apply(app, actor="ana")
    xfs.calls.clear()
    warnings: list[str] = []

    _forget_kernel_quota(app, lambda what, error: warnings.append(what))

    assert warnings == []
    assert any("bhard=0" in c[3] for c in xfs.calls if c[0] == "xfs_quota")


def test_a_quota_that_could_not_be_taken_off_is_a_warning_of_the_deletion(
    app: App, xfs: FakeRunner
) -> None:
    from noust.deployers.lifecycle import _forget_kernel_quota

    kernel_quota.apply(app, actor="ana")
    xfs.fail_on = "limit"
    warnings: list[str] = []

    _forget_kernel_quota(app, lambda what, error: warnings.append(what))

    assert warnings == ["Its kernel disk quota was not taken off"]


def test_the_status_shows_what_the_kernel_counts(app: App, xfs: FakeRunner) -> None:
    kernel_quota.apply(app, actor="ana")

    shown = app_disk.status(app).to_dict()

    assert shown["kernel"] == {
        "applied": True,
        "project": project(app),
        "mount": MOUNT,
        "hard_mb": 64,
        "used_bytes": 4096,
    }


def test_the_status_of_an_application_without_one_asks_the_kernel_nothing(
    app: App, xfs: FakeRunner
) -> None:
    shown = app_disk.status(app).to_dict()

    assert shown["kernel"]["applied"] is False
    assert [c for c in xfs.calls if c[0] in {"xfs_quota", "repquota"}] == []


# -- What it refuses -----------------------------------------------------------------------------------------


def test_without_a_limit_there_is_nothing_to_give_the_kernel(
    app: App, xfs: FakeRunner, store: NoustStore
) -> None:
    app_disk_store.set_limit(DOMAIN, None, changed_by="ana", store=store)

    with pytest.raises(ValidationError, match="no disk limit to give the kernel"):
        kernel_quota.apply(app)

    assert [c for c in xfs.calls if c[0] == "xfs_quota"] == []


@pytest.mark.parametrize(
    ("findmnt", "words"),
    [
        (FINDMNT_PLAIN_EXT4, "ext4: available"),
        ("/var/www/apps btrfs rw,relatime", "btrfs: unsupported"),
        ("/ overlay rw,relatime", "overlay: unsupported"),
    ],
)
def test_a_filesystem_not_mounted_with_project_quotas_is_never_mounted_by_noust(
    app: App, findmnt: str, words: str
) -> None:
    fake = FakeRunner()
    fake.script(["findmnt"], stdout=findmnt + "\n")
    set_runner(fake)
    try:
        with pytest.raises(ValidationError, match="not mounted with project quotas") as refused:
            kernel_quota.apply(app)
    finally:
        set_runner(None)

    assert words in str(refused.value) and "does not mount or tune" in str(refused.value)
    assert {c[0] for c in fake.calls} == {"findmnt"}


def test_a_site_noust_adopted_has_its_files_left_alone(
    app: App, xfs: FakeRunner, store: NoustStore
) -> None:
    adopted = store.get_app(DOMAIN)
    assert adopted is not None
    adopted.app_type = "adopted"

    with pytest.raises(ValidationError, match="its files are yours"):
        kernel_quota.apply(adopted)

    assert [c for c in xfs.calls if c[0] in {"xfs_quota", "find", "chattr"}] == []


def test_a_directory_noust_did_not_make_is_not_given_a_project(
    app: App, xfs: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("noust.deployers.lifecycle.is_in_apps_directory", lambda path: False)

    with pytest.raises(ValidationError, match="outside Noust's apps directory"):
        kernel_quota.apply(app)


def test_a_missing_tool_is_said_with_the_package_that_has_it(app: App, store: NoustStore) -> None:
    fake = FakeRunner().only_knows("findmnt")
    fake.script(["findmnt"], stdout=FINDMNT_XFS + "\n")
    set_runner(fake)
    try:
        with pytest.raises(DeploymentError, match="xfs_quota is not installed") as refused:
            kernel_quota.apply(app)
    finally:
        set_runner(None)

    assert "xfsprogs" in str(refused.value)


def test_a_command_that_fails_stops_the_plan_with_the_tools_own_words(
    app: App, xfs: FakeRunner
) -> None:
    xfs.fail_on = "limit"

    with pytest.raises(DeploymentError, match="xfs_quota failed") as refused:
        kernel_quota.apply(app)

    assert refused.value.output == "xfs_quota: cannot set limits"
    record = app_disk_store.get_record(DOMAIN)
    assert record is not None and record.kernel_project is None  # not recorded as applied


def test_a_kernel_that_does_not_report_the_limit_afterwards_is_not_taken_at_its_word(
    app: App,
) -> None:
    fake = FakeRunner()
    fake.script(["findmnt"], stdout=FINDMNT_XFS + "\n")
    fake.script(["xfs_quota", "-x", "-c", "report"], stdout="#0  0  0  0  00 [--------]\n")
    set_runner(fake)
    try:
        with pytest.raises(DeploymentError, match="does not report the limit"):
            kernel_quota.apply(app)
    finally:
        set_runner(None)
