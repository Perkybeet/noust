# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The tools the assistant is given, which are the MCP endpoint's tools and three of its own (item 171).

**One definition of a tool.** The short list of routes the MCP endpoint offers
(:data:`noust.web.mcp.tools.TOOLS`) is what the assistant is given too, with the same description,
the same schema read from the OpenAPI document of the routes and the same validation: a tool is a
route and a name, and a model that calls one with arguments that do not fit is told what is wrong
in words it can correct. Not a copy: the same tuple.

**Three of its own**, for what the short list does not reach:

- ``search_docs``: passages of the documentation, each with its page, to cite;
- ``find_operation``: the operations of the API that do what the person wants, ranked, with what
  each needs and whether a person has to carry it out in their own session;
- ``call_operation``: prepares the exact call of an operation that was found. It runs the same way a
  named tool does: a read is made, a change is made only where the policy of who calls says so, and
  otherwise it is proposed.

What is not offered is not on the list: a tool whose route needs a permission its token does not hold
is not given to the model, and neither is an operation of the floor.

**With a fleet**, a tool is the same tool called on a server: the path goes through the central's
proxy to that server (``/api/nodes/{server}/api/...``), which applies everything it applies to a
person (the node's own policy, the ceiling the node puts on its central, sudo mode, the team scope),
and the assistant's token is what authenticates there. With all the servers, every per-server tool
takes a ``server`` argument and the central's own views of the fleet are offered for reading.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from noust.core.assistant.docsearch import DocIndex
from noust.core.assistant.opsearch import OperationHit, OperationIndex, plan_of
from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.operation_classes import (
    NEUTRAL_PERMISSIONS,
    OWN,
    PERSON_CONFIRMATIONS,
    PUBLIC_CLASS,
    OperationClass,
    classify_schema,
)
from noust.web.assistant.routing import runs_as
from noust.web.mcp import guardrails
from noust.web.mcp.tools import (
    GROUP_CONTROL,
    GROUP_INSPECT,
    TOOLS,
    PathParameterRefused,
    Tool,
    build_request,
    check_arguments,
    env_names,
    fill_path,
    input_schema,
    path_problem,
)
from noust.web.permissions.registry import permission_for_route

__all__ = [
    "ACT_CODES",
    "FLEET_TOOLS",
    "INSIDE_TOOLS",
    "OWN_TOOLS",
    "STEP_CODES",
    "VARIABLE_READS",
    "Prepared",
    "Reach",
    "ToolProblem",
    "ToolRefused",
    "Toolbox",
    "proxy_path",
]

#: The tools of its own.
OWN_TOOLS: tuple[str, ...] = ("search_docs", "find_operation", "call_operation")

#: What the model is never offered to say in a call, as the MCP hides what it hides: the confirmations a
#: person gives (going back past a change of schema, deploying past a freeze). The model prepares the call
#: and the person, who sees it, decides; a field that confirms is theirs. The policy holds the same line
#: where the call is judged (:data:`noust.core.operation_classes.PERSON_CONFIRMATIONS`), whoever sends it.
ASSISTANT_HIDDEN: frozenset[str] = PERSON_CONFIRMATIONS

#: The reads whose answer carries variables: an application's environment and its file, its Compose
#: variables and its previews', and those of a team, a project, an environment and the server. Whatever tool
#: they are asked through, they reach the model as names alone (:func:`noust.web.mcp.tools.env_names`): a value
#: nobody knows is a secret and that has no shape of one cannot be told from any other text. A test walks
#: the API for every ``GET`` that carries variables, so a route added tomorrow is not left out.
VARIABLE_READS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/apps/{domain}/compose/variables"),
        ("GET", "/api/apps/{domain}/env"),
        ("GET", "/api/apps/{domain}/env/file"),
        ("GET", "/api/apps/{domain}/previews/variables"),
        ("GET", "/api/teams/{team}/projects/{project}/environments/{environment}/variables"),
        ("GET", "/api/teams/{team}/projects/{project}/variables"),
        ("GET", "/api/teams/{team}/variables"),
        ("GET", "/api/variables/server"),
    }
)

