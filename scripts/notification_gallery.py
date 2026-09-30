#!/usr/bin/env python3
"""
Render every notification, on every channel, in both languages, for review.

The renderers in ``noust.core.notifications.render`` are pure, so this script
needs no server, no network and no account: it builds the canonical set of
notifications (``tests/notifications_support.py``, the same one the snapshot
tests pin) and writes what each channel would receive::

    python scripts/notification_gallery.py --out /tmp/noust-notif-after
    python scripts/notification_gallery.py --out /tmp/gallery --screenshots

For every event and language, a directory with the exact payloads:

- ``telegram.json``, ``slack.json``, ``discord.json``, ``webhook.json``
- ``email.subject.txt``, ``email.text.txt`` and ``email.html`` (the real HTML
  part, byte for byte; ``email.preview.html`` has the wordmark's ``cid:``
  pointed at the image so a browser can open it)
- ``telegram.html``, ``slack.html``, ``discord.html``: approximate mock-ups
  drawn from those payloads. Telegram's HTML is a subset of HTML and is drawn
  as it is; Slack's Block Kit and Discord's embed are drawn by hand. They are
  labelled as approximations: the real thing is only ever seen in the clients.

and an ``index.html`` to browse it all. With ``--screenshots`` every mock-up
and both themes of the email are rendered to PNG with the Chromium the
console's Playwright suite already installs (Node 22, ``npm ci`` in
``panel/``); it needs no network.

Nothing is sent anywhere.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from noust.core.notifications.model import Notification  # noqa: E402
from noust.core.notifications.render import discord, email, slack, telegram, webhook  # noqa: E402
from noust.core.runner import get_runner  # noqa: E402
from tests.notifications_support import catalog, hostile  # noqa: E402

LOCALES = ("en", "es")
CHAT_ID = "-1001234567890"
NOTE = "Approximation drawn from the payload; the real client differs."

# -- mock-ups ------------------------------------------------------------------


def _mrkdwn(text: str) -> str:
    """Draw Slack's mrkdwn subset as HTML."""
    text = html.escape(html.unescape(text), quote=False)
    text = re.sub(r"```(.*?)```", lambda m: f"<pre>{m.group(1)}</pre>", text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"<i>\1</i>", text)
    text = re.sub(r"&lt;(https?://[^|&]+)\|([^&]+)&gt;", r'<a href="\1">\2</a>', text)
    return text.replace("\n", "<br>")


def telegram_mock(payload: dict[str, Any], *, dark: bool = True) -> str:
    """
    Args:
        payload: A ``sendMessage`` body.
        dark: Draw the dark theme.

    Returns:
        An HTML page that looks roughly like the message in Telegram.
    """
    bg, bubble, fg, link = (
        ("#0e1621", "#182533", "#f5f5f5", "#6ab3f3")
        if dark
        else ("#dfe6ea", "#ffffff", "#000000", "#168acd")
    )
    text = payload["text"] if payload.get("parse_mode") == "HTML" else html.escape(payload["text"])
    buttons = "".join(
        f'<div class="btn">&#8599; {html.escape(button["text"])}</div>'
        for row in payload.get("reply_markup", {}).get("inline_keyboard", [])
        for button in row
    )
    sound = "" if not payload.get("disable_notification") else " &middot; silent"
    return f"""<!doctype html><meta charset="utf-8"><title>Telegram mock-up</title>
<style>
body{{margin:0;background:{bg};font:15px/1.35 -apple-system,'Segoe UI',Roboto,sans-serif;padding:24px;width:430px;overflow-wrap:anywhere}}
.b{{background:{bubble};color:{fg};border-radius:14px;padding:8px 12px;max-width:410px;white-space:pre-wrap}}
pre{{background:rgba(127,127,127,.18);border-radius:6px;padding:6px 8px;margin:4px 0;overflow-x:auto;font-size:12.5px;line-height:1.3;white-space:pre}}
code{{font-family:ui-monospace,monospace;font-size:13px;color:#7fc8ff}} pre code{{color:inherit}}
.btn{{margin-top:4px;background:{bubble};color:{link};border-radius:10px;text-align:center;padding:8px;max-width:410px;filter:brightness(1.15)}}
.t{{text-align:right;font-size:11px;opacity:.55}} .n{{color:#888;font-size:11px;margin-top:10px}}
</style>
<div class="b">{text}<div class="t">10:45{sound}</div></div>{buttons}
<div class="n">{NOTE}</div>"""


