"""
Sites API endpoints.

This module is a client of :class:`~noust.managers.webserver.WebServerManager`,
through its nginx and apache bindings. It used to be a second implementation of
them, and the two had already diverged in production in the worst possible way:
the API wrote its virtual host to ``sites-available/example_com`` while every
manager, the CLI and the store use ``sites-available/example.com``. A site
created from the panel was therefore invisible to ``noust site list``, could not
be enabled, disabled or deleted from the CLI, and was skipped by certificate
issuance. The file name is now produced by exactly one piece of code -
:meth:`~noust.managers.webserver.WebServerManager.config_path` - and the panel
never renders a server block itself.

Two further rules, the same ones the services API follows:

- **Every domain is validated before it becomes a path.** The manager's
  ``config_path`` is the single place a domain turns into a file name, and it
  validates and contains it; :func:`noust.web.api.deps.strict_domain` refuses at
  the edge anything that would only survive by being rewritten.
- **Handlers are synchronous.** They call nginx, apache2ctl and systemctl,
  which block. Declared ``async def`` they would run on the event loop and
  freeze the panel for every other client; declared ``def``, FastAPI runs them
  in the threadpool.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.store import get_store
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.site_topology import TopologyProbe, default_probe, include_reader
from noust.managers.siteconf import (
    EditOp,
    IncludeReader,
    Kind,
    ParseError,
    SiteStructure,
    apply_ops,
    parse,
    route,
    structure,
)
from noust.managers.webserver import (
    SiteRow,
    WebServerManager,
    create_secured_site,
    delete_site_completely,
    list_all_sites,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.pydantic_compat import PYDANTIC_V2, dump_model

#: Cache of managers already built by :func:`_manager_for`, keyed by resolved
#: name. A site listing may hold rows for both backends, and building one
#: manager per row would mean re-detecting the same installation and
#: re-loading the same Jinja environment for every site of a mixed
#: deployment.
_ManagerCache = dict[str, WebServerManager]

router = APIRouter(route_class=NoustErrorRoute)

#: Web server used when none is installed and none was requested.
DEFAULT_WEBSERVER = "nginx"

#: Manager class per web server name. This is the allowlist for the
#: ``webserver`` field: anything not in it is a client error, not a fallback.
#: Typed as the concrete classes because both expose their configuration
#: directory as a class attribute, which detection reads without building one.
MANAGERS: dict[str, type[NginxManager] | type[ApacheManager]] = {
    "nginx": NginxManager,
    "apache": ApacheManager,
}


class SiteInfo(BaseModel):
    """
    A configured virtual host.

    Attributes:
        name: The domain the site is addressed by: its application's, else
            the file's name. What every ``/api/sites/{domain}`` route takes.
        webserver: Web server serving it.
        enabled: Whether the site is enabled.
        config_path: Absolute path of the configuration file.
        has_ssl: Whether the configuration carries TLS directives.
        server_names: Every name the configuration answers on - the primary
            domain and its aliases - read from the file's own directives.
            Empty when the configuration cannot be read.
        noust_managed: Whether Noust wrote the file (it carries Noust's
            marker). False for a site the operator wrote, which a deploy
            never rewrites.
        app: Domain of the application the file serves, when one records it.
        site_name: The file in the sites directory, which is not the domain
            for a site the operator named (``proggest`` for ``proggest.es``).
    """

    name: str
    webserver: str
    enabled: bool
    config_path: str
    has_ssl: bool = False
    server_names: list[str] = []
    noust_managed: bool = False
    app: str | None = None
    site_name: str = ""


class SiteListResponse(BaseModel):
    """Response for listing sites."""

    sites: list[SiteInfo]
    total: int
    webserver: str


class SiteActionResponse(BaseModel):
    """Response for site actions."""

    success: bool
    message: str
    site: str


class SiteConfigResponse(BaseModel):
    """Response carrying the raw configuration of a site."""

    site: str
    webserver: str
    config: str
    path: str


class ReloadResponse(BaseModel):
    """Response for a web server reload."""

    success: bool
    message: str
    webserver: str


class CreateSiteRequest(BaseModel):
    """
    Request to create a site.

    Attributes:
        domain: Domain to serve. It is also the configuration file name.
        webserver: Web server to configure, detected when omitted.
        template: Manager template to render.
        port: Upstream port for the proxy template.
        ssl: Render the template with TLS directives.
        enable: Enable the site once written.
    """

    domain: str
    webserver: str | None = None
    template: str = "proxy"
    port: int = Field(default=3000, ge=1, le=65535)
    ssl: bool = False
    enable: bool = True


class UpdateSiteConfigRequest(BaseModel):
    """Request to replace the raw configuration of a site."""

    config: str


class TestSiteConfigRequest(BaseModel):
    """Request to try a candidate configuration without saving it."""

    content: str


class SiteConfigTestResponse(BaseModel):
    """
    Outcome of testing a candidate configuration.

    Attributes:
        ok: Whether the web server would accept the configuration.
        output: The web server's own output, verbatim - present whichever way
            it answers, since nginx and apache2ctl both print a confirmation
            line even when there is nothing wrong.
    """

    ok: bool
    output: str


class SiteTemplatesResponse(BaseModel):
    """
    Templates a site can be created or rendered from.

    Attributes:
        templates: Template names, without their file suffix, sorted.
        webserver: Backend the templates belong to.
    """

    templates: list[str]
    webserver: str


# -- The structure of a site ---------------------------------------------
#
# These mirror the analyzer's dataclasses (noust.managers.siteconf.model) field
# for field, so the console's types are generated from the schema rather than
# guessed. The analyzer's ``to_dict()`` is what fills them.

#: Largest site configuration the read endpoints accept. A site file is tens
#: of kilobytes; a megabyte is a mistake or an attempt to make the analyzer
#: work for nothing.
MAX_CONFIG_LENGTH = 1024 * 1024

#: Most edit operations one request may carry.
MAX_EDIT_OPS = 200


class SiteRawDirective(BaseModel):
    """A directive as written: id, name, arguments, exact text, lines, comments."""

    id: str
    name: str
    args: list[str]
    text: str
    block: bool
    line: int
    end_line: int
    comments: list[str] = []
    modeled: bool = False


class SiteNote(BaseModel):
    """A comment of no single element: its text, its line, the id it follows."""

    text: str
    line: int
    after: str | None = None


class SiteListen(BaseModel):
    """One address and port a server accepts connections on."""

    id: str | None = None
    address: str | None = None
    port: int | None = None
    ssl: bool = False
    http2: bool = False
    quic: bool = False
    ipv6: bool = False
    default_server: bool = False
    raw: str = ""


class SiteTls(BaseModel):
    """A server's certificate, key and protocols, as the file names them."""

    certificate: str | None = None
    key: str | None = None
    protocols: list[str] = []


