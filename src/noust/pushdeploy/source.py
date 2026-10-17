# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading an application's push repository: what its repository cache is filled from.

An application deployed with ``git push`` has the source ``push:<domain>``. Everything above the
repository cache treats it as a git source (commits, tags, branches, the instant way back);
this module is the only code that knows where the commits are. The repository is owned by the
push account, which is fed by pushers, so it is read the way an untrusted remote is:

- Questions that only read (which commit is the branch at, which tags are there) are asked **as
  the push account** (``user=``): nothing of root's travels with them, and git's own check of who
  owns a repository is satisfied rather than switched off.
- Filling the cache is root's, because the cache is root's. git is told once, for that one
  command, that the local transport is allowed and that this one repository's owner is known
  (``protocol.file.allow=always``, ``safe.directory``); the fetch carries no hooks and no
  submodules, objects are copied rather than linked, and every object is checked as it arrives
  (``fsck``). Nowhere else is the local transport allowed.
- A push source deploys **its own application only**: the cache it fills has to be the one that
  application owns. A source copied to another application (a clone, a manifest, a typed
  ``push:other.example.com``) finds the door shut, whoever typed it. This is the chokepoint
  (rule 4): the check is here, not in the callers.
"""

from __future__ import annotations

from pathlib import Path

from noust.core import paths, push_store
from noust.core.exceptions import SourceError
from noust.core.fs import FileSystem
from noust.core.logger import Logger
from noust.core.runner import CommandResult, CommandRunner
from noust.core.store import get_store
from noust.deployers.helpers.layout import app_root
from noust.validators.source import push_source_domain

#: The account the repositories belong to.
PUSH_USER = paths.PUSH_USER

#: Deadline of the local git commands this runs: nothing here touches a network.
GIT_TIMEOUT = 300

#: Deadline of a question that only reads refs.
READ_TIMEOUT = 60


def repository_path(domain: str, home: Path | None = None) -> Path:
    """
    Name the bare repository of an application.

    Args:
        domain: The application's domain.
        home: The directory that holds the repositories.

    Returns:
        ``<push dir>/<domain>.git``. The domain is what names it, so it cannot collide with
        another application's: a domain is validated and unique.
    """
    return (home or paths.push_dir()) / f"{domain}.git"


def _git(
    runner: CommandRunner,
    args: list[str],
    *,
    cwd: Path | None = None,
    as_push_account: bool = False,
    timeout: int = GIT_TIMEOUT,
    local_repository: Path | None = None,
) -> CommandResult:
    """
    Run git on a push repository.

    Args:
        runner: The command runner.
        args: Arguments after ``git``.
        cwd: Where it runs.
        as_push_account: Run as the account the repository belongs to (a read).
        timeout: Deadline in seconds.
        local_repository: A repository the command reads over the local transport, which git is
            told to allow for this command alone and whose owner it is told to know.

    Returns:
        The command result.
    """
    prefix = ["git", "-c", "protocol.ext.allow=never"]
    if local_repository is not None:
        prefix += [
            "-c",
            "protocol.file.allow=always",
            "-c",
            f"safe.directory={local_repository}",
            "-c",
            "transfer.fsckObjects=true",
            "-c",
            "core.hooksPath=/dev/null",
        ]
    else:
        prefix += ["-c", "protocol.file.allow=never"]
    env = {"GIT_TERMINAL_PROMPT": "0"}
    if as_push_account:
        # The account has a home that is the repositories' directory and no configuration of
        # its own to read; root's configuration is not carried over.
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(paths.push_dir())})
    return runner.run(
        [*prefix, *args],
        cwd=cwd,
        timeout=timeout,
        user=PUSH_USER if as_push_account else None,
        env=env,
    )


def _require_own_cache(domain: str, cache: Path) -> push_store.PushRecord:
    """
    Refuse to fill a cache that is not the application's own.

    Args:
        domain: The application the source names.
        cache: The cache being filled.

    Returns:
        The application's push record.

    Raises:
        SourceError: The application does not deploy from push, or the cache is another's.
    """
    app = get_store().get_app(domain)
    record = push_store.get(domain)
    if app is None or record is None:
        raise SourceError(
            f"{domain} does not deploy from git push",
            details=f"Turn it on with: noust push enable {domain}",
        )
    own = app_root(app) / "repo"
    if cache.resolve() != own.resolve():
        raise SourceError(
            f"The push repository of {domain} deploys {domain} only",
            details="A source of the form push:<domain> is the application's own. Give another "
            "application its own repository: noust push enable <domain>.",
        )
    return record


def _existing_branch(runner: CommandRunner, domain: str, branch: str) -> str:
    """
    Read the commit a branch of the repository is at.

    Args:
        runner: The command runner.
        domain: The application.
        branch: A validated branch name.

    Returns:
        The full commit id.

    Raises:
        SourceError: Nothing has been pushed to the branch.
    """
    repository = repository_path(domain)
    result = _git(
        runner,
        [
            "--git-dir",
            str(repository),
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}^{{commit}}",
        ],
        as_push_account=True,
        timeout=READ_TIMEOUT,
    )
    commit = result.stdout.strip()
    if not result.success or not commit:
        raise SourceError(
            f"Nothing has been pushed to {branch} of {domain} yet",
            details=f"Push it: git push <remote> {branch}. 'noust push show {domain}' prints "
            "the remote.",
        )
    return commit


def sync_cache(
    source: str,
    cache: Path,
    branch: str | None,
    *,
    runner: CommandRunner,
    fs: FileSystem,
    logger: Logger,
) -> str:
    """
    Bring an application's repository cache up to date with its push repository.

    Args:
        source: ``push:<domain>``.
        cache: The cache to fill: ``<application>/repo``.
        branch: The branch to follow; the one the repository deploys when None.
        runner: The command runner.
        fs: The filesystem the cache is removed through.
        logger: Where progress is said.

    Returns:
        The full id of the commit the cache is at.

    Raises:
        SourceError: The source is not an application's own, nothing was pushed to the branch,
            or git failed.
    """
    from noust.managers.source_manager import validate_git_ref

    domain = push_source_domain(source)
    record = _require_own_cache(domain, cache)
    wanted = validate_git_ref(branch or record.branch)
    repository = repository_path(domain)
    if not repository.is_dir() or repository.is_symlink():
        raise SourceError(
            f"The push repository of {domain} is missing ({repository})",
            details=f"Make it again: noust push enable {domain}",
        )
    tip = _existing_branch(runner, domain, wanted)
    logger.substep(f"Push repository: {repository} ({wanted} at {tip[:7]})")

    origin = ""
    if (cache / ".git").is_dir():
        found = _git(runner, ["remote", "get-url", "origin"], cwd=cache, timeout=READ_TIMEOUT)
        origin = found.stdout.strip() if found.success else ""
    if origin != str(repository):
        # Another source's clone, or a half-made one: the cache is Noust's own to remake.
        if cache.exists():
            fs.remove_tree(cache)
        fs.make_dir(cache.parent, parents=True)
        cloned = _git(
            runner,
            [
                "clone",
                "--no-hardlinks",
                "--no-recurse-submodules",
                "--branch",
                wanted,
                "--",
                str(repository),
                str(cache),
            ],
            local_repository=repository,
        )
        if not cloned.success:
            raise SourceError(
                f"Cloning the push repository of {domain} failed",
                details=(cloned.stderr or cloned.stdout).strip(),
            )
    else:
        fetched = _git(
            runner,
            [
                "fetch",
                "--prune",
                "--force",
                "--no-recurse-submodules",
                "origin",
                "+refs/heads/*:refs/remotes/origin/*",
                "+refs/tags/*:refs/tags/*",
            ],
            cwd=cache,
            local_repository=repository,
        )
        if not fetched.success:
            raise SourceError(
                f"Fetching the push repository of {domain} failed",
                details=(fetched.stderr or fetched.stdout).strip(),
            )
        for step in (
            ["checkout", "--force", "-B", wanted, f"origin/{wanted}"],
            ["reset", "--hard", f"origin/{wanted}"],
        ):
            done = _git(runner, step, cwd=cache)
            if not done.success:
                raise SourceError(
                    f"git {step[0]} of {wanted} failed in the cache of {domain}",
                    details=(done.stderr or done.stdout).strip(),
                )
    head = _git(runner, ["rev-parse", "HEAD"], cwd=cache, timeout=READ_TIMEOUT)
    if not head.success:
        raise SourceError(f"Cannot read the commit of {cache}", details=head.stderr)
    return head.stdout.strip()


def remote_head(source: str, branch: str | None, *, runner: CommandRunner) -> tuple[str, str]:
    """
    Ask which commit a branch of the push repository is at.

    Args:
        source: ``push:<domain>``.
        branch: The branch; the one the repository deploys when None.
        runner: The command runner.

    Returns:
        The branch and the full commit id.

    Raises:
        SourceError: The application does not deploy from push, or nothing is on the branch.
    """
    from noust.managers.source_manager import validate_git_ref

    domain = push_source_domain(source)
    record = push_store.get(domain)
    if record is None:
        raise SourceError(
            f"{domain} does not deploy from git push",
            details=f"Turn it on with: noust push enable {domain}",
        )
    wanted = validate_git_ref(branch or record.branch)
    return wanted, _existing_branch(runner, domain, wanted)


def remote_tags(source: str, *, runner: CommandRunner) -> list[str]:
    """
    List the tags pushed to the repository.

    Args:
        source: ``push:<domain>``.
        runner: The command runner.

    Returns:
        The tag names; empty when there are none.

    Raises:
        SourceError: The application does not deploy from push, or git could not read it.
    """
    domain = push_source_domain(source)
    if push_store.get(domain) is None:
        raise SourceError(
            f"{domain} does not deploy from git push",
            details=f"Turn it on with: noust push enable {domain}",
        )
    result = _git(
        runner,
        [
            "--git-dir",
            str(repository_path(domain)),
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/tags",
        ],
        as_push_account=True,
        timeout=READ_TIMEOUT,
    )
    if not result.success:
        raise SourceError(
            f"Cannot read the tags of the push repository of {domain}",
            details=(result.stderr or result.stdout).strip(),
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]
