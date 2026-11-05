# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust console sign-in-image``: the picture the sign-in screen shows in place of Noust's drawing.

``set`` takes a PNG, a JPEG or a WebP and, with ``--fit``, says whether it fills the space or is shown
whole; ``--alt`` is what a screen reader says in its place. ``remove`` brings the drawing back and
``show`` says what is there. The work is :class:`noust.core.sign_in_image.SignInImage`, which the
console's API calls too, so what is accepted here is what is accepted there and the same sentences
refuse the rest. This module only parses, asks and prints.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.core.fs import is_rehearsal
from noust.core.sign_in_image import Appearance, SignInImage
from noust.validators import sign_in_image as rules


@click.group("sign-in-image", cls=NoustGroup)
def sign_in_image() -> None:
    """Choose the picture the sign-in screen shows instead of Noust's own drawing."""


def _payload(appearance: Appearance | None) -> dict[str, Any]:
    """
    Describe the picture as JSON.

    Args:
        appearance: What the screen shows, or None.

    Returns:
        ``{"image": null}`` or the picture's details.
    """
    if appearance is None:
        return {"image": None}
    return {
        "image": {
            "sha256": appearance.sha256,
            "content_type": appearance.content_type,
            "width": appearance.width,
            "height": appearance.height,
            "size": appearance.size,
            "fit": appearance.fit,
            "alt": appearance.alt,
            "updated_at": appearance.updated_at,
        }
    }


def _read(path: Path) -> bytes:
    """
    Read the file to set, no further than the limit allows.

    Args:
        path: The file.

    Returns:
        Its bytes, at most one more than the limit: enough for the validator to refuse a file that is
        larger, without reading a file of any size into memory.

    Raises:
        ValidationError: When it cannot be read.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(rules.MAX_IMAGE_BYTES + 1)
    except OSError as exc:
        raise ValidationError(
            f"Cannot read {path}: {exc.strerror or exc}",
            details="Give the path of a PNG, JPEG or WebP file this account can read.",
            field="file",
        ) from exc


def _say(logger: Any, appearance: Appearance) -> None:
    """
    Print what the picture is.

    Args:
        logger: The command's logger.
        appearance: The picture.
    """
    logger.key_value(
        "Picture", f"{appearance.content_type}, {appearance.width} x {appearance.height}"
    )
    logger.key_value("Size", f"{appearance.size} bytes")
    logger.key_value(
        "Fit", "fills the space" if appearance.fit == "cover" else "shown whole in the space"
    )
    logger.key_value("Description", appearance.alt or "none: the picture is decoration")
    logger.key_value("Address", f"sha256 {appearance.sha256}")


@sign_in_image.command("show", read_only=True)
@json_option("Print what is there as JSON.")
@pass_context
def show_command(ctx: Context) -> None:
    """Say which picture the sign-in screen shows, if any, and how it is drawn."""
    current = SignInImage().get()
    if ctx.json_output:
        click.echo(json.dumps(_payload(current)))
        return
    if current is None:
        ctx.logger.info("The sign-in screen shows Noust's own drawing.")
        ctx.logger.info("Put a picture there with: noust console sign-in-image set FILE")
        return
    _say(ctx.logger, current)


@sign_in_image.command("set")
@click.argument(
    "file", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--fit",
    type=click.Choice(rules.FITS),
    help="cover fills the space and crops what does not fit (a photograph); contain shows the "
    "whole picture in it (a logo). contain for a new picture; what is set now when only the "
    "fit or the description changes.",
)
@click.option(
    "--alt",
    metavar="TEXT",
    help=f"What a screen reader says in the picture's place, up to {rules.MAX_ALT_CHARS} characters. "
    "Leave it out and a new picture is decoration; when only the fit changes, the description stays.",
)
@global_flags
@json_option("Print the outcome as JSON.")
@pass_context
def set_command(ctx: Context, file: Path | None, fit: str | None, alt: str | None) -> None:
    """
    Put a picture of your own on the sign-in screen, or change how the one there is drawn.

    FILE is a PNG, a JPEG or a WebP of at most 1 MiB and 4096 pixels on a side; its type is read
    from its bytes, not its name. An SVG is refused (it is code, and the picture is shown to anyone
    who opens the console) and so is an animation. What a camera or an editor left in the file (a
    place, a name, a thumbnail) is removed before it is kept. Without FILE the picture that is there
    stays and only --fit and --alt change. Only the space of the drawing changes: the logo of
    Noust, its sentence and the licence notice stay on the screen.
    """
    manager = SignInImage()
    current = manager.get()
    data = _read(file) if file is not None else None
    chosen_fit = fit or (current.fit if current is not None and data is None else "contain")
    chosen_alt = alt if alt is not None else (current.alt if current and data is None else "")
    result = manager.set(data, fit=chosen_fit, alt=chosen_alt)
    if is_rehearsal():
        ctx.logger.info("Would put this picture on the sign-in screen; nothing was written.")
        return
    if ctx.json_output:
        click.echo(json.dumps(_payload(result)))
        return
    ctx.logger.success("The sign-in screen shows this picture now")
    _say(ctx.logger, result)


@sign_in_image.command("remove")
@global_flags
@json_option("Print the outcome as JSON.")
@pass_context
def remove_command(ctx: Context) -> None:
    """Take the picture off the sign-in screen: Noust's own drawing comes back."""
    removed = SignInImage().remove()
    if ctx.json_output:
        click.echo(json.dumps({"removed": removed is not None}))
        return
    if removed is None:
        ctx.logger.info("There was no picture on the sign-in screen.")
        return
    if is_rehearsal():
        ctx.logger.info("Would take the picture off the sign-in screen; nothing was removed.")
        return
    ctx.logger.success("The sign-in screen shows Noust's own drawing again")