class SiteHeader(BaseModel):
    """A response header added, or a header passed to the backend."""

    id: str
    name: str
    value: str
    always: bool = False


class SiteReturn(BaseModel):
    """A server-level ``return``/``Redirect``."""

    id: str
    code: int | None = None
    destination: str | None = None


class SiteTarget(BaseModel):
    """
    What a location hands a request to.

    ``kind`` is ``proxy``, ``static``, ``return``, ``fastcgi`` or ``other``;
    the other fields are those of :class:`noust.managers.siteconf.model.Target`.
    """

    kind: str
    directive: str | None = None
    url: str | None = None
    protocol: str | None = None
    upstream: str | None = None
    host: str | None = None
    port: int | None = None
    address: str | None = None
    root: str | None = None
    alias: str | None = None
    inherited: bool = False
    code: int | None = None
    destination: str | None = None
    detail: str | None = None


class SiteSettings(BaseModel):
    """The settings the Structure view edits in line."""

    read_timeout: str | None = None
    send_timeout: str | None = None
    connect_timeout: str | None = None
    client_max_body_size: str | None = None
    limit_req: dict[str, Any] | None = None
    websocket: bool = False
    buffering: bool | None = None
    expires: str | None = None
    cache: str | None = None
    deny: bool = False
    headers: list[SiteHeader] = []
    proxy_headers: list[SiteHeader] = []


class SiteLocation(BaseModel):
    """
    A location (or an Apache ``ProxyPass`` rule).

    ``locations`` are the nested ones in file order; ``evaluation_order`` their
    ids in the order the web server tries them.
    """

    id: str
    modifier: str
    path: str
    source: str
    line: int
    end_line: int
    target: SiteTarget
    settings: SiteSettings
    directives: list[SiteRawDirective] = []
    locations: list[SiteLocation] = []
    evaluation_order: list[str] = []
    comments: list[str] = []
    notes: list[SiteNote] = []


class SiteServer(BaseModel):
    """A virtual server: listens, names, TLS, locations and the rest, raw."""

    id: str
    line: int
    end_line: int
    listens: list[SiteListen] = []
    names: list[str] = []
    tls: SiteTls | None = None
    http2: bool = False
    root: str | None = None
    headers: list[SiteHeader] = []
    gzip: bool | None = None
    client_max_body_size: str | None = None
    returns: SiteReturn | None = None
    rewrites: list[str] = []
    locations: list[SiteLocation] = []
    evaluation_order: list[str] = []
    directives: list[SiteRawDirective] = []
    comments: list[str] = []
    notes: list[SiteNote] = []