#: What a read of variables is never offered to ask for: the values in clear, or the file as a download.
_VARIABLE_HIDDEN: frozenset[str] = frozenset({"unmask", "unmasked", "download"})

#: What the MCP hides in the tool of each route, which a call to that route made by its path hides too: the
#: same operation reached by its name or by its address is the same operation.
_MCP_HIDDEN: dict[tuple[str, str], frozenset[str]] = {
    (tool.method, tool.path): tool.hidden for tool in TOOLS if tool.hidden
}

#: The only tools whose answer is not text somebody else wrote: what Noust ships (its documentation and the
#: description of its own API). Everything else a tool reads is from outside: the names of an application, a
#: certificate or a backup are written by whoever made them, a health check carries the programs' own lines, a
#: job quotes what a build printed. Having read any of it marks a conversation: what the assistant is told to do
#: afterwards is not done on its own. The list is of what is trusted, so a tool added tomorrow is from outside
#: until somebody argues otherwise here.
INSIDE_TOOLS = frozenset({"search_docs", "find_operation"})

#: What a central offers to look at its servers together.
FLEET_TOOLS: tuple[Tool, ...] = (
    Tool(
        "fleet_servers",
        "Servers of the fleet",
        "List the servers of the fleet: whether each answers, what it runs and its version.",
        "GET",
        "/api/fleet/servers",
        GROUP_INSPECT,
        domain_field=None,
    ),
    Tool(
        "fleet_apps",
        "Applications of the fleet",
        "List the applications of every server, each with the server it runs on and its state.",
        "GET",
        "/api/fleet/apps",
        GROUP_INSPECT,
        domain_field=None,
    ),
    Tool(
        "fleet_certificates",
        "Certificates of the fleet",
        "List the certificates of every server and when each expires.",
        "GET",
        "/api/fleet/certificates",
        GROUP_INSPECT,
        domain_field=None,
    ),
    Tool(
        "fleet_backups",
        "Backups of the fleet",
        "List the backups of every server.",
        "GET",
        "/api/fleet/backups",
        GROUP_INSPECT,
        domain_field=None,
    ),
    Tool(
        "fleet_updates",
        "Updates of the fleet",
        "Say which servers have a newer version of Noust or of the system to install.",
        "GET",
        "/api/fleet/updates",
        GROUP_INSPECT,
        domain_field=None,
    ),
)

_FLEET_NAMES = frozenset(tool.name for tool in FLEET_TOOLS)

#: The longest a query to a search is.
_MAX_QUERY = 300

#: What a tool's step says, by tool: the values of its sentence (``assistant.step.<tool>``).
_STEP_PARAMS: dict[str, tuple[str, ...]] = {
    "get_application": ("domain",),
    "application_logs": ("domain",),
    "diagnose_application": ("domain",),
    "list_environment_variables": ("domain",),
    "list_application_domains": ("domain",),
    "list_releases": ("domain",),
    "get_deployment": ("deployment_id",),
    "deployment_log": ("deployment_id",),
    "get_job": ("job_id",),
    "job_log": ("job_id",),
    "search_docs": ("query",),
    "find_operation": ("query",),
}

#: What a change's title says, by tool: the values of its sentence (``assistant.act.<tool>``).
_ACT_PARAMS: dict[str, tuple[str, ...]] = {
    "deploy_application": ("domain",),
    "rollback_application": ("domain",),
    "activate_release": ("domain", "release_id"),
    "cancel_job": ("job_id",),
    "create_backup": ("domain",),
    "restart_application": ("domain",),
    "start_application": ("domain",),
    "stop_application": ("domain",),
    "renew_certificate": ("domain",),
}


