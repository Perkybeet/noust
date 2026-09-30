# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as an email: a subject, headers, a text part and an HTML part.

**The text part is its own layout**, not the chat text: the same state line,
summary, facts, excerpt and address, and a footer saying who sent it, when
and where to change what is received. RFC 2046 wants the preferred part last,
so the transport puts it first and the HTML after.

**The HTML is tables and inline CSS.** Gmail ignores ``<style>`` in some
accounts and Outlook on Windows lays out with Word, so everything essential is
in ``style=`` attributes on ``<table>`` cells at 600 pixels; a ``<style>``
block only adds dark mode (``prefers-color-scheme``) on top, with the console's
dark tokens, and never depends on pure black or white so a client that inverts
colours on its own still reads it. The card has a bar in the state's colour, a
state line (glyph, colour and word: three ways), the subject, the summary, the
facts, the excerpt in a sunken block, and a button - a bordered cell with an
anchor, which Outlook honours. The address is written only on the button (the
text part has it in full). There are no remote images: the wordmark is a PNG
in mid grey that reads on white and on ``#111``, attached inline and
referenced as ``cid:``.

**Headers**: ``Auto-Submitted: auto-generated`` and ``X-Auto-Response-Suppress``
tell auto-responders to stay quiet; ``X-Noust-Event``, ``-Code`` and
``-Server`` are for mail rules. The subject is
``[Noust] <state>: <subject> (<server>)``: state first, the ``[Noust]`` prefix
kept for existing filters, no glyph (a subject is plain).
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

from noust.core.messages import message
from noust.core.notifications.model import STATE_COLORS, Excerpt, Fact, Notification, State
from noust.core.notifications.render.common import (
    clip,
    detail_facts,
    footer_line,
    http_url,
    omitted_marker,
    render_text,
)

logger = logging.getLogger(__name__)

#: The content id the HTML refers to the wordmark by.
WORDMARK_CID = "noust-wordmark"

#: Displayed size of the wordmark; the PNG is twice that, for dense screens.
WORDMARK_WIDTH = 155
WORDMARK_HEIGHT = 22

_SUBJECT_LIMIT = 200
_VALUE_LIMIT = 600

_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

# The console's tokens (panel/src/styles/tokens.css), light and dark. Nothing
# here is pure black or pure white on purpose.
_LIGHT = {
    "page": "#f7f7f7",
    "card": "#ffffff",
    "line": "#e3e3e3",
    "text": "#161616",
    "muted": "#555555",
    "code": "#f0f0f0",
    "button": "#6a45d5",
}
_DARK = {
    "page": "#111111",
    "card": "#171717",
    "line": "#2a2a2a",
    "text": "#ededed",
    "muted": "#a3a3a3",
    "code": "#0b0b0b",
    "button": "#7152de",
}


@dataclass(frozen=True)
class InlineImage:
    """
    An image attached to the message and referenced from its HTML.

    Attributes:
        cid: The content id; the HTML says ``cid:<cid>``.
        data: The image bytes.
        subtype: The MIME subtype (``png``).
        filename: The name mail clients show if they list attachments.
    """

    cid: str
    data: bytes
    subtype: str
    filename: str


@dataclass(frozen=True)
class RenderedEmail:
    """
    Everything a transport needs to send one notification.

    Attributes:
        subject: The subject line, one line of plain text.
        text: The text part.
        html: The HTML part.
        headers: Extra headers (``Auto-Submitted``, ``X-Noust-*``).
        images: Inline images the HTML refers to by ``cid:``.
        message_id: A unique local part for ``Message-ID``: the notification's
            own id, so a delivery and its webhook twin share one.
    """

    subject: str
    text: str
    html: str
    headers: dict[str, str]
    images: tuple[InlineImage, ...]
    message_id: str


@lru_cache(maxsize=1)
def wordmark() -> InlineImage | None:
    """
    Load the wordmark PNG shipped with the package.

    Returns:
        The image, or None when the resource is missing from a broken
        install: the message then carries the word ``noust`` as text instead,
        and the problem is logged once.
    """
    try:
        data = files("noust.core.notifications").joinpath("assets/noust-wordmark.png").read_bytes()
    except OSError as exc:
        logger.warning("The email wordmark could not be read; sending without it: %s", exc)
        return None
    return InlineImage(WORDMARK_CID, data, "png", "noust.png")


def _e(text: str) -> str:
    """
    Args:
        text: Text from anywhere.

    Returns:
        The text escaped for HTML, quotes included.
    """
    return html.escape(text, quote=True)


