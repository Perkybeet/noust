# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one Jinja environment Noust renders text files with.

Systemd units, web server sites, PHP-FPM pools and the Compose files Noust writes are not
markup, and HTML-escaping what they hold would corrupt it (an ``&`` in an ``ExecStart``). What
they interpolate is made safe where it is judged (``validators/`` and the ``_escape.j2``
macros), not by the template engine. That decision is made here once, rather than in every
module that renders a template, so that a template that is markup (a name ending in ``.html``
or ``.xml``) is escaped, and so that the choice is visible to a reader and to a scanner as what
it is: the file's kind decides, and for the kinds Noust writes the answer is no.
"""

from __future__ import annotations

from jinja2 import Environment, PackageLoader, Undefined, select_autoescape

__all__ = ["text_environment"]

#: The kinds of template that are markup. ``.j2`` is the suffix of every template Noust ships, so
#: a markup template is ``name.html.j2``.
_MARKUP = ("html", "htm", "xml", "html.j2", "htm.j2", "xml.j2")


def text_environment(package_path: str, *, undefined: type[Undefined] = Undefined) -> Environment:
    """
    Make the environment that renders the templates of one folder of the package.

    Args:
        package_path: The folder inside the ``noust`` package, such as
            ``templates/systemd``.
        undefined: What a missing variable becomes; pass
            :class:`jinja2.StrictUndefined` to make it an error.

    Returns:
        An environment that trims blocks, strips their indentation and escapes only templates
        that are markup.

    Raises:
        ValueError: The folder is not in the package (it was installed without its data files).
        ImportError: The package cannot be imported.
    """
    return Environment(
        loader=PackageLoader("noust", package_path),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=undefined,
        # A template given as a string has no name to tell its kind by: it is text, as before.
        autoescape=select_autoescape(
            enabled_extensions=_MARKUP, default=False, default_for_string=False
        ),
    )