def slack_mock(payload: dict[str, Any], *, dark: bool = False) -> str:
    """
    Args:
        payload: An incoming-webhook body.
        dark: Draw the dark theme.

    Returns:
        An HTML page that looks roughly like the message in Slack.
    """
    bg, fg, muted = ("#1a1d21", "#d1d2d3", "#ababad") if dark else ("#ffffff", "#1d1c1d", "#616061")
    code_bg, code_line = ("#222529", "#35373b") if dark else ("#f8f8f8", "#dddddd")
    attachment = payload["attachments"][0]
    parts: list[str] = []
    for block in attachment["blocks"]:
        if block["type"] == "header":
            parts.append(
                f'<div style="font-weight:900;font-size:18px;margin:4px 0 6px">'
                f"{html.escape(block['text']['text'])}</div>"
            )
        elif block["type"] == "section":
            if "text" in block:
                parts.append(f'<div style="margin:4px 0">{_mrkdwn(block["text"]["text"])}</div>')
            if "fields" in block:
                cells = "".join(
                    '<div style="width:50%;box-sizing:border-box;padding:2px 8px 6px 0">'
                    f"{_mrkdwn(field['text'])}</div>"
                    for field in block["fields"]
                )
                parts.append(f'<div style="display:flex;flex-wrap:wrap;margin:4px 0">{cells}</div>')
        elif block["type"] == "context":
            items = " &nbsp;&middot;&nbsp; ".join(_mrkdwn(e["text"]) for e in block["elements"])
            parts.append(f'<div style="color:{muted};font-size:12px;margin-top:8px">{items}</div>')
    return f"""<!doctype html><meta charset="utf-8"><title>Slack mock-up</title>
<style>
body{{margin:0;background:{bg};color:{fg};font:15px/1.45 Lato,'Segoe UI',sans-serif;padding:20px;width:640px;overflow-wrap:anywhere}}
pre{{background:{code_bg};border:1px solid {code_line};border-radius:4px;padding:8px;margin:4px 0;font-size:12px;line-height:1.35;white-space:pre-wrap;word-break:break-word;font-family:Monaco,Menlo,monospace}}
code{{background:{code_bg};border:1px solid {code_line};border-radius:3px;padding:0 3px;color:{"#e8912d" if dark else "#e01e5a"};font-size:12.5px;font-family:Monaco,Menlo,monospace}}
a{{color:#1264a3}} .n{{color:{muted};font-size:11px;margin-top:14px}}
</style>
<div style="display:flex"><div style="width:36px;height:36px;background:#7b61ff;border-radius:6px;margin-right:10px;flex:none"></div><div style="flex:1">
<div style="font-weight:900">Noust <span style="font-weight:400;font-size:12px;color:{muted}">APP &nbsp; 10:45</span></div>
<div style="display:flex;margin-top:4px"><div style="width:4px;background:{attachment["color"]};border-radius:2px;flex:none"></div>
<div style="padding-left:12px;max-width:560px">{"".join(parts)}</div></div></div></div>
<div class="n">{NOTE}</div>"""


def discord_mock(payload: dict[str, Any]) -> str:
    """
    Args:
        payload: A webhook body.

    Returns:
        An HTML page that looks roughly like the embed in Discord.
    """
    embed = payload["embeds"][0]
    colour = f"#{embed['color']:06x}"
    description = html.escape(embed["description"], quote=False)
    description = re.sub(
        r"```\n?(.*?)\n?```", lambda m: f"<pre>{m.group(1)}</pre>", description, flags=re.S
    )
    description = re.sub(r"`([^`]+)`", r"<code>\1</code>", description)
    description = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", description)
    description = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?!\w)", r"<i>\1</i>", description)
    description = re.sub(r"\\(.)", r"\1", description).replace("\u200b", "")
    description = description.replace("\n", "<br>").replace("</pre><br>", "</pre>")
    description = description.replace("<br><pre>", "<pre>")

    def field_value(value: str) -> str:
        value = html.escape(re.sub(r"\\(.)", r"\1", value).replace("\u200b", ""), quote=False)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", value)

    fields = "".join(
        '<div style="flex:1 1 30%;min-width:30%;margin:6px 0 0">'
        f'<div style="font-weight:700;font-size:13px">{html.escape(field["name"])}</div>'
        f'<div style="font-size:14px">{field_value(field["value"])}</div></div>'
        for field in embed["fields"]
    )
    title = html.escape(embed["title"])
    if "url" in embed:
        title = f'<a style="color:#00a8fc;text-decoration:none" href="{html.escape(embed["url"])}">{title}</a>'
    quiet = " (no sound)" if payload.get("flags") == 4096 else ""
    return f"""<!doctype html><meta charset="utf-8"><title>Discord mock-up</title>
<style>
body{{margin:0;background:#313338;color:#dbdee1;font:15px 'gg sans','Segoe UI',sans-serif;padding:20px;width:640px;overflow-wrap:anywhere}}
pre{{background:#2b2d31;border:1px solid #1e1f22;border-radius:4px;padding:8px;margin:6px 0;font-size:12.5px;white-space:pre-wrap;word-break:break-word;font-family:Consolas,monospace;line-height:1.3}}
code{{background:#2b2d31;border-radius:3px;padding:0 3px;font-family:Consolas,monospace;font-size:13px}}
.n{{color:#949ba4;font-size:11px;margin-top:14px}}
</style>
<div style="display:flex"><div style="width:40px;height:40px;background:#7b61ff;border-radius:50%;margin-right:14px;flex:none"></div><div>
<div style="font-weight:600">Noust <span style="background:#5865f2;color:#fff;font-size:10px;border-radius:3px;padding:1px 4px">APP</span>
<span style="font-size:12px;color:#949ba4;font-weight:400">Today at 10:45{quiet}</span></div>
<div style="margin-top:4px;background:#2b2d31;border-left:4px solid {colour};border-radius:4px;padding:8px 16px 12px 12px;max-width:520px">
<div style="font-weight:700;font-size:16px;margin-bottom:4px">{title}</div>
<div style="font-size:14px">{description}</div>
<div style="display:flex;flex-wrap:wrap;gap:0 16px;margin-top:4px">{fields}</div>
<div style="font-size:12px;color:#949ba4;margin-top:10px">{html.escape(embed["footer"]["text"])} &nbsp;&bull;&nbsp; Today at 10:45</div></div></div></div>
<div class="n">{NOTE}</div>"""