#: The sentence of what a tool read, and of what a tool that changes something is, by tool. Written out,
#: because a code is a literal wherever it is said.
STEP_CODES: dict[str, str] = {
    "list_applications": "assistant.step.list_applications",
    "get_application": "assistant.step.get_application",
    "application_logs": "assistant.step.application_logs",
    "diagnose_application": "assistant.step.diagnose_application",
    "list_environment_variables": "assistant.step.list_environment_variables",
    "list_application_domains": "assistant.step.list_application_domains",
    "list_releases": "assistant.step.list_releases",
    "list_deployments": "assistant.step.list_deployments",
    "get_deployment": "assistant.step.get_deployment",
    "deployment_log": "assistant.step.deployment_log",
    "list_jobs": "assistant.step.list_jobs",
    "get_job": "assistant.step.get_job",
    "job_log": "assistant.step.job_log",
    "list_backups": "assistant.step.list_backups",
    "list_certificates": "assistant.step.list_certificates",
    "list_teams": "assistant.step.list_teams",
    "list_projects": "assistant.step.list_projects",
    "server_health": "assistant.step.server_health",
    "fleet_servers": "assistant.step.fleet_servers",
    "fleet_apps": "assistant.step.fleet_apps",
    "fleet_certificates": "assistant.step.fleet_certificates",
    "fleet_backups": "assistant.step.fleet_backups",
    "fleet_updates": "assistant.step.fleet_updates",
    "search_docs": "assistant.step.search_docs",
    "find_operation": "assistant.step.find_operation",
    "call_operation": "assistant.step.operation",
}
ACT_CODES: dict[str, str] = {
    "deploy_application": "assistant.act.deploy_application",
    "rollback_application": "assistant.act.rollback_application",
    "activate_release": "assistant.act.activate_release",
    "cancel_job": "assistant.act.cancel_job",
    "create_backup": "assistant.act.create_backup",
    "restart_application": "assistant.act.restart_application",
    "start_application": "assistant.act.start_application",
    "stop_application": "assistant.act.stop_application",
    "renew_certificate": "assistant.act.renew_certificate",
    "call_operation": "assistant.act.operation",
}


class ToolProblem(Exception):
    """A call that cannot be made as it is: the message is written for the model, to correct it."""


class ToolRefused(ToolProblem):
    """
    An operation that exists and is not offered: the floor, or what is a person's own.

    The model is told, and so is the person (what the assistant was not offered, and who changes it).

    Attributes:
        title_code: The sentence that says what it was.
        title_params: Its values.
        why_code: The sentence that says why not.
        floor: Nobody changes it.
    """

    def __init__(
        self,
        message: str,
        *,
        title_code: str,
        title_params: Mapping[str, Any],
        why_code: str,
        floor: bool,
    ) -> None:
        """
        Args:
            message: What the model is told.
            title_code: The sentence that says what it was.
            title_params: Its values.
            why_code: The sentence that says why not.
            floor: Nobody changes it.
        """
        super().__init__(message)
        self.title_code = title_code
        self.title_params = dict(title_params)
        self.why_code = why_code
        self.floor = floor