class SiteUpstreamServer(BaseModel):
    """One backend of an upstream; ``source`` is the included file it comes from."""

    address: str
    params: list[str] = []
    id: str | None = None
    source: str | None = None


class SiteUpstream(BaseModel):
    """A named group of backends; ``source`` set when an include defines it (read-only)."""

    id: str
    name: str
    line: int
    end_line: int
    servers: list[SiteUpstreamServer] = []
    keepalive: int | None = None
    directives: list[SiteRawDirective] = []
    comments: list[str] = []
    notes: list[SiteNote] = []
    used_by: list[str] = []
    source: str | None = None


class SiteIncludedFile(BaseModel):
    """A file an include brought in, read-only."""

    path: str
    text: str


class SiteInclude(BaseModel):
    """An include and what it resolved to, or why it could not be."""

    id: str
    parent: str
    pattern: str
    line: int
    files: list[SiteIncludedFile] = []
    error: str | None = None


class SiteStructureModel(BaseModel):
    """Everything in a site file, structured (``kind`` is ``nginx`` or ``apache``)."""

    kind: str
    servers: list[SiteServer] = []
    upstreams: list[SiteUpstream] = []
    includes: list[SiteInclude] = []
    directives: list[SiteRawDirective] = []
    notes: list[SiteNote] = []


if not PYDANTIC_V2:  # pragma: no cover - exercised by the pydantic 1.10 CI job
    # pydantic 1 resolves a model that names itself only when asked to.
    SiteLocation.update_forward_refs()


class SiteParseFailure(BaseModel):
    """
    Why a configuration could not be analysed.

    Attributes:
        line: 1-based line of the offending character.
        column: 1-based column.
        message: What is wrong there, in the web server's terms.
    """

    line: int
    column: int
    message: str


class SiteStructureResponse(BaseModel):
    """
    The structure of a site's file or of a draft of it.

    Attributes:
        site: The domain asked about.
        webserver: ``nginx`` or ``apache``.
        path: The site's file.
        structure: The model, None when the text does not parse.
        error: Why it does not parse, None when it does. A draft that does not
            parse is an answer, not a failure: the console shows the line and
            sends the operator to the text.
    """

    site: str
    webserver: str
    path: str
    structure: SiteStructureModel | None = None
    error: SiteParseFailure | None = None


class SiteDraftRequest(BaseModel):
    """A draft of a site's configuration."""

    config: str = Field(max_length=MAX_CONFIG_LENGTH)


class SiteEditRequest(BaseModel):
    """
    Edit operations to apply to a text.

    Attributes:
        config: The text (saved or a draft).
        ops: The operations of :mod:`noust.managers.siteconf.edit`, in order;
            each an object with ``op`` and its fields.
    """

    config: str = Field(max_length=MAX_CONFIG_LENGTH)
    ops: list[dict[str, Any]] = Field(default_factory=list)


class SiteEditResponse(BaseModel):
    """
    The result of edit operations. Nothing was written.

    Attributes:
        config: The edited text.
        structure: Its model.
        changed_lines: Lines that differ from the text sent.
    """

    config: str
    structure: SiteStructureModel
    changed_lines: int


class SiteRouteRequest(BaseModel):
    """
    A request to trace through a site.

    Attributes:
        config: A draft to trace through; the saved file when absent.
        host: The Host header.
        path: The request path (a query string is ignored).
        scheme: ``http`` or ``https``.
        port: The port it arrives on; the scheme's default when absent.
    """

    config: str | None = Field(default=None, max_length=MAX_CONFIG_LENGTH)
    host: str = Field(min_length=1, max_length=253)
    path: str = Field(default="/", max_length=4096)
    scheme: Literal["http", "https"] = "https"
    port: int | None = Field(default=None, ge=1, le=65535)


class SiteRouteStep(BaseModel):
    """
    One step of a route's explanation.

    Attributes:
        code: The step, as a key the console translates.
        params: The values the sentence names.
        text: The sentence in English.
    """

    code: str
    params: dict[str, Any] = {}
    text: str


class SiteRouteResponse(BaseModel):
    """
    Which server and location answer a request, and why.

    Attributes:
        server_id: The server that answers; None when none listens.
        location_id: The location that answers; None when the server does.
        steps: The explanation in English, one sentence per step.
        trace: The same steps as codes and parameters.
        highlight: Ids along the path, server first, then locations, then
            the upstream (``u:name``), for the diagram.
        redirect: Where the web server's automatic 301 sends the client.
    """

    server_id: str | None = None
    location_id: str | None = None
    steps: list[str] = []
    trace: list[SiteRouteStep] = []
    highlight: list[str] = []
    redirect: str | None = None