def subject_line(n: Notification) -> str:
    """
    Args:
        n: The notification.

    Returns:
        ``[Noust] Rolled back: shop.example.com (web-1)``.
    """
    what = f"{n.title}: {n.subject}" if n.subject else n.title
    return clip(f"[Noust] {what} ({n.server})", _SUBJECT_LIMIT)


def _fact_row(fact: Fact) -> str:
    """
    Args:
        fact: A labelled value.

    Returns:
        A table row: muted label, value.
    """
    mono = f"font-family:{_MONO};font-size:13px;" if fact.mono else ""
    return (
        f'<tr><td class="mut" style="padding:4px 16px 4px 0;color:{_LIGHT["muted"]};'
        f'font-size:14px;line-height:20px;white-space:nowrap;vertical-align:top;">'
        f"{_e(clip(fact.label, 60))}</td>"
        f'<td class="txt" style="padding:4px 0;color:{_LIGHT["text"]};font-size:14px;'
        f'line-height:20px;vertical-align:top;word-break:break-word;{mono}">'
        f"{_e(clip(fact.value, _VALUE_LIMIT))}</td></tr>"
    )


def _excerpt_html(excerpt: Excerpt, n: Notification) -> str:
    """
    Args:
        excerpt: The excerpt.
        n: Its notification, for the language.

    Returns:
        The label and the sunken code block.
    """
    body = "\n".join(_e(line) for line in excerpt.lines)
    marker = omitted_marker(excerpt, n)
    if marker:
        body += "\n" + _e(f"… {marker}")
    label = (
        f'<p class="mut" style="margin:20px 0 6px;color:{_LIGHT["muted"]};font-size:13px;'
        f'line-height:18px;">{_e(clip(excerpt.label, 200))}</p>'
        if excerpt.label
        else '<div style="height:20px;line-height:20px;font-size:0;">&nbsp;</div>'
    )
    return (
        f"{label}"
        f'<pre class="code" style="margin:0;padding:12px 14px;background-color:{_LIGHT["code"]};'
        f"color:{_LIGHT['text']};border:1px solid {_LIGHT['line']};border-radius:6px;"
        f"font-family:{_MONO};font-size:12px;line-height:18px;white-space:pre-wrap;"
        f'word-break:break-word;overflow-wrap:anywhere;">{body}</pre>'
    )


def _section_html(heading: str, rows: tuple[Fact, ...], state: State | None) -> str:
    """
    Args:
        heading: A report entry's title.
        rows: Its facts.
        state: Its severity, when it has one.

    Returns:
        A block: coloured heading, facts.
    """
    colour = STATE_COLORS[state].light if state is not None else _LIGHT["text"]
    table = "".join(_fact_row(fact) for fact in rows if fact.value)
    css_class = f"sec sec-{state.value}" if state is not None else "sec txt"
    return (
        f'<p class="{css_class}" style="margin:20px 0 4px;color:{colour};font-size:14px;'
        f'font-weight:700;line-height:20px;">{_e(clip(heading, 200))}</p>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">{table}</table>'
    )


def _button(n: Notification) -> str:
    """
    Args:
        n: The notification.

    Returns:
        The console button, or nothing when there is no web link.
    """
    link = n.console_link
    if link is None or not http_url(link.url):
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:24px 0 0;"><tr>'
        f'<td bgcolor="{_LIGHT["button"]}" class="btn" '
        f'style="background-color:{_LIGHT["button"]};border-radius:6px;">'
        f'<a href="{_e(link.url)}" style="display:inline-block;padding:10px 18px;'
        f'color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;border-radius:6px;">'
        f"{_e(clip(link.label, 60))}</a></td></tr></table>"
    )


def _logo() -> str:
    """
    Returns:
        The wordmark image, or the word when it could not be loaded.
    """
    image = wordmark()
    if image is None:
        return (
            f'<span class="txt" style="font-size:20px;font-weight:700;color:{_LIGHT["text"]};">'
            "noust</span>"
        )
    return (
        f'<img src="cid:{image.cid}" width="{WORDMARK_WIDTH}" height="{WORDMARK_HEIGHT}" '
        'alt="noust" style="display:block;border:0;outline:none;text-decoration:none;">'
    )