# -- writing the set -----------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def render_one(notification: Notification, directory: Path) -> list[dict[str, Any]]:
    """
    Write every channel's output for one notification.

    Args:
        notification: What to render.
        directory: Where the event's files go.

    Returns:
        The screenshot jobs for the files written: ``html`` to open, ``png``
        to write, and the viewport.
    """
    tg = telegram.render(notification, CHAT_ID)
    sl = slack.render(notification)
    dc = discord.render(notification)
    wh = webhook.render(notification)
    mail = email.render(notification)

    _write(directory / "telegram.json", _json(tg))
    _write(directory / "slack.json", _json(sl))
    _write(directory / "discord.json", _json(dc))
    _write(directory / "webhook.json", _json(wh))
    _write(directory / "email.subject.txt", mail.subject + "\n")
    _write(directory / "email.text.txt", mail.text)
    _write(directory / "email.html", mail.html)
    _write(
        directory / "email.preview.html",
        mail.html.replace("cid:noust-wordmark", "../../assets/noust-wordmark.png"),
    )
    _write(directory / "telegram.html", telegram_mock(tg))
    _write(directory / "slack.html", slack_mock(sl))
    _write(directory / "discord.html", discord_mock(dc))

    jobs: list[dict[str, Any]] = []
    for name in ("telegram", "slack", "discord"):
        jobs.append(
            {
                "html": str(directory / f"{name}.html"),
                "png": str(directory / f"{name}.png"),
                "width": 700,
            }
        )
    for scheme in ("light", "dark"):
        jobs.append(
            {
                "html": str(directory / "email.preview.html"),
                "png": str(directory / f"email-{scheme}.png"),
                "width": 720,
                "dark": scheme == "dark",
            }
        )
    return jobs


def index_page(rows: list[tuple[str, dict[str, Notification]]], screenshots: bool) -> str:
    """
    Args:
        rows: ``(section title, {code: notification})`` per group.
        screenshots: Whether PNGs exist next to the files.

    Returns:
        The gallery's front page.
    """
    body: list[str] = []
    for title, items in rows:
        body.append(f"<h2>{html.escape(title)}</h2><table>")
        body.append(
            "<tr><th>event</th><th>state</th>"
            + "".join(f"<th>{loc}</th>" for loc in LOCALES)
            + "</tr>"
        )
        for code, notification in items.items():
            links = "".join(
                f'<td><a href="{loc}/{code}/index.html">open</a></td>' for loc in LOCALES
            )
            body.append(
                f"<tr><td><code>{html.escape(code)}</code></td>"
                f"<td>{notification.glyph} {notification.state.value}</td>{links}</tr>"
            )
        body.append("</table>")
    return (
        "<!doctype html><meta charset='utf-8'><title>Noust notifications</title>"
        "<style>body{font:14px system-ui;margin:24px;max-width:900px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 10px;text-align:left}</style>"
        "<h1>Noust notifications</h1><p>Every event, on every channel, in English and Spanish. "
        "Mock-ups are approximations; the email HTML is exactly what is sent.</p>" + "".join(body)
    )