@dataclass(frozen=True)
class Reach:
    """
    Where the person is looking, which is where the assistant works.

    Attributes:
        server: A node's name when the person chose one, ``*`` for all the servers, ``""`` for the
            server where Noust runs.
        servers: The names of the servers of the fleet the person reaches.
    """

    server: str = ""
    servers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Prepared:
    """
    A call, checked and ready: what it is, what it needs and how to make it.

    Attributes:
        name: The tool that was called.
        kind: ``api`` for a call to the API, ``docs`` or ``operations`` for a search.
        method: The HTTP method.
        template: The route's template, which the policy and the schema know it by.
        path: What is called: the path with its parameters in place, through the central for a server.
        query: The query pairs.
        body: The JSON body, or None.
        server: The server it is for, or ``""``.
        operation: What the operation is (:mod:`noust.core.operation_classes`).
        needed: The permissions it needs.
        domain: The application it names, for masking and for the environment.
        arguments: The checked arguments, without ``confirm`` and ``server``.
        summary: What the API says the operation does.
        read_only: It changes nothing.
        outside: What it returns is text somebody else wrote.
        log: What it returns is a log: its end is what matters.
        plan: The operation that shows what it would do, as ``METHOD /path``, when it has one.
        step_code: The code of the sentence that says what it is.
        step_params: The values of that sentence.
        query_text: For a search, what is looked for.
        shape: Turns the API's answer into what the model reads (a summary, a list of names).
    """

    name: str
    kind: str
    method: str = ""
    template: str = ""
    path: str = ""
    query: tuple[tuple[str, str], ...] = ()
    body: dict[str, Any] | None = None
    server: str = ""
    operation: OperationClass | None = None
    needed: tuple[str, ...] = ()
    domain: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    read_only: bool = True
    outside: bool = False
    log: bool = False
    plan: str | None = None
    step_code: str = ""
    step_params: dict[str, Any] = field(default_factory=dict)
    query_text: str = ""
    shape: Callable[[Any], Any] | None = None


def proxy_path(server: str, path: str) -> str:
    """
    Args:
        server: A server of the fleet.
        path: A route of the API, from ``/api`` on.

    Returns:
        The path that asks the central to make that call on the server.
    """
    rest = path[len("/api/") :] if path.startswith("/api/") else path.lstrip("/")
    return f"/api/nodes/{urllib.parse.quote(server, safe='')}/api/{rest}"