class SitePortOwner(BaseModel):
    """
    Who holds a port: ``kind`` is ``app``, ``compose``, ``container``, ``unit``
    or ``process``, with the fields that kind has.
    """

    kind: str
    app: str | None = None
    project: str | None = None
    service: str | None = None
    container: str | None = None
    unit: str | None = None
    process: str | None = None
    pid: int | None = None


class SiteBackend(BaseModel):
    """
    One address the site reaches, live.

    Attributes:
        address: ``host:port`` or ``unix:/path``.
        written: How the site spells it (upstream server addresses, proxy
            URLs), to match the structure's elements.
        host: The host.
        port: The port.
        local: Whether it is this machine; only those are probed.
        upstreams: Upstream names listing it.
        locations: Location ids reaching it.
        owner: Who holds the port, None when nobody does or unknown.
        listening: Whether a socket listens on it; None when unknown.
        reachable: Whether it accepts a connection (1 s, cached 10 s); None
            when not probed.
    """

    address: str
    written: list[str] = []
    host: str | None = None
    port: int | None = None
    local: bool = False
    upstreams: list[str] = []
    locations: list[str] = []
    owner: SitePortOwner | None = None
    listening: bool | None = None
    reachable: bool | None = None


class SiteCertificate(BaseModel):
    """The certificate a server presents, with its expiry when certbot manages it."""

    server_id: str
    path: str
    name: str | None = None
    domains: list[str] = []
    expiry: str | None = None
    days_left: int | None = None


class SiteTopologyResponse(SiteStructureResponse):
    """
    The structure of the saved site plus what is behind it now.

    Attributes:
        backends: Every address the site reaches, with its owner and state.
        certificates: The certificate of each server that presents one.
        docker: Whether Docker answered; when not, containers are not owners.
    """

    backends: list[SiteBackend] = []
    certificates: list[SiteCertificate] = []
    docker: bool = False


def detect_webserver() -> str:
    """
    Work out which web server this host uses.

    Returns:
        ``nginx`` or ``apache``. Nginx is the answer when neither is installed,
        because it is what a fresh deployment will configure.
    """
    for name, manager_class in MANAGERS.items():
        if manager_class(verbose=False).is_installed():
            return name

    for name, manager_class in MANAGERS.items():
        if manager_class.SITES_AVAILABLE.exists():
            return name

    return DEFAULT_WEBSERVER


def _manager_for(webserver: str | None) -> tuple[str, WebServerManager]:
    """
    Resolve a web server name to its manager.

    Args:
        webserver: Requested web server, or None to detect one.

    Returns:
        Tuple of the resolved name and its manager.

    Raises:
        ValidationError: When the name is not a web server Noust supports.
    """
    name = (webserver or detect_webserver()).lower()
    manager_class = MANAGERS.get(name)
    if manager_class is None:
        raise ValidationError(
            f"Unknown web server: {webserver!r}",
            details=f"Use one of: {', '.join(sorted(MANAGERS))}.",
        )
    return name, manager_class(verbose=False)


def _manager_cached(cache: _ManagerCache, webserver: str | None) -> WebServerManager:
    """
    Resolve a web server name to its manager, reusing one already built.

    Args:
        cache: Managers already resolved in this request, keyed by name.
        webserver: Requested web server, or None to detect one.

    Returns:
        The manager, built once per distinct name.
    """
    name, manager = _manager_for(webserver)
    return cache.setdefault(name, manager)


def _site_info(row: SiteRow) -> SiteInfo:
    """
    Describe a site row the way the API reports it.

    Args:
        row: The row :func:`~noust.managers.webserver.list_all_sites` built.

    Returns:
        The API record; one translation, so the list and the detail of a site
        cannot say different things.
    """
    return SiteInfo(
        name=row.domain,
        webserver=row.webserver,
        enabled=row.enabled,
        config_path=row.config_path,
        has_ssl=row.has_ssl,
        server_names=list(row.server_names),
        noust_managed=row.noust_managed,
        app=row.app,
        site_name=row.name,
    )


def _site_rows(cache: _ManagerCache) -> list[SiteRow]:
    """
    List the sites of every web server this API can drive.

    Args:
        cache: Managers already resolved in this request.

    Returns:
        The rows of :func:`~noust.managers.webserver.list_all_sites`.
    """
    managers = [_manager_cached(cache, name) for name in MANAGERS]
    return list_all_sites(managers=managers, store=get_store())