def event_page(code: str, locale: str, screenshots: bool) -> str:
    """
    Args:
        code: The event's code.
        locale: Its language.
        screenshots: Whether PNGs exist to show instead of live iframes.

    Returns:
        A page with the five channels side by side.
    """

    def tile(name: str, caption: str, *, width: int, height: int) -> str:
        if screenshots and (name.endswith(".png")):
            content = f'<img src="{name}" style="max-width:{width}px">'
        else:
            content = f'<iframe src="{name}" width="{width}" height="{height}" style="border:1px solid #ccc"></iframe>'
        return f"<div style='margin:12px'><b>{caption}</b><br>{content}</div>"

    def source(name: str) -> str:
        return f"{name}.png" if screenshots else f"{name}.html"

    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{html.escape(code)} ({locale})</title>"
        "<style>body{font:14px system-ui;margin:16px}.row{display:flex;flex-wrap:wrap}</style>"
        f"<h1><code>{html.escape(code)}</code> &middot; {locale}</h1>"
        "<div class='row'>"
        + tile(source("telegram"), "Telegram", width=460, height=700)
        + tile(source("slack"), "Slack", width=680, height=700)
        + tile(source("discord"), "Discord", width=680, height=700)
        + "</div><div class='row'>"
        + tile(
            source("email-light") if screenshots else "email.preview.html",
            "Email, light",
            width=740,
            height=900,
        )
        + (tile("email-dark.png", "Email, dark", width=740, height=900) if screenshots else "")
        + "</div><p>"
        "<a href='telegram.json'>telegram.json</a> &middot; <a href='slack.json'>slack.json</a> &middot; "
        "<a href='discord.json'>discord.json</a> &middot; <a href='webhook.json'>webhook.json</a> &middot; "
        "<a href='email.text.txt'>email.text.txt</a> &middot; <a href='email.html'>email.html</a></p>"
    )


SHOT_SCRIPT = """\
import { chromium } from "%(playwright)s";
import fs from "node:fs";
const jobs = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const browser = await chromium.launch();
for (const job of jobs) {
  const context = await browser.newContext({
    viewport: { width: job.width ?? 720, height: 900 },
    colorScheme: job.dark ? "dark" : "light",
  });
  const page = await context.newPage();
  await page.goto("file://" + job.html);
  await page.screenshot({ path: job.png, fullPage: true });
  await context.close();
}
await browser.close();
"""


def screenshot(jobs: list[dict[str, Any]], out: Path) -> None:
    """
    Render mock-ups and emails to PNG with the console's Chromium.

    Args:
        jobs: What :func:`render_one` returned, for every event.
        out: The gallery directory.

    Raises:
        SystemExit: When Node or Playwright is not available.
    """
    playwright = ROOT / "panel" / "node_modules" / "playwright-core" / "index.mjs"
    if not playwright.exists():
        raise SystemExit("Playwright is missing: run `npm ci` in panel/ (Node 22), then retry.")
    script = out / "_shots.mjs"
    script.write_text(SHOT_SCRIPT % {"playwright": playwright}, encoding="utf-8")
    manifest = out / "_shots.json"
    manifest.write_text(json.dumps(jobs), encoding="utf-8")
    result = get_runner().run(["node", str(script), str(manifest)], timeout=1800)
    if not result.success:
        raise SystemExit(f"Screenshots failed: {result.stderr or result.stdout}")
    script.unlink()
    manifest.unlink()


def main() -> None:
    """Build the gallery."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(tempfile.gettempdir()) / "noust-notif-gallery",
        help="where to write the gallery (default: a directory under the system temp dir)",
    )
    parser.add_argument(
        "--screenshots", action="store_true", help="also render PNGs (needs Playwright)"
    )
    parser.add_argument("--only", nargs="*", metavar="CODE", help="render only these event codes")
    args = parser.parse_args()

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    logo = email.wordmark()
    if logo is not None:
        (out / "assets" / "noust-wordmark.png").write_bytes(logo.data)

    jobs: list[dict[str, Any]] = []
    groups: list[tuple[str, dict[str, Notification]]] = []
    for locale in LOCALES:
        normal = catalog(locale)  # type: ignore[arg-type]
        risky = hostile(locale)  # type: ignore[arg-type]
        for group in (normal, risky):
            for code, notification in group.items():
                if args.only and code not in args.only:
                    continue
                directory = out / locale / code
                jobs.extend(render_one(notification, directory))
                _write(directory / "index.html", event_page(code, locale, args.screenshots))
        if locale == LOCALES[0]:
            groups = [("Events", normal), ("Hostile text (escaping and limits)", risky)]

    if args.only:
        groups = [
            (title, {c: n for c, n in items.items() if c in args.only}) for title, items in groups
        ]
    _write(out / "index.html", index_page(groups, args.screenshots))
    if args.screenshots:
        screenshot(jobs, out)
    print(f"{len(jobs)} renders in {out}" if args.screenshots else f"Gallery written to {out}")


if __name__ == "__main__":
    main()