class Toolbox:
    """The tools one conversation is offered, and the checking of a call to one."""

    def __init__(
        self,
        openapi: Mapping[str, Any],
        *,
        token_permissions: frozenset[str],
        person_permissions: frozenset[str],
        reach: Reach,
        docs: DocIndex | None = None,
    ) -> None:
        """
        Args:
            openapi: The application's OpenAPI document.
            token_permissions: What the assistant's token holds: what a read needs.
            person_permissions: What the person holds: a change it only proposes is carried out by
                them, so it needs what they hold and not what the token does.
            reach: Where the person is looking.
            docs: The documentation, when there is any.
        """
        self._openapi = openapi
        self._token = token_permissions
        self._person = person_permissions
        self.reach = reach
        self._docs = docs if docs is not None and docs.available else None
        self._ops = shared_index(openapi)
        tools = [*TOOLS, *(FLEET_TOOLS if reach.server == "*" else ())]
        self._named: dict[str, Tool] = {
            tool.name: replace(tool, hidden=tool.hidden | ASSISTANT_HIDDEN)
            for tool in tools
            if self._tool_offered(tool)
        }

    # -- what is on offer ------------------------------------------------------------------------

    def _offered(self, found: OperationClass) -> bool:
        """
        Args:
            found: What an operation is.

        Returns:
            Whether the assistant is offered it at all.
        """
        if found.kind in (OWN, PUBLIC_CLASS) or found.permission in NEUTRAL_PERMISSIONS:
            return False
        if found.permission in ASSISTANT_FLOOR:
            return False
        if found.changes:
            return found.permission in self._person
        return found.permission in self._token

    def _tool_offered(self, tool: Tool) -> bool:
        """
        Args:
            tool: A tool of the short list.

        Returns:
            Whether the token holds what its route needs.
        """
        needed = permission_for_route(tool.method, tool.path)
        if needed is None:
            return False
        if tool.read_only:
            return needed in self._token and needed not in ASSISTANT_FLOOR
        return needed in self._person and needed not in ASSISTANT_FLOOR

    @property
    def names(self) -> tuple[str, ...]:
        """The tools on offer, by name."""
        own = [*(("search_docs",) if self._docs else ()), "find_operation", "call_operation"]
        return (*self._named, *own)

    def has(self, name: str) -> bool:
        """
        Args:
            name: What the model called.

        Returns:
            Whether it is one of the tools on offer.
        """
        return name in self.names

    def _per_server(self, tool: Tool) -> bool:
        """
        Args:
            tool: A tool.

        Returns:
            Whether it applies to one server (and takes a ``server`` argument with all of them).
        """
        return tool.name not in _FLEET_NAMES

    def _schema(self, tool: Tool) -> dict[str, Any]:
        """
        Args:
            tool: A tool.

        Returns:
            What it accepts: the MCP's schema without ``approval`` (a proposal is the way a person
            approves, not a number) and, with all the servers, the server it applies to.
        """
        schema = input_schema(self._openapi, tool)
        schema["properties"].pop("approval", None)
        if self.reach.server == "*" and self._per_server(tool):
            schema["properties"]["server"] = _server_property(self.reach.servers)
            schema["required"] = sorted({*schema.get("required", []), "server"})
        return schema

    def definitions(self) -> list[dict[str, Any]]:
        """
        Returns:
            The tools on offer as the protocol describes them.
        """
        described = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": self._schema(tool),
                },
            }
            for tool in self._named.values()
        ]
        if self._docs:
            described.append(_function("search_docs", _SEARCH_DOCS, _query_schema()))
        described.append(_function("find_operation", _FIND_OPERATION, _query_schema()))
        described.append(_function("call_operation", _CALL_OPERATION, _call_schema(self.reach)))
        return described

    # -- checking a call -------------------------------------------------------------------------

    def prepare(self, name: str, arguments: Mapping[str, Any]) -> Prepared:
        """
        Check a call and get it ready.

        Args:
            name: The tool.
            arguments: What the model sent, with the disguise already put back.

        Returns:
            The call.

        Raises:
            ToolProblem: It cannot be made as it is, and why, for the model.
        """
        if not self.has(name):
            raise ToolProblem(f"Unknown tool: {name}")
        if name in ("search_docs", "find_operation"):
            query = str(arguments.get("query") or "").strip()[:_MAX_QUERY]
            if not query:
                raise ToolProblem("Missing argument(s): query.")
            kind = "docs" if name == "search_docs" else "operations"
            return Prepared(
                name,
                kind,
                step_code=STEP_CODES[name],
                step_params={"query": query},
                query_text=query,
            )
        if name == "call_operation":
            return self._prepare_call(arguments)
        return self._prepare_tool(self._named[name], dict(arguments))

    def _prepare_tool(self, tool: Tool, arguments: dict[str, Any]) -> Prepared:
        """
        Args:
            tool: A named tool.
            arguments: What the model sent.

        Returns:
            The call.

        Raises:
            ToolProblem: The arguments do not fit, or a destructive tool was not confirmed.
        """
        server = self._server_of(tool, arguments)
        problem = _path_problem(tool, arguments) or check_arguments(self._schema(tool), arguments)
        if problem:
            raise ToolProblem(problem)
        if tool.destructive and arguments.get("confirm") is not True:
            raise ToolProblem(guardrails.CONFIRM_NEEDED.format(effect=tool.effect))
        sent = {k: v for k, v in arguments.items() if k not in ("confirm", "server")}
        return self._build(tool, sent, server)

    def _build(self, tool: Tool, sent: dict[str, Any], server: str) -> Prepared:
        """
        Args:
            tool: The tool, a named one or one made for ``call_operation``.
            sent: The checked arguments without ``confirm`` and ``server``.
            server: The server it is for.

        Returns:
            The call.
        """
        try:
            path, query, body = build_request(tool, self._openapi, sent)
        except PathParameterRefused as refused:
            raise ToolProblem(str(refused)) from None
        operation = self._ops.class_of(tool.method, tool.path)
        found = self._ops.get(tool.method, tool.path)
        permission = permission_for_route(tool.method, tool.path) or ""
        domain = sent.get(tool.domain_field) if tool.domain_field else sent.get("domain")
        title_params = _ACT_PARAMS if not tool.read_only else _STEP_PARAMS
        wanted = title_params.get(tool.name, ())
        step_code = (STEP_CODES if tool.read_only else ACT_CODES)[tool.name]
        params: dict[str, Any] = {key: str(sent.get(key, "")) for key in wanted}
        if tool.name == "call_operation":
            params = (
                {"method": tool.method, "path": tool.path}
                if tool.read_only
                else {
                    "summary": found.summary if found else tool.path,
                    "method": tool.method,
                    "path": tool.path,
                }
            )
        return Prepared(
            tool.name,
            "api",
            method=tool.method,
            template=tool.path,
            path=proxy_path(server, path) if server else path,
            query=tuple((key, _text(value)) for key, value in query.items() if value is not None),
            body=body,
            server=server,
            operation=operation,
            needed=(permission,) if permission else (),
            domain=str(domain) if domain else None,
            arguments=sent,
            summary=found.summary if found else "",
            read_only=tool.read_only,
            outside=tool.read_only,
            log=bool(tool.extra.get("log")),
            plan=_plan(self._openapi, tool),
            step_code=step_code,
            step_params=params,
            shape=tool.shape,
        )

    def _prepare_call(self, arguments: Mapping[str, Any]) -> Prepared:
        """
        Args:
            arguments: What the model sent to ``call_operation``.

        Returns:
            The call it describes.

        Raises:
            ToolProblem: There is no such operation, it is not offered, or its parameters do not fit.
        """
        method = str(arguments.get("method") or "").upper()
        template = str(arguments.get("path") or "")
        unknown = sorted(set(arguments) - {"method", "path", "parameters", "body", "server"})
        if unknown:
            raise ToolProblem(
                f"Unknown argument(s): {', '.join(unknown)}. This tool takes: method, path, "
                "parameters, body" + (", server" if self.reach.server == "*" else "") + "."
            )
        found = self._ops.get(method, template)
        known = self._ops.class_of(method, template)
        if found is None or known is None:
            raise ToolProblem(
                f"No operation {method} {template}. Look for one with find_operation and give its "
                "method and path exactly as it says them."
            )
        if not self._offered(known):
            floor = known.permission in ASSISTANT_FLOOR
            raise ToolRefused(
                f"{method} {template} is not offered to you. Do not look for another way to do it: "
                "tell the person it is not something you can do, and that a person does it in the "
                "console.",
                title_code=ACT_CODES["call_operation"]
                if known.changes
                else STEP_CODES["call_operation"],
                title_params=(
                    {"summary": found.summary or template, "method": method, "path": template}
                    if known.changes
                    else {"method": method, "path": template}
                ),
                why_code="approvals.never_assistant_floor"
                if floor
                else "assistant.refused.not_offered",
                floor=True,
            )
        parameters, body = arguments.get("parameters") or {}, arguments.get("body") or {}
        if not isinstance(parameters, dict) or not isinstance(body, dict):
            raise ToolProblem("parameters and body must be JSON objects.")
        read_only = not known.changes
        tool = Tool(
            "call_operation",
            found.summary or template,
            "",
            method,
            template,
            GROUP_INSPECT if read_only else GROUP_CONTROL,
            read_only=read_only,
            hidden=_MCP_HIDDEN.get((method, template), frozenset())
            | ASSISTANT_HIDDEN
            | (_VARIABLE_HIDDEN if (method, template) in VARIABLE_READS else frozenset()),
            shape=env_names if (method, template) in VARIABLE_READS else None,
        )
        merged = {**parameters, **body}
        server = self._server_of(tool, dict(arguments))
        problem = _path_problem(tool, merged) or check_arguments(
            _without(input_schema(self._openapi, tool), "approval"), merged
        )
        if problem:
            raise ToolProblem(problem)
        return self._build(tool, merged, server)

    def _server_of(self, tool: Tool, arguments: Mapping[str, Any]) -> str:
        """
        Args:
            tool: The tool.
            arguments: What the model sent.

        Returns:
            The server the call is for: the one the person is looking at, or the one the model
            named when the person is looking at all of them. ``""`` for this server and for a view
            of the fleet.

        Raises:
            ToolProblem: A server that is not one of the person's.
        """
        if not self._per_server(tool):
            return ""
        if self.reach.server == "*":
            chosen = str(arguments.get("server") or "")
            if not chosen:
                raise ToolProblem(
                    "Missing argument(s): server. Say which server this is for; ask the person "
                    "when it is not clear."
                )
            if chosen not in self.reach.servers:
                raise ToolProblem(
                    f"Unknown server {chosen!r}. The servers are: {', '.join(self.reach.servers)}."
                )
            return chosen
        if "server" in arguments:
            raise ToolProblem(
                "Unknown argument(s): server. This tool takes: "
                + ", ".join(sorted(self._schema(tool)["properties"]))
                + "."
            )
        return self.reach.server

    # -- the searches ----------------------------------------------------------------------------

    def search_docs(self, query: str) -> tuple[str, int]:
        """
        Args:
            query: What is looked for.

        Returns:
            The passages that fit, each with its page and title, as text, and how many.
        """
        if self._docs is None:
            return "There is no documentation to look up on this server.", 0
        passages = self._docs.search(query)
        if not passages:
            return "Nothing in the documentation matches. Try other words.", 0
        text = "\n\n".join(
            f"[{number}] {p.page} - {p.title}\n{p.text}" for number, p in enumerate(passages, 1)
        )
        return text, len(passages)

    def find_operation(self, query: str) -> tuple[str, int]:
        """
        Args:
            query: What the person wants to do, in words.

        Returns:
            The operations that fit, one to a line, each with what it needs and who carries it
            out; and how many.
        """
        hits = self._ops.search(query, allow=self._offered)
        if not hits:
            return (
                "No operation matches. Try other words, or say it is not something you can do.",
                0,
            )
        return "\n".join(_describe(hit) for hit in hits), len(hits)


