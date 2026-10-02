"""
Read, explain and edit web server site files without losing a byte.

The package is pure: it reads text and returns text and data. The only thing
it may touch outside its arguments is an included file, through a reader the
caller passes in and which decides what may be read.

- :func:`parse` turns a site file (nginx or Apache) into a concrete tree whose
  ``render()`` gives the text back exactly, or raises :class:`ParseError` with
  the line and column, never half a tree.
- :func:`structure` builds the structured model the console shows: servers,
  locations with their classified targets, upstreams, includes, comments and
  every directive it does not model, raw and editable.
- :func:`apply_ops` applies :class:`EditOp` operations that change only the
  bytes of the element they name.
- :func:`route` says which server and location answer a request, step by
  step, with the web server's own algorithm.
"""

from noust.managers.siteconf.edit import EditOp, EditResult, apply_ops, changed_lines
from noust.managers.siteconf.model import IncludeReader, SiteStructure, structure
from noust.managers.siteconf.route import RouteResult, RouteStep, route
from noust.managers.siteconf.tree import Kind, ParseError, Tree, parse

__all__ = [
    "EditOp",
    "EditResult",
    "IncludeReader",
    "Kind",
    "ParseError",
    "RouteResult",
    "RouteStep",
    "SiteStructure",
    "Tree",
    "apply_ops",
    "changed_lines",
    "parse",
    "route",
    "structure",
]