def _dark_css(n: Notification) -> str:
    """
    Args:
        n: The notification.

    Returns:
        The dark-mode rules: the console's dark tokens.
    """
    state = STATE_COLORS[n.state].dark
    return (
        ":root { color-scheme: light dark; supported-color-schemes: light dark; }\n"
        "@media (prefers-color-scheme: dark) {\n"
        f"  body, .bg {{ background-color:{_DARK['page']} !important; }}\n"
        f"  .card {{ background-color:{_DARK['card']} !important; "
        f"border-color:{_DARK['line']} !important; }}\n"
        f"  .txt {{ color:{_DARK['text']} !important; }}\n"
        f"  .mut {{ color:{_DARK['muted']} !important; }}\n"
        f"  .code {{ background-color:{_DARK['code']} !important; color:{_DARK['text']} !important; "
        f"border-color:{_DARK['line']} !important; }}\n"
        f"  .state {{ color:{state} !important; }}\n"
        f"  .bar {{ background-color:{state} !important; }}\n"
        f"  .btn {{ background-color:{_DARK['button']} !important; }}\n"
        + "".join(
            f"  .sec-{s.value} {{ color:{STATE_COLORS[s].dark} !important; }}\n" for s in State
        )
        + "}"
    )


def render_html(n: Notification) -> str:
    """
    Lay a notification out as the HTML part.

    Args:
        n: The notification.

    Returns:
        A complete HTML document.
    """
    light = STATE_COLORS[n.state].light
    rows = "".join(_fact_row(fact) for fact in detail_facts(n))
    facts = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">{rows}</table>'
        if rows
        else ""
    )
    sections = "".join(_section_html(s.heading, s.rows, s.state) for s in n.sections)
    excerpt = _excerpt_html(n.excerpt, n) if n.excerpt and n.excerpt.lines else ""
    if n.subject:
        # Two lines, the state and what it is about, each in its own element: the
        # state's word is coloured, the subject is the largest thing on the page.
        state_line = (
            f'<p class="state" style="margin:0 0 4px;color:{light};font-size:14px;'
            f'font-weight:700;line-height:20px;">{n.glyph}&nbsp;{_e(clip(n.title, 100))}</p>'
        )
        heading = (
            f'<h1 class="txt" style="margin:0 0 12px;color:{_LIGHT["text"]};font-size:22px;'
            f'line-height:28px;font-weight:700;word-break:break-word;">'
            f"{_e(clip(n.subject, 200))}</h1>"
        )
    else:
        # Nothing to name (a report, a restore whose job carries no domain): the
        # state line is the heading, and the state is not said twice.
        state_line = ""
        heading = (
            f'<h1 class="state" style="margin:0 0 12px;color:{light};font-size:22px;'
            f'line-height:28px;font-weight:700;">{n.glyph}&nbsp;{_e(clip(n.title, 100))}</h1>'
        )
    return f"""<!DOCTYPE html>
<html lang="{n.locale}" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_e(subject_line(n))}</title>
<style>
{_dark_css(n)}
</style>
</head>
<body class="bg" style="margin:0;padding:0;background-color:{_LIGHT["page"]};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">{_e(clip(n.summary, 200))}&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="bg" style="background-color:{_LIGHT["page"]};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;font-family:{_FONT};">
<tr><td style="padding:0 4px 12px;">{_logo()}</td></tr>
<tr><td class="card" style="background-color:{_LIGHT["card"]};border:1px solid {_LIGHT["line"]};border-radius:8px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td class="bar" height="4" style="height:4px;line-height:4px;font-size:0;background-color:{light};border-radius:8px 8px 0 0;">&nbsp;</td></tr>
<tr><td style="padding:24px 28px 28px;">
{state_line}
{heading}
<p class="txt" style="margin:0 0 20px;color:{_LIGHT["text"]};font-size:16px;line-height:24px;">{_e(clip(n.summary, 500))}</p>
{facts}{sections}{excerpt}{_button(n)}
</td></tr>
</table>
</td></tr>
<tr><td class="mut" style="padding:16px 4px 0;color:{_LIGHT["muted"]};font-size:12px;line-height:18px;">{_e(footer_line(n))}<br>{_e(message("ui.change_settings", n.locale))}</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def render(n: Notification) -> RenderedEmail:
    """
    Render a notification as an email.

    Args:
        n: The notification.

    Returns:
        The subject, both parts, the headers and the inline image.
    """
    image = wordmark()
    return RenderedEmail(
        subject=subject_line(n),
        text=render_text(n, footer=True) + "\n",
        html=render_html(n),
        headers={
            "Auto-Submitted": "auto-generated",
            "X-Auto-Response-Suppress": "All",
            "X-Noust-Event": n.kind,
            "X-Noust-Code": n.code,
            "X-Noust-Server": clip(n.server, 64),
        },
        images=(image,) if image is not None else (),
        message_id=n.id,
    )