@router.get("", response_model=SiteListResponse)
def list_sites(session: Annotated[dict, Depends(get_current_session)]) -> SiteListResponse:
    """
    List every configured site.

    Args:
        session: The authenticated session.

    Returns:
        The store's sites and the files in each web server's sites directory,
        together: a site the operator wrote by hand is listed beside the ones
        Noust wrote, and each entry says which it is (``noust_managed``) and
        which application it serves (``app``). Every entry carries the server
        names its own configuration answers on.
    """
    webserver, manager = _manager_for(None)
    cache: _ManagerCache = {webserver: manager}

    sites = [_site_info(row) for row in _site_rows(cache)]

    return SiteListResponse(sites=sites, total=len(sites), webserver=webserver)


@router.get("/templates", response_model=SiteTemplatesResponse)
def list_site_templates(
    session: Annotated[dict, Depends(get_current_session)],
) -> SiteTemplatesResponse:
    """
    List the site templates available for the detected web server.

    Registered before ``/{domain}`` so the literal path wins, the same reason
    ``/reload`` is declared here rather than after it: the templates
    directory :meth:`~noust.managers.webserver.WebServerManager.list_templates`
    reads is the one source of truth this shares with ``POST /api/sites``,
    which refuses a template not on this list.

    Args:
        session: The authenticated session.

    Returns:
        The template names and the web server they belong to.
    """
    webserver, manager = _manager_for(None)
    return SiteTemplatesResponse(templates=manager.list_templates(), webserver=webserver)


@router.post("", response_model=SiteActionResponse)
def create_site(
    data: CreateSiteRequest, session: Annotated[dict, Depends(get_current_session)]
) -> SiteActionResponse:
    """
    Create a virtual host from one of the manager's templates.

    The configuration file is named by the manager, which is what the CLI, the
    store and certificate issuance all expect.

    Args:
        data: The create request.
        session: The authenticated session.

    Returns:
        The action outcome.

    Raises:
        HTTPException: 409 when the site already exists.
        ValidationError: When the template is not one the backend offers.
        DomainError: When the domain is not acceptable.
        SiteError: When the manager cannot write the configuration.
    """
    domain = strict_domain(data.domain)
    webserver, manager = _manager_for(data.webserver)

    templates = manager.list_templates()
    if data.template not in templates:
        raise ValidationError(
            f"Unknown template: {data.template!r}",
            details=f"Available {webserver} templates: {', '.join(templates) or 'none'}.",
        )

    if manager.site_exists(domain):
        raise HTTPException(status_code=409, detail=f"Site already exists: {domain}")

    # Routed through create_secured_site so that a request with ssl=true never
    # renders certificate paths into the vhost before a certificate exists.
    # This endpoint used to set the ssl flag and render once, which is why an
    # SSL site created from the panel failed nginx's own config test: the
    # certificate files it pointed at had never been requested.
    outcome = create_secured_site(
        domain,
        manager=manager,
        webserver=webserver,
        template=data.template,
        port=data.port,
        ssl=data.ssl,
        enable=data.enable,
    )

    if outcome.ssl_requested and not outcome.ssl_enabled:
        message = (
            f"Site created: {domain}. TLS was requested but not enabled"
            f"{f': {outcome.certificate_error}' if outcome.certificate_error else ''}."
        )
    elif outcome.ssl_enabled:
        message = f"Site created with SSL: {domain}."
    else:
        message = f"Site created: {domain}. Reload {webserver} to serve it."

    return SiteActionResponse(success=True, message=message, site=domain)


@router.post("/reload", response_model=ReloadResponse)
def reload_webserver(session: Annotated[dict, Depends(get_current_session)]) -> ReloadResponse:
    """
    Test and reload the web server configuration.

    Registered before ``/{domain}`` so the literal path wins: FastAPI matches
    routes in registration order, and a parametrised route declared first would
    swallow this one.

    Args:
        session: The authenticated session.

    Returns:
        The reload outcome.

    Raises:
        ValidationError: When the configuration does not pass its own test;
            ``details`` and ``output`` both carry the web server's own output
            verbatim, and the running configuration is kept.
        HTTPException: 500 when the reload itself fails.
    """
    webserver, manager = _manager_for(None)

    errors = manager.config_errors()
    if errors is not None:
        raise ValidationError(
            f"{webserver} configuration test failed; the running config was kept",
            details=errors,
            output=errors,
        )

    if not manager.reload():
        raise HTTPException(status_code=500, detail=f"Failed to reload {webserver}")

    return ReloadResponse(success=True, message=f"{webserver} reloaded", webserver=webserver)


