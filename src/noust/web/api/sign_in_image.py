# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The picture on the sign-in screen, over HTTP: ``/api/auth/sign-in``.

A thin client of :class:`noust.core.sign_in_image.SignInImage` (rule 3): what a picture may be, how it
is rewritten and where it is kept are the manager's and the validator's, not repeated here. This
module says who may ask for what.

- ``GET /appearance`` and ``GET /image`` are **public**, because the sign-in screen is drawn before
  there is a session. The first says what the screen needs (nothing when there is no picture, and
  then the console draws the noust it always did) and the second is the picture, addressed by the
  digest the first one gave: ``?v=<sha256>`` makes it cacheable for a year, because a different
  picture is a different address, and an address that is not the current picture's is a 404, never the
  new picture under an old name. It is served with the type that was judged from its bytes (never one
  the uploader said), ``nosniff`` and the console's own policy, from the console's own address.
- ``PUT /appearance`` and ``DELETE /appearance`` change it. They are the sign-in's own settings
  (``security.manage``, like the line and the notice an operator shows before sign-in), ask for sudo
  mode, and are audited by the manager. The picture travels as base64 in the JSON body, and the body of
  this one route is the only one allowed to be larger than the API's mebibyte, as large as a file of the
  limit makes it (:func:`noust.web.server.SecurityMiddleware._body_limit`).

Under ``/api/auth`` like the rest of what the sign-in is made of, so a central never forwards it to a
node and a fleet token never reaches it (``noust.web.auth.FLEET_REFUSED_PREFIXES``): what a server
shows before anyone signs in is that server's own decision, as its ``auth.login_label`` is.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from noust.core.sign_in_image import IMAGE_PATH, Appearance, SignInImage
from noust.core.teams import ResourceNotFoundError
from noust.validators import sign_in_image as rules
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.permissions.principal import actor_of

router = APIRouter(route_class=NoustErrorRoute)

#: The types a picture can be served as; the response documents them.
_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")


class SignInImageLimits(BaseModel):
    """
    What a picture may be, so a client need not hard-code it.

    Attributes:
        bytes: The most a file weighs, as it is sent.
        side: The most pixels on a side.
        alt_chars: The most characters of its description.
        types: The types a file may be, by what its bytes are.
    """

    bytes: int = rules.MAX_IMAGE_BYTES
    side: int = rules.MAX_SIDE
    alt_chars: int = rules.MAX_ALT_CHARS
    types: list[str] = Field(default_factory=lambda: list(_IMAGE_TYPES))


class SignInImageOut(BaseModel):
    """
    The picture the sign-in screen shows.

    Attributes:
        url: Where it is, with its digest in ``v`` so it can be cached for good.
        sha256: The digest of the file served.
        content_type: What it is: ``image/png``, ``image/jpeg`` or ``image/webp``.
        width: Pixels across.
        height: Pixels down.
        size: Bytes of the file served.
        fit: ``cover`` fills the space and crops; ``contain`` shows the whole picture in it.
        alt: What a screen reader says in its place; empty for decoration.
        updated_at: When it was set, ISO 8601 in UTC.
    """

    url: str
    sha256: str
    content_type: str
    width: int
    height: int
    size: int
    fit: str
    alt: str
    updated_at: str

    @classmethod
    def of(cls, appearance: Appearance) -> SignInImageOut:
        """
        Args:
            appearance: What the manager keeps.

        Returns:
            Its answer.
        """
        return cls(
            url=f"{IMAGE_PATH}?v={appearance.sha256}",
            sha256=appearance.sha256,
            content_type=appearance.content_type,
            width=appearance.width,
            height=appearance.height,
            size=appearance.size,
            fit=appearance.fit,
            alt=appearance.alt,
            updated_at=appearance.updated_at,
        )


class SignInAppearance(BaseModel):
    """
    What the sign-in screen needs to draw itself.

    Attributes:
        image: The picture an operator chose; null when there is none and the console draws its
            own.
        limits: What a picture may be.
    """

    image: SignInImageOut | None = None
    limits: SignInImageLimits = Field(default_factory=SignInImageLimits)


