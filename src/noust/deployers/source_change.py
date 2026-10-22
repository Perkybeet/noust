# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Changing where an application's code comes from, without creating it again.

A repository moves to another organisation, a team leaves one code host for
another, or an application that was uploaded gets a repository. Recreating the
application for that would lose its deployment history, its variables, its
domain and its database wiring, so the source is changed in place.

The change is two steps and the first one writes nothing. :func:`plan_change`
judges the new address by the rules a deploy applies (the allowed origins, no
credential in the address, a layout that builds releases), asks the remote
whether it can be read and whether the branch exists, and says what the change
does not carry with it: the webhook configured on the old repository, the
history the new one may not have, the previews built from the old one.
:func:`change_source` repeats the judgement under the application's lock and
writes the new source, with the branch and its pin.

Nothing is fetched or built: the repository cache re-points its remote at the
next update (``SourceManager._update_cache``), and the releases built so far
stay on disk, so a rollback still works. The next update builds the new source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import app_lock
from noust.core.ens.origins import check_origin
from noust.core.exceptions import NoustError, ValidationError
from noust.core.repositories import repository_key
from noust.core.store import App, NoustStore, get_store, is_compose_backed
from noust.deployers.helpers.layout import RELEASES
from noust.managers.source_manager import SourceManager, split_url_credentials, validate_git_ref
from noust.validators.domain import validate_domain
from noust.validators.source import (
    is_push_source,
    is_upload_tree,
    upload_source,
    validate_source,
)