# -- helpers -------------------------------------------------------------------------------------

_SEARCH_DOCS = (
    "Search Noust's documentation. Returns passages with the page and the heading each is under, to "
    "cite them. Use it for how something works or is configured; it does not know this server."
)
_FIND_OPERATION = (
    "Find the operation of Noust's API that does what the person wants, in words (for example "
    "'raise the memory limit of an application'). Returns up to eight: the method and path, what it "
    "does, the permission it needs and whether a person has to carry it out in their own session. "
    "Then prepare it with call_operation."
)
_CALL_OPERATION = (
    "Prepare the exact call of an operation found with find_operation: give its method and path "
    "as they were given, the values of the {parameters} of the path and of its query in "
    "`parameters`, and its JSON body in `body`. A read is made and you get its answer. A change is "
    "either made, when the person's approval policy lets you make it alone, or proposed: it waits "
    "for the person, who sees the exact call and carries it out. You are told which."
)


def _function(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """
    Args:
        name: The tool.
        description: What it does.
        parameters: Its schema.

    Returns:
        It as the protocol describes a tool.
    """
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def _query_schema() -> dict[str, Any]:
    """
    Returns:
        The schema of a search: one text.
    """
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": _MAX_QUERY,
                "description": "What to look for, in a few words.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def _call_schema(reach: Reach) -> dict[str, Any]:
    """
    Args:
        reach: Where the person is looking.

    Returns:
        The schema of ``call_operation``.
    """
    properties: dict[str, Any] = {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "path": {
            "type": "string",
            "description": "The operation's path exactly as find_operation gave it.",
        },
        "parameters": {
            "type": "object",
            "description": "Values for the {parameters} of the path and for its query.",
        },
        "body": {"type": "object", "description": "The JSON body, when the operation takes one."},
    }
    required = ["method", "path"]
    if reach.server == "*":
        properties["server"] = _server_property(reach.servers)
        required.append("server")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _server_property(servers: tuple[str, ...]) -> dict[str, Any]:
    """
    Args:
        servers: The servers the person reaches.

    Returns:
        The schema of the ``server`` argument.
    """
    return {
        "type": "string",
        "description": "The server this applies to: one of " + ", ".join(servers) + ".",
    }


def _describe(hit: OperationHit) -> str:
    """
    Args:
        hit: An operation.

    Returns:
        A line that says what it is and who carries it out.
    """
    asks = []
    if hit.elevation:
        asks.append("asks the person to confirm it is them (sudo mode)")
    if hit.approval:
        asks.append("a second person approves it")
    if hit.plan:
        asks.append(f"its plan: {hit.plan}")
    kind = "changes something" if hit.changes else "reads"
    extra = f"; {'; '.join(asks)}" if asks else ""
    return f"{hit.method} {hit.path} - {hit.summary} [{kind}; needs {hit.permission}{extra}]"


_INDEXES: dict[int, tuple[Mapping[str, Any], OperationIndex]] = {}


def shared_index(openapi: Mapping[str, Any]) -> OperationIndex:
    """
    Args:
        openapi: The application's OpenAPI document, which the application builds once.

    Returns:
        The index of its operations, built the first time and kept: a thousand operations are not
        read again for every turn.
    """
    held = _INDEXES.get(id(openapi))
    if held is not None and held[0] is openapi:
        return held[1]
    index = OperationIndex(openapi, classify_schema_safely(openapi))
    _INDEXES.clear()
    _INDEXES[id(openapi)] = (openapi, index)
    return index


def classify_schema_safely(openapi: Mapping[str, Any]) -> dict[tuple[str, str], OperationClass]:
    """
    Args:
        openapi: The OpenAPI document.

    Returns:
        The class of every operation that can be classified; an operation nobody classified is left
        out (a test fails for that before it is served), and is not offered.
    """
    from noust.core.operation_classes import Unclassified, classify

    found: dict[tuple[str, str], OperationClass] = {}
    try:
        return dict(classify_schema(openapi))
    except Unclassified:
        pass
    for path, item in (openapi.get("paths") or {}).items():
        for method, operation in item.items():
            try:
                found[(method.upper(), path)] = classify(method, path, operation)
            except Unclassified:
                continue
    return found


def _path_problem(tool: Tool, arguments: Mapping[str, Any]) -> str | None:
    """
    Args:
        tool: A tool, named or made for ``call_operation``.
        arguments: What the model sent.

    Returns:
        What is wrong with the path its parameters make, for the model: a value that cannot be one
        part of a path, or one that makes the server route the path to another operation than the
        tool's. Said before anything else about the call, so it does not depend on what else is
        missing from it. A read is not held to the second: it has no card, and the token that makes
        it holds what the route it reaches needs.
    """
    problem = path_problem(tool, arguments)
    if problem or tool.method == "GET" or not all(n in arguments for n in tool.path_parameters):
        return problem
    path = fill_path(tool.path, {name: arguments[name] for name in tool.path_parameters})
    if runs_as(tool.method, path, tool.path):
        return None
    # The card is classified by the template and what runs is whatever the server routes the path
    # to: a value that splits into parts (an encoded slash) makes them two operations.
    return (
        f"With these values {tool.method} {tool.path} would reach another operation: a name cannot "
        "hold a '/' or anything else that splits it into parts. Give the name itself."
    )


def _without(schema: dict[str, Any], key: str) -> dict[str, Any]:
    """
    Args:
        schema: A tool's schema.
        key: A property to take out.

    Returns:
        The schema without it.
    """
    schema["properties"].pop(key, None)
    return schema


def _plan(openapi: Mapping[str, Any], tool: Tool) -> str | None:
    """
    Args:
        openapi: The OpenAPI document.
        tool: A tool.

    Returns:
        The operation that shows what the tool's would do, when it declares one.
    """
    operation = (openapi.get("paths") or {}).get(tool.path, {}).get(tool.method.lower())
    return plan_of(operation) if isinstance(operation, Mapping) else None


def _text(value: Any) -> str:
    """
    Args:
        value: A query value.

    Returns:
        It as the query string carries it.
    """
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)