class SignInAppearanceBody(BaseModel):
    """
    What the sign-in picture is set to.

    Attributes:
        image: The file, as base64 text. Left out, the picture that is there stays and only its
            fit and description change.
        fit: ``cover`` to fill the space, ``contain`` to show the whole picture.
        alt: What a screen reader says in its place, up to 120 characters; empty or left out says
            the picture is decoration.
    """

    image: str | None = None
    fit: str = "contain"
    alt: str | None = None


def _answer() -> SignInAppearance:
    """
    Returns:
        What the screen shows now.
    """
    found = SignInImage().get()
    return SignInAppearance(image=SignInImageOut.of(found) if found is not None else None)


@router.get("/appearance", response_model=SignInAppearance)
def get_appearance() -> SignInAppearance:
    """
    Say what the sign-in screen draws in place of Noust's own drawing, if anything.

    Public: the screen is drawn before there is a session, so this carries nothing but what is shown
    on it.

    Returns:
        The picture and how it is fitted, or ``image: null``; and the limits a picture is held to.
    """
    return _answer()


@router.get(
    "/image",
    response_class=Response,
    responses={
        200: {"description": "The picture.", "content": {kind: {} for kind in _IMAGE_TYPES}}
    },
)
def get_image(request: Request, v: Annotated[str | None, Query()] = None) -> Response:
    """
    Serve the picture of the sign-in screen.

    Public, like the screen it is on. With ``v`` set to its digest it is cacheable for a year; the
    digest of anything but the current picture is a 404.

    Args:
        request: The incoming request, for an ETag the client already has.
        v: The digest the picture is addressed by, from ``GET /appearance``. Without it the current
            picture is served and not cached.

    Returns:
        The file, with the type its bytes were judged to be.

    Raises:
        ResourceNotFoundError: 404 when there is no picture, or ``v`` is not the current one's digest.
    """
    manager = SignInImage()
    found = manager.get()
    stored = manager.read(found.sha256) if found is not None else None
    # A digest that is not the current one's (an old page, a made-up one) is never answered with the
    # current picture: the address is cached for a year.
    if found is None or stored is None or (v is not None and v != found.sha256):
        raise ResourceNotFoundError(
            "There is no such sign-in image",
            details="Read GET /api/auth/sign-in/appearance for the current one.",
            field="v",
        )
    data, content_type = stored
    etag = f'"{found.sha256}"'
    headers = {"ETag": etag, "Content-Disposition": "inline"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=content_type, headers=headers)


@router.put("/appearance", response_model=SignInAppearance)
def put_appearance(
    body: SignInAppearanceBody, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> SignInAppearance:
    """
    Put a picture on the sign-in screen, or change how the one there is drawn.

    The picture is judged by its bytes and rewritten without its metadata before it is kept; see
    :mod:`noust.validators.sign_in_image`. Asks for sudo mode.

    Args:
        body: The picture as base64 (left out to keep the one there), its fit and its description.
        session: The authenticated session in sudo mode.

    Returns:
        What the screen shows now.

    Raises:
        ValidationError: 400, with a code the console translates, when the file is not a PNG, JPEG
            or WebP within the limits, is damaged or animated, when the fit or the description is not
            valid, or when no picture is sent and there is none to change.
    """
    data = rules.decode_upload(body.image) if body.image is not None else None
    SignInImage().set(data, fit=body.fit, alt=body.alt, actor=actor_of(session))
    return _answer()


@router.delete("/appearance", response_model=SignInAppearance)
def delete_appearance(
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> SignInAppearance:
    """
    Take the picture off the sign-in screen: Noust's own drawing comes back.

    Asks for sudo mode. Removing what is not there is not an error.

    Args:
        session: The authenticated session in sudo mode.

    Returns:
        What the screen shows now: no picture.
    """
    SignInImage().remove(actor=actor_of(session))
    return _answer()