@router.get("/{domain}", response_model=SiteInfo)
def get_site(domain: str, session: Annotated[dict, Depends(get_current_session)]) -> SiteInfo:
    """
    Describe one site.

    Args:
        domain: Domain of the site.
        session: The authenticated session.

    Returns:
        The site description.

    Raises:
        HTTPException: 404 when no such site exists.
        DomainError: When the domain is not acceptable.
    """
    validated = strict_domain(domain)

    webserver, manager = _manager_for(None)
    cache: _ManagerCache = {webserver: manager}

    # The same rows the list is made of, so one site is described by one piece
    # of code. When a domain is on both web servers, the detected one answers.
    matches = [row for row in _site_rows(cache) if validated in (row.domain, row.name)]
    row = next((row for row in matches if row.webserver == webserver), None) or (
        matches[0] if matches else None
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Site not found: {validated}")

    return _site_info(row)


@router.get("/{domain}/config", response_model=SiteConfigResponse)
def get_site_config(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> SiteConfigResponse:
    """
    Read the raw configuration of a site.

    Args:
        domain: Domain of the site.
        session: The authenticated session.

    Returns:
        The configuration content.

    Raises:
        HTTPException: 404 when no such site exists.
        DomainError: When the domain is not acceptable.
    """
    validated = strict_domain(domain)
    webserver, manager = _manager_for(None)

    content = manager.get_site_config(validated)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Site not found: {validated}")

    return SiteConfigResponse(
        site=validated,
        webserver=webserver,
        config=content,
        path=str(manager.config_path(validated)),
    )


@router.post("/{domain}/config/test", response_model=SiteConfigTestResponse)
def test_site_config(
    domain: str,
    data: TestSiteConfigRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> SiteConfigTestResponse:
    """
    Try a candidate configuration against the web server, without saving it.

    Reuses :meth:`~noust.managers.webserver.WebServerManager.test_config_text`,
    the exact staging and syntax check ``PUT /{domain}/config`` validates
    through before it writes anything - one implementation, so the answer
    this gives is the answer saving would get. The site named in the path
    need not exist yet: this only asks the web server about the text, it
    never reads or touches the file on disk.

    Args:
        domain: Domain the configuration is meant for.
        data: The candidate configuration.
        session: The authenticated session.

    Returns:
        Whether the web server would accept it, and its own output verbatim.

    Raises:
        DomainError: When the domain is not acceptable.
        NginxError: When the nginx snippet cannot be staged.
        ApacheError: When the apache snippet cannot be staged.
    """
    validated = strict_domain(domain)
    _, manager = _manager_for(None)

    ok, output = manager.test_config_text(data.content, domain=validated)

    return SiteConfigTestResponse(ok=ok, output=output)


# -- Reading a site: structure, edits, routes, topology ---------------------
#
# None of these writes anything. They read the saved file or take a draft, run
# the analyzer, and answer; the console's visual editor turns its result into
# text that is saved by PUT /config like any other edit.


def topology_probe() -> TopologyProbe:
    """
    The probe the topology endpoint asks, shared so its cache is.

    Returns:
        The process-wide probe.
    """
    return default_probe()


def _site_kind(webserver: str) -> Kind:
    """The analyzer's dialect for a web server name."""
    return "apache" if webserver == "apache" else "nginx"


def _reader(manager: WebServerManager) -> IncludeReader:
    """
    The include reader for a manager's sites.

    Args:
        manager: The web server's manager.

    Returns:
        A reader limited to the web server's directory and Noust's upstream
        files.
    """
    extra = [manager.backend.upstreams_dir] if manager.backend.upstreams_dir else []
    return include_reader(manager.sites_available.parent, extra_roots=extra)


def _saved_config(domain: str) -> tuple[str, WebServerManager, str, str]:
    """
    Read a site's saved configuration.

    Args:
        domain: The site, as the path names it.

    Returns:
        The validated domain, the manager, the file's path and its text.

    Raises:
        HTTPException: 404 when the site has no file.
        DomainError: When the domain is not acceptable.
    """
    validated = strict_domain(domain)
    _, manager = _manager_for(None)
    text = manager.get_site_config(validated)
    if text is None:
        raise HTTPException(status_code=404, detail=f"Site not found: {validated}")
    return validated, manager, str(manager.config_path(validated)), text


def _structure_response(
    site: str, manager: WebServerManager, path: str, text: str
) -> tuple[SiteStructureResponse, SiteStructure | None]:
    """
    Analyse a text into the structure response.

    Args:
        site: The domain.
        manager: The web server's manager.
        path: The site's file.
        text: The configuration.

    Returns:
        The response, and the model when the text parsed.
    """
    webserver = manager.backend.name
    try:
        model = structure(parse(text, _site_kind(webserver)), read_include=_reader(manager))
    except ParseError as exc:
        failure = SiteParseFailure(line=exc.line, column=exc.column, message=exc.message)
        return SiteStructureResponse(site=site, webserver=webserver, path=path, error=failure), None
    return (
        SiteStructureResponse(
            site=site,
            webserver=webserver,
            path=path,
            structure=SiteStructureModel(**model.to_dict()),
        ),
        model,
    )


def _unparsable(exc: ParseError) -> ValidationError:
    """
    Say that a text sent to be edited or traced does not parse.

    Args:
        exc: The analyzer's error.

    Returns:
        A validation error on the ``config`` field; its hint carries the
        line and column.
    """
    return ValidationError(
        f"The configuration does not parse: {exc.message}",
        details=exc.details,
        field="config",
    )


@router.get("/{domain}/structure", response_model=SiteStructureResponse)
def get_site_structure(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> SiteStructureResponse:
    """
    The structure of a site's saved file.

    Args:
        domain: Domain of the site.
        session: The authenticated session.

    Returns:
        The model (servers, locations in evaluation order, upstreams,
        includes resolved inside the web server's directory, comments, raw
        directives), or the parse error with its line.

    Raises:
        HTTPException: 404 when no such site exists.
        DomainError: When the domain is not acceptable.
    """
    site, manager, path, text = _saved_config(domain)
    response, _ = _structure_response(site, manager, path, text)
    return response


@router.post("/{domain}/structure", response_model=SiteStructureResponse)
def post_site_structure(
    domain: str,
    data: SiteDraftRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> SiteStructureResponse:
    """
    The structure of a draft. Nothing is written; the site need not exist.

    Args:
        domain: Domain the draft is meant for.
        data: The draft.
        session: The authenticated session.

    Returns:
        The draft's model, or the parse error with its line.

    Raises:
        DomainError: When the domain is not acceptable.
    """
    validated = strict_domain(domain)
    _, manager = _manager_for(None)
    response, _ = _structure_response(
        validated, manager, str(manager.config_path(validated)), data.config
    )
    return response


@router.post("/{domain}/config/edit", response_model=SiteEditResponse)
def edit_site_config(
    domain: str,
    data: SiteEditRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> SiteEditResponse:
    """
    Apply edit operations to a text and return the result, without saving it.

    Each operation changes only the bytes of the element it names; the rest
    of the text comes back exactly as it was sent.

    Args:
        domain: Domain the text is meant for.
        data: The text and the operations.
        session: The authenticated session.

    Returns:
        The edited text, its structure and how many lines changed.

    Raises:
        ValidationError: When the text does not parse (``fields.config``,
            the line in the hint) or an operation is malformed or names
            nothing (``fields["ops[2].target"]``).
        DomainError: When the domain is not acceptable.
    """
    strict_domain(domain)
    _, manager = _manager_for(None)
    # Counted here rather than in the model: pydantic 1 and 2 spell a list's
    # length limit differently, and the web layer runs on both.
    if len(data.ops) > MAX_EDIT_OPS:
        raise ValidationError(
            f"Too many operations: {len(data.ops)}",
            details=f"Send at most {MAX_EDIT_OPS} operations per request.",
            field="ops",
        )
    # apply_ops checks every operation's shape and names the one at fault.
    ops = cast(list[EditOp], data.ops)
    try:
        result = apply_ops(
            data.config,
            _site_kind(manager.backend.name),
            ops,
            read_include=_reader(manager),
        )
    except ParseError as exc:
        raise _unparsable(exc) from exc
    return SiteEditResponse(
        config=result.config,
        structure=SiteStructureModel(**result.structure.to_dict()),
        changed_lines=result.changed_lines,
    )


@router.post("/{domain}/route", response_model=SiteRouteResponse)
def route_site_request(
    domain: str,
    data: SiteRouteRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> SiteRouteResponse:
    """
    Say which server and location answer a request, and why.

    The web server's own selection is replayed over the structure of the
    saved file, or of the draft when one is sent.

    Args:
        domain: Domain of the site.
        data: The draft (optional) and the request.
        session: The authenticated session.

    Returns:
        The server and location ids, the explanation as English sentences
        and as codes with parameters, the ids to highlight and the automatic
        redirect when there is one.

    Raises:
        HTTPException: 404 when no draft is sent and the site has no file.
        ValidationError: When the text does not parse.
        DomainError: When the domain is not acceptable.
    """
    if data.config is None:
        _, manager, _, text = _saved_config(domain)
    else:
        strict_domain(domain)
        _, manager = _manager_for(None)
        text = data.config
    try:
        tree = parse(text, _site_kind(manager.backend.name))
    except ParseError as exc:
        raise _unparsable(exc) from exc
    result = route(
        structure(tree, read_include=_reader(manager)),
        host=data.host,
        path=data.path,
        scheme=data.scheme,
        port=data.port,
    )
    return SiteRouteResponse(**result.to_dict())


@router.get("/{domain}/topology", response_model=SiteTopologyResponse)
def get_site_topology(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> SiteTopologyResponse:
    """
    The saved site's structure and what is behind it now.

    For every address the site reaches: who holds the port (a Noust
    application, a Compose service by its labels, a container, a systemd
    unit, a process), whether it accepts a connection (only on this machine,
    one second, cached ten seconds) and, for each server, its certificate's
    expiry.

    Args:
        domain: Domain of the site.
        session: The authenticated session.

    Returns:
        The structure response with ``backends``, ``certificates`` and
        ``docker``; only the parse error when the file does not parse.

    Raises:
        HTTPException: 404 when no such site exists.
        DomainError: When the domain is not acceptable.
    """
    site, manager, path, text = _saved_config(domain)
    response, model = _structure_response(site, manager, path, text)
    if model is None:
        return SiteTopologyResponse(**dump_model(response))
    facts = topology_probe().topology(model).to_dict()
    return SiteTopologyResponse(**dump_model(response), **facts)


@router.put("/{domain}/config", response_model=SiteActionResponse)
def update_site_config(
    domain: str,
    data: UpdateSiteConfigRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> SiteActionResponse:
    """
    Replace the raw configuration of a site.

    The path comes from the manager, so a hand-edited configuration can only
    ever overwrite the file that domain already owns. The manager validates
    the text against the web server itself before persisting it: a broken
    configuration used to be written unchecked and took the site down at the
    next reload.

    Args:
        domain: Domain of the site.
        data: The new configuration.
        session: The authenticated session.

    Returns:
        The action outcome.

    Raises:
        HTTPException: 404 when no such site exists.
        ValidationError: When the web server rejects the configuration; its
            output travels verbatim in both ``hint`` and the dedicated
            ``output`` field of the error body, and the file on disk is left
            as it was. ``POST /{domain}/config/test`` answers the same
            question beforehand, without this side effect.
        SiteError: When the file cannot be staged or written.
        DomainError: When the domain is not acceptable.
    """
    validated = strict_domain(domain)
    webserver, manager = _manager_for(None)

    if not manager.site_exists(validated):
        raise HTTPException(status_code=404, detail=f"Site not found: {validated}")

    manager.replace_site_config(validated, data.config)

    return SiteActionResponse(
        success=True,
        message=f"Configuration updated for {validated}. Reload {webserver} to apply it.",
        site=validated,
    )


@router.post("/{domain}/enable", response_model=SiteActionResponse)
def enable_site(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> SiteActionResponse:
    """
    Enable a site and reload the web server.

    Args:
        domain: Domain of the site.
        session: The authenticated session.

    Returns:
        The action outcome.

    Raises:
        SiteError: When the manager refuses the operation.
        DomainError: When the domain is not acceptable.
    """
    validated = strict_domain(domain)
    _, manager = _manager_for(None)

    manager.enable_site(validated)
    manager.reload()

    return SiteActionResponse(success=True, message=f"Site enabled: {validated}", site=validated)


@router.post("/{domain}/disable", response_model=SiteActionResponse)
def disable_site(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> SiteActionResponse:
    """
    Disable a site and reload the web server.

    Args:
        domain: Domain of the site.
        session: The authenticated session.

    Returns:
        The action outcome.

    Raises:
        SiteError: When the manager refuses the operation.
        DomainError: When the domain is not acceptable.
    """
    validated = strict_domain(domain)
    _, manager = _manager_for(None)

    manager.disable_site(validated)
    manager.reload()

    return SiteActionResponse(success=True, message=f"Site disabled: {validated}", site=validated)


@router.delete("/{domain}", response_model=SiteActionResponse)
def delete_site(
    domain: str, session: Annotated[dict, Depends(require_elevated)]
) -> SiteActionResponse:
    """
    Delete a site's virtual host on every web server backend, and its
    certificate.

    Args:
        domain: Domain of the site.
        session: The authenticated session.

    Returns:
        The action outcome.

    Raises:
        HTTPException: 404 when nothing was found for the domain on either
            backend, and no certificate either.
        DomainError: When the domain is not acceptable.
    """
    validated = strict_domain(domain)

    # delete_site_completely walks both nginx and apache and the certificate.
    # This endpoint used to ask only the detected backend, so a site created
    # on the other one - or a certificate left behind after a manual web
    # server switch - outlived every delete request that reached this route.
    deletion = delete_site_completely(validated)

    if not deletion.removed_anything:
        raise HTTPException(status_code=404, detail=f"Site not found: {validated}")

    return SiteActionResponse(success=True, message=f"Site deleted: {validated}", site=validated)