__all__ = ["SourcePlan", "change_source", "plan_change"]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourcePlan:
    """
    What changing an application's source would do, decided before anything is written.

    Attributes:
        domain: The application.
        current: The source it deploys from now.
        target: The source it would deploy from, as it is recorded.
        current_branch: The branch recorded now.
        target_branch: The branch it would follow; None for a source that is not git.
        current_kind: ``git``, ``local``, ``archive``, ``upload`` or ``push``.
        target_kind: The same for the new source.
        same_repository: Both are the same git repository spelled differently
            (an ssh address and an https one, a renamed alias).
        head: The commit the branch points at on the new remote, when it was asked.
        checked: Whether the remote was asked.
        pinned: Whether the application has a branch pinned, which then follows the change.
        previews: The previews built from the old source, which are not rebuilt.
        warnings: What the change does not carry with it.
        changed: Whether the source or the branch is different from the recorded one.
    """

    domain: str
    current: str
    target: str
    current_branch: str | None
    target_branch: str | None
    current_kind: str
    target_kind: str
    same_repository: bool
    head: str | None
    checked: bool
    pinned: bool
    previews: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
    changed: bool = True

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as plain data, for the API and ``--json``.
        """
        return {
            "domain": self.domain,
            "current": self.current,
            "target": self.target,
            "current_branch": self.current_branch,
            "target_branch": self.target_branch,
            "current_kind": self.current_kind,
            "target_kind": self.target_kind,
            "same_repository": self.same_repository,
            "head": self.head,
            "checked": self.checked,
            "pinned": self.pinned,
            "previews": list(self.previews),
            "warnings": list(self.warnings),
            "changed": self.changed,
        }


def _kind(source: str) -> str:
    """
    Args:
        source: A source as the store records it.

    Returns:
        ``git``, ``local``, ``archive``, ``upload`` or ``push``.
    """
    if is_push_source(source):
        return "push"
    kind, normalized = validate_source(source)
    return "upload" if kind == "local" and is_upload_tree(normalized) else kind


def _canonical(source: str) -> str:
    """
    Write an uploaded archive's source one way, whether it was recorded as ``upload:<id>`` or
    as the directory that stands for it.

    Args:
        source: A source as the store records it or as it was typed.

    Returns:
        ``upload:<id>`` for an upload; the source unchanged for anything else.
    """
    text = (source or "").strip()
    if is_upload_tree(text):
        return upload_source(Path(text).name)
    return text


def _known(store: NoustStore, domain: str) -> App:
    """
    Args:
        store: The store.
        domain: The application's domain, validated.

    Returns:
        Its row.

    Raises:
        NoustError: Nothing is deployed there.
    """
    app = store.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    return app


def _refuse_unsuitable(app: App) -> None:
    """
    Refuse an application whose source is not this module's to change.

    Args:
        app: The application's row.

    Raises:
        ValidationError: It is a preview, runs as a stack or a container, builds
            in place, or deploys from ``git push``.
    """
    if app.preview_parent:
        raise ValidationError(
            f"{app.domain} is a preview",
            details=f"A preview is built from its pull request; change {app.preview_parent}.",
        )
    if is_compose_backed(app.app_type):
        raise ValidationError(
            f"{app.domain} runs as {app.app_type}, whose source is its own checkout or image",
            details="A Docker Compose stack is brought to its branch where it lives, and a "
            "container application's image is changed with 'noust app container'.",
        )
    if app.layout != RELEASES:
        raise ValidationError(
            f"{app.domain} is not on the release layout",
            details=f"Its tree is a checkout of the old source: noust app migrate {app.domain} "
            "first, then change the source.",
        )
    if app.source and is_push_source(app.source):
        raise ValidationError(
            f"{app.domain} deploys from git push",
            details=f"Turn that off first: noust push disable {app.domain}",
        )


def _clean_target(text: str) -> tuple[str, str]:
    """
    Judge a new source the way a deploy does.

    Args:
        text: The source as typed.

    Returns:
        ``(kind, source)`` as :func:`~noust.validators.source.validate_source`
        classifies and normalises it.

    Raises:
        ValidationError: It is empty, names a credential, or is a push repository.
        SourceError: It is not a source Noust can deploy from, or comes from an
            origin the server does not allow.
    """
    stripped = (text or "").strip()
    if not stripped:
        raise ValidationError("Name the new source", field="source")
    if is_push_source(stripped):
        raise ValidationError(
            "A push repository is turned on for an application, not named as its source",
            details="Use 'noust push enable DOMAIN'.",
            field="source",
        )
    bare, auth = split_url_credentials(stripped)
    if auth:
        raise ValidationError(
            "The new source carries a credential",
            details="Keep passwords and tokens out of the address, where they would be stored "
            "and shown. Give the server a deploy key ('noust app deploy-key') or connect the "
            "GitHub App instead.",
            field="source",
        )
    kind, normalized = validate_source(bare)
    if kind == "local" and not Path(normalized).is_dir():
        raise ValidationError(
            f"{normalized} is not a directory on this server",
            details="A local source is a directory whose files are copied into each release.",
            field="source",
        )
    if kind == "local" and is_upload_tree(normalized):
        # An uploaded archive is a kind of its own for the allowed origins, and is recorded by
        # the identifier it was printed with, not by the directory that stands for it.
        check_origin(normalized, kind="upload")
        return "upload", upload_source(Path(normalized).name)
    check_origin(normalized, kind=kind)
    return kind, normalized


def plan_change(
    domain: str,
    source: str,
    branch: str | None = None,
    *,
    check: bool = True,
    source_manager: SourceManager | None = None,
    store: NoustStore | None = None,
) -> SourcePlan:
    """
    Judge a change of source and say what it would do. Writes nothing.

    Args:
        domain: The application.
        source: The new source: a git address, an archive URL or a directory.
        branch: The branch to follow, for a git source. When left out, the one
            the application follows if the repository is the same, else the
            remote's default branch.
        check: Ask the remote whether it can be read and has the branch. Off,
            the address is judged without any network.
        source_manager: Asks the remote; a default one when None.
        store: The store; the process-wide one when None.

    Returns:
        The plan.

    Raises:
        NoustError: The application is unknown.
        ValidationError: The application cannot change its source, the new one
            is not acceptable, or a branch goes with something that is not git.
        SourceError: The remote cannot be read, or has no such branch.
    """
    require_server_role("Applications")
    store = store or get_store()
    app = _known(store, validate_domain(domain))
    _refuse_unsuitable(app)
    kind, target = _clean_target(source)
    current = app.source or ""
    current_kind = _kind(current) if current else "none"
    pinned = store.get_branch_pin(app.domain) is not None
    follows = getattr(app, "follow_tags", None)
    previews = tuple(
        sorted(other.domain for other in store.list_apps() if other.preview_parent == app.domain)
    )

    warnings: list[str] = []
    same = False
    head: str | None = None
    checked = False
    target_branch: str | None = None
    if kind == "git":
        same = repository_key(current) is not None and repository_key(current) == repository_key(
            target
        )
        wanted = branch
        if wanted is None and same:
            wanted = app.branch
        if wanted is not None:
            wanted = validate_git_ref(wanted)
        if check:
            found = (source_manager or SourceManager()).remote_head(target, wanted)
            head, wanted, checked = found.commit, found.branch, True
        elif wanted is None:
            raise ValidationError(
                "Name the branch",
                details="Without asking the remote there is no way to know its default branch: "
                "pass --branch, or leave the check on.",
                field="branch",
            )
        else:
            warnings.append(
                "The remote was not asked: if it cannot be read, or has no such branch, the "
                "next update fails and the application keeps serving what it serves."
            )
        target_branch = wanted
    else:
        if branch:
            raise ValidationError(
                "A branch goes with a git repository",
                details=f"{target} is not one; leave the branch out.",
                field="branch",
            )
        if follows:
            raise ValidationError(
                f"{app.domain} follows tags ({follows}), which only a git repository has",
                details=f"Stop following them first: noust app follow-tags {app.domain} --off",
            )

    changed = _canonical(target) != _canonical(current) or target_branch != app.branch
    if changed and current_kind == "git" and kind == "git" and not same:
        warnings.append(
            "This is another repository, not the same one at a new address. The releases built "
            "so far stay on disk and can be rolled back to, but 'noust update --commit' and "
            "'Redeploy' on an old deployment only reach commits the new repository has."
        )
    if changed and current_kind == "git" and kind != "git":
        warnings.append(
            "The old repository stops deploying this application: its webhook and "
            "pushes no longer match it."
        )
    if changed and kind == "git" and not same and store.get_webhook_secret(app.domain):
        warnings.append(
            "The webhook of this application is configured on the old repository. Add it to "
            f"the new one: noust webhook show {app.domain}"
        )
    if changed and kind == "git" and follows:
        warnings.append(
            f"It follows tags matching {follows}: the new repository needs tags like that, or "
            "the next update has nothing to deploy."
        )
    if changed and previews:
        warnings.append(
            f"{len(previews)} preview(s) were built from the old source and keep running as "
            f"they are: {', '.join(previews)}. They are not rebuilt from the new one."
        )
    return SourcePlan(
        domain=app.domain,
        current=current,
        target=target,
        current_branch=app.branch,
        target_branch=target_branch,
        current_kind=current_kind,
        target_kind=kind,
        same_repository=same,
        head=head,
        checked=checked,
        pinned=pinned,
        previews=previews,
        warnings=tuple(warnings),
        changed=changed,
    )


def change_source(
    domain: str,
    source: str,
    branch: str | None = None,
    *,
    check: bool = True,
    actor: str = "",
    source_manager: SourceManager | None = None,
    store: NoustStore | None = None,
) -> SourcePlan:
    """
    Make an application deploy from another source.

    The plan is made again first, so what is written is what a plan just before
    would have said. Nothing is fetched and nothing is rebuilt: the next update
    builds the new source, and the release that serves keeps serving until then.

    Args:
        domain: The application.
        source: The new source.
        branch: The branch to follow, for a git source.
        check: Ask the remote first (see :func:`plan_change`).
        actor: Who asked, for the record.
        source_manager: Asks the remote; a default one when None.
        store: The store; the process-wide one when None.

    Returns:
        The plan that was carried out.

    Raises:
        NoustError: The application is unknown.
        ValidationError: See :func:`plan_change`; or the source is already the
            one recorded.
        SourceError: The remote cannot be read, or has no such branch.
        AppBusyError: Another operation is running on the application.
    """
    store = store or get_store()
    plan = plan_change(
        domain, source, branch, check=check, source_manager=source_manager, store=store
    )
    if not plan.changed:
        raise ValidationError(
            f"{plan.domain} already deploys from {plan.target}"
            + (f" on {plan.target_branch}" if plan.target_branch else ""),
            details="There is nothing to change.",
        )
    # Refused while an update runs: it read the source it builds already, and a deploy writes
    # back the whole row it started with.
    with app_lock(plan.domain, "source change"):
        app = _known(store, plan.domain)
        app.source = plan.target
        app.branch = plan.target_branch
        store.update_app(app)
        if plan.target_kind == "git" and plan.pinned:
            store.set_branch_pin(plan.domain, plan.target_branch, by=actor or None)
        elif plan.target_kind != "git":
            store.set_branch_pin(plan.domain, None)
    if plan.current:
        # The archive the old source was, if it was one and nothing else names it any more.
        from noust.managers.source_uploads import SourceUploads

        try:
            SourceUploads(store=store).release(plan.current)
        except OSError as exc:
            _log.warning("The archive %s was not removed: %s", plan.current, exc)
    audit.record(
        "apps.source",
        target=f"app:{plan.domain}",
        details={
            "source": plan.target,
            "previous": plan.current,
            "branch": plan.target_branch,
            "previous_branch": plan.current_branch,
            "by": actor or None,
        },
    )
    return plan
