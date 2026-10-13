# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Recipes: the catalogue Noust deploys applications from (2.3, 3.4).

A client of :mod:`noust.recipes`, like ``noust recipe``: it lists, searches,
describes and installs recipes, and says which installed ones are behind the
catalogue. Installing is the one deployment ``POST /api/apps`` queues; updating
is the one update ``noust update`` runs; neither is a second implementation.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.recipes import (
    RecipeNotFoundError,
    categories,
    get_recipe,
    logo_svg,
    search_recipes,
)
from noust.recipes.catalog import catalogue_problems
from noust.recipes.deploy import require_available
from noust.recipes.installed import get_installed
from noust.recipes.model import ORIGINS, RecipeError
from noust.recipes.private import add_recipes, read_bundles, remove_recipe, staged_document
from noust.recipes.requirements import check_requirements
from noust.recipes.sources import MAX_RECIPE_BYTES
from noust.recipes.updates import installed_statuses, status_of
from noust.recipes.variables import require_inputs
from noust.web.api.apps import CreateAppRequest, create_app
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    ensure_elevated,
    require_elevated,
    strict_domain,
)
from noust.web.api.scope import require_instance_scope
from noust.web.auth import actor_label
from noust.web.jobs import JobType, get_job_manager
from noust.web.permissions import Permission
from noust.web.permissions.enforce import check_permission
from noust.web.recipe_jobs import update_recipe_job

router = APIRouter(route_class=NoustErrorRoute)

#: Sent with a logo. The Content-Security-Policy is the console's own, which the
#: server's middleware puts on every response: the file needs nothing it forbids.
_LOGO_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "public, max-age=3600",
}


class RecipeSummaryOut(BaseModel):
    """
    One recipe in the catalogue.

    Attributes:
        name: What ``recipe`` takes in ``POST /api/apps``.
        title: What it is called.
        slogan: One line for the catalogue card.
        description: One or two sentences.
        homepage: The project's site.
        docs: The project's documentation.
        repository: The project's source code.
        license: The project's licence.
        min_noust: The oldest Noust that can install it, when it asks for one.
        architectures: What it runs on; empty for any. Checked at install.
        category: One of the closed list (``GET /api/recipes/categories``).
        tags: Lower-case labels.
        logo: Where the logo is served, or None.
        version: The version of the application the recipe pins.
        revision: The recipe's own revision of that version.
        updated: When the recipe last changed (ISO date).
        kind: ``native`` or ``compose``.
        runs_in_docker: Whether it runs in containers.
        available: Whether this release can deploy it.
        unavailable_reason: Why not, when it cannot.
        app_type: The deployer that builds it; None when unavailable.
        database: The SQL engine it is given, if any.
        dependencies: The services it asks Noust to provide.
        requires: What the server needs, in words.
        digest_verified: Compose: every image carries a digest read from its
            registry. None for a native recipe.
        schema_version: The format version the file is written in.
        origin: ``shipped`` (the package), ``catalog`` (a signed catalogue
            package) or ``operator`` (the server's own recipes).
    """

    name: str
    title: str
    slogan: str = ""
    description: str
    homepage: str
    docs: str | None = None
    repository: str | None = None
    license: str | None = None
    min_noust: str | None = None
    architectures: list[str] = []
    category: str = "other"
    tags: list[str] = []
    logo: str | None = None
    version: str | None = None
    revision: int = 1
    updated: str | None = None
    kind: str = "native"
    runs_in_docker: bool = False
    available: bool
    unavailable_reason: str | None = None
    app_type: str | None = None
    database: str | None = None
    dependencies: list[str] = []
    requires: list[str] = []
    digest_verified: bool | None = None
    schema_version: int = 2
    origin: str = "shipped"


class RecipeProblemOut(BaseModel):
    """
    A file in the server's own recipes that is not a usable recipe.

    Attributes:
        name: The recipe's name, or the file's.
        origin: Where it was found.
        location: Its path.
        error: What is wrong.
        details: What to do about it.
    """

    name: str
    origin: str
    location: str
    error: str
    details: str | None = None


class RecipeListOut(BaseModel):
    """
    The catalogue, or the part of it a search found.

    Attributes:
        items: The recipes, the ones this release can deploy first.
        total: How many there are.
        problems: Files in the server's own recipes that were left out, and why.
    """

    items: list[RecipeSummaryOut]
    total: int = 0
    problems: list[RecipeProblemOut] = []


class CategoryOut(BaseModel):
    """
    One category of the catalogue.

    Attributes:
        category: The category.
        count: How many recipes it holds.
    """

    category: str
    count: int


class CategoryListOut(BaseModel):
    """The categories that hold at least one recipe."""

    items: list[CategoryOut]


class RecipeSourceOut(BaseModel):
    """
    Where a native recipe's code comes from.

    Attributes:
        kind: ``git``, ``archive`` or ``template``.
        url: The repository or archive.
        ref: The git tag deployed.
        sha256: The archive's pinned checksum.
        checksum_url: Where the archive's checksum is published.
        files: For a template, the files rendered from the recipe.
    """

    kind: str
    url: str | None = None
    ref: str | None = None
    sha256: str | None = None
    checksum_url: str | None = None
    files: list[str] = []


class RecipeEnvOut(BaseModel):
    """
    One variable a recipe sets.

    Attributes:
        name: The variable.
        generated: Whether Noust gives its value (a secret, a database
            credential, the domain); either way ``env_vars`` overrides it.
    """

    name: str
    generated: bool


class RecipeVariableOut(BaseModel):
    """
    One variable of a recipe, with where its value comes from.

    Attributes:
        name: The environment variable.
        origin: ``generated``, ``deployment``, ``provisioned``, ``composed``,
            ``default`` or ``required``.
        input: Whether the operator is asked for it at install.
        required: Whether installation stops until it has a value.
        secret: Whether its value must not be shown in clear.
        description: What it is for.
        default: Its value when the operator gives none; never a secret.
        generate: The form it is generated in, when it is.
    """

    name: str
    origin: str
    input: bool
    required: bool
    secret: bool
    description: str = ""
    default: str | None = None
    generate: str | None = None


class RecipeBuildOut(BaseModel):
    """A native recipe's install, build and start commands."""

    base_directory: str | None = None
    install: str | None = None
    build: str | None = None
    start: str | None = None
    publish_directory: str | None = None
    runtime: str | None = None


class RecipeImageOut(BaseModel):
    """
    One image of a Compose recipe.

    Attributes:
        service: The Compose service.
        repository: The image without tag or digest.
        tag: The tag.
        digest: The ``sha256:`` digest.
    """

    service: str
    repository: str
    tag: str
    digest: str | None = None


class RecipePublicPortOut(BaseModel):
    """
    A port a Compose recipe opens to the network.

    Attributes:
        port: The port.
        protocol: ``tcp`` or ``udp``.
        purpose: What it is for.
    """

    port: int
    protocol: str
    purpose: str


class RecipeChangeOut(BaseModel):
    """
    What a version of a recipe changed.

    Attributes:
        version: The version.
        summary: One sentence.
    """

    version: str
    summary: str


class RecipeHealthOut(BaseModel):
    """What the health gate asks of the application."""

    path: str
    expect: str | None = None
    timeout: int | None = None


class RecipeOut(RecipeSummaryOut):
    """
    One recipe in full.

    Attributes:
        source: Where its code comes from; None for a Compose recipe or an
            unavailable one.
        layout: ``releases`` or ``inplace``.
        port: Its default port, when it runs a process.
        build: A native recipe's commands.
        variables: Its variables and where each value comes from.
        env: The variables, as the review step reads them.
        images: A Compose recipe's images, as pinned.
        public_ports: What a Compose recipe opens to the network.
        persistent_paths: What survives every release in ``shared/``.
        health: Its health check.
        notes: What the operator is told after the deploy, with ``{{ url }}``
            and the like still to be filled in.
        changelog: What the latest versions changed.
    """

    source: RecipeSourceOut | None = None
    layout: str = "releases"
    port: int | None = None
    build: RecipeBuildOut | None = None
    variables: list[RecipeVariableOut] = []
    env: list[RecipeEnvOut] = []
    images: list[RecipeImageOut] = []
    public_ports: list[RecipePublicPortOut] = []
    persistent_paths: list[str] = []
    health: RecipeHealthOut | None = None
    notes: list[str] = []
    changelog: list[RecipeChangeOut] = []


class InstalledRecipeOut(BaseModel):
    """
    An application installed from a recipe, against the catalogue.

    Attributes:
        domain: The application.
        recipe: The recipe it was installed from.
        kind: ``native`` or ``compose``.
        installed_version: The version it runs.
        installed_revision: The recipe's revision of that version.
        latest_version: The catalogue's version; None when the recipe left it.
        latest_revision: The catalogue's revision of it.
        update_available: Whether the catalogue is ahead and can be deployed.
        reason: Why it cannot be updated, when it cannot.
        changes: What the versions in between changed.
    """

    domain: str
    recipe: str
    kind: str
    installed_version: str
    installed_revision: int
    latest_version: str | None = None
    latest_revision: int | None = None
    update_available: bool
    reason: str | None = None
    changes: list[RecipeChangeOut] = []


class InstalledRecipeListOut(BaseModel):
    """Every application installed from a recipe."""

    items: list[InstalledRecipeOut]


class RecipeInstallRequest(BaseModel):
    """
    Request to install a recipe on a domain.

    Attributes:
        domain: The domain it is served on.
        variables: Values for the recipe's variables: the ``required`` ones
            must be among them, and any other overrides what the recipe sets.
        port: The port to listen on; a free one near the recipe's when omitted.
        ssl: Whether to obtain a certificate.
        include_www: Also answer on ``www.<domain>``, as a redirect.
        health_path: Path the first health gate probes, over the recipe's.
        health_expect: Statuses the first health gate accepts, over the recipe's.
        health_timeout: Seconds the first health gate waits.
        default_backups: Schedule the daily backups an installed application
            starts with: its databases dumped and test-restored, its data
            archived. They stay on this server until a destination is chosen.
        install_requirements: Install the language runtime and the database
            engines the recipe needs and the server lacks, instead of refusing.
            Packages installed as root: the server's permission and sudo mode.
    """

    domain: str
    variables: dict[str, str] = Field(default_factory=dict)
    port: int | None = None
    ssl: bool = True
    include_www: bool = False
    default_backups: bool = True
    install_requirements: bool = False
    health_path: str | None = None
    health_expect: str | None = None
    health_timeout: int | None = None


class RecipeUpdateRequest(BaseModel):
    """
    Request to update an installed recipe to the catalogue's version.

    Attributes:
        variables: Values for the variables the new version asks for.
        force: Deploy the catalogue's version even when it is the one installed.
    """

    variables: dict[str, str] = Field(default_factory=dict)
    force: bool = False


class RequirementOut(BaseModel):
    """
    One thing a recipe needs from the server.

    Attributes:
        kind: ``tool``, ``runtime`` or ``service``.
        name: What it is (``Docker with the Compose plugin``, ``node@22``,
            ``postgresql``).
        met: Whether the server has it now.
        detail: What was found, or what is missing.
        fix: The command that provides it, when there is one.
        installable: Whether the install can provide it itself when asked
            (``install_requirements``).
    """

    kind: str
    name: str
    met: bool
    detail: str
    fix: str | None = None
    installable: bool = False


class RequirementListOut(BaseModel):
    """
    What a recipe needs, and whether this server can run it.

    Attributes:
        recipe: The recipe.
        ready: Whether nothing is missing.
        items: Each requirement.
    """

    recipe: str
    ready: bool
    items: list[RequirementOut]


class PrivateRecipeRequest(BaseModel):
    """
    A recipe of the server's own, sent as text.

    Attributes:
        document: The recipe's YAML (``<name>.yaml``).
        assets: The files it ships beside the document: path under
            ``assets/<name>/`` to its text (the logo, a configuration file).
        replace: Overwrite a recipe of the server's own with the same name.
    """

    document: str = Field(max_length=MAX_RECIPE_BYTES)
    assets: dict[str, str] = Field(default_factory=dict)
    replace: bool = False


class PrivateRecipeResult(BaseModel):
    """
    The recipe a request checked or added.

    Attributes:
        recipe: The recipe in full, as the catalogue shows it.
        added: Whether it was added to the server's recipes (False for a check).
    """

    recipe: RecipeOut
    added: bool


def _summary(data: dict[str, Any]) -> dict[str, Any]:
    """
    Turn a recipe's description into what the API answers.

    The logo becomes the URL it is served from, and ``schema`` is renamed
    because pydantic reserves that word.

    Args:
        data: ``Recipe.summary()`` or ``Recipe.to_dict()``.

    Returns:
        The same, ready for the response models.
    """
    out = dict(data)
    out["logo"] = f"/api/recipes/{data['name']}/logo" if data.get("logo") else None
    out["schema_version"] = out.pop("schema", 2)
    return out


@router.get("", response_model=RecipeListOut)
def get_recipes(
    session: Annotated[dict, Depends(get_current_session)],
    q: Annotated[str | None, Query(max_length=100, description="Words to look for")] = None,
    category: Annotated[str | None, Query(description="One category of the closed list")] = None,
    tag: Annotated[str | None, Query(max_length=40, description="One tag, exactly")] = None,
    kind: Annotated[str | None, Query(description="native or compose")] = None,
    origin: Annotated[
        str | None, Query(description="shipped, catalog or operator: where the recipe comes from")
    ] = None,
) -> RecipeListOut:
    """
    List the recipes, the ones this release can deploy first, and search them.

    Every filter given must hold. ``q`` matches the name, title, slogan,
    description, category and tags, and each of its words must be found.

    Args:
        session: The authenticated session.
        q: Words to look for.
        category: One category of the closed list.
        tag: One tag.
        kind: ``native`` or ``compose``.
        origin: Only the recipes from this source.

    Returns:
        The matching recipes, with whether each is available and why not, and
        the files of the server's own recipes that could not be read.

    Raises:
        RecipeError: ``category``, ``kind`` or ``origin`` is not one of the
            closed lists (400).
    """
    if origin is not None and origin not in ORIGINS:
        raise RecipeError(
            f"{origin!r} is not a recipe origin", details=f"Choose one of: {', '.join(ORIGINS)}."
        )
    found = [
        recipe
        for recipe in search_recipes(query=q, category=category, tag=tag, kind=kind)
        if origin is None or recipe.origin == origin
    ]
    return RecipeListOut(
        items=[RecipeSummaryOut(**_summary(recipe.summary())) for recipe in found],
        total=len(found),
        problems=[
            RecipeProblemOut(
                name=problem.name,
                origin=problem.origin,
                location=problem.location,
                error=problem.error,
                details=problem.details,
            )
            for problem in catalogue_problems()
            if origin in (None, problem.origin)
        ],
    )


@router.get("/categories", response_model=CategoryListOut)
def get_recipe_categories(
    session: Annotated[dict, Depends(get_current_session)],
) -> CategoryListOut:
    """
    Count the recipes of each category.

    Args:
        session: The authenticated session.

    Returns:
        Every category that holds a recipe, with how many.
    """
    return CategoryListOut(items=[CategoryOut(**entry) for entry in categories()])


@router.get("/installed", response_model=InstalledRecipeListOut)
def get_installed_recipes(
    session: Annotated[dict, Depends(get_current_session)],
) -> InstalledRecipeListOut:
    """
    List the applications installed from a recipe, and whether each is behind.

    Args:
        session: The authenticated session.

    Returns:
        One entry per application, with the version it runs, the catalogue's
        and what changed in between.
    """
    # A team member is shown the applications of its own teams.
    statuses = scoped_store().filter_domains(installed_statuses(), lambda status: status.domain)
    return InstalledRecipeListOut(
        items=[InstalledRecipeOut(**status.to_dict()) for status in statuses]
    )


@router.get("/installed/{domain}", response_model=InstalledRecipeOut)
def get_installed_recipe(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> InstalledRecipeOut:
    """
    Say which recipe an application was installed from and whether it is behind.

    Args:
        domain: The application's domain.
        session: The authenticated session.

    Returns:
        Its version, the catalogue's and what changed in between.

    Raises:
        HTTPException: 404 when the application was not installed from a recipe.
    """
    installed = get_installed(strict_domain(domain))
    if installed is None:
        raise HTTPException(status_code=404, detail=f"{domain} was not installed from a recipe")
    return InstalledRecipeOut(**status_of(installed).to_dict())


@router.post("/installed/{domain}/update", response_model=JobAcceptedResponse, status_code=202)
def update_installed_recipe(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    body: RecipeUpdateRequest | None = None,
) -> JobAcceptedResponse:
    """
    Queue the update of an application to its recipe's current version.

    The job runs :func:`noust.recipes.updates.update_recipe`, which hands the
    new version to the one update (``noust update``): backup, build, health
    gate, and the previous version back when the gate fails.

    Args:
        domain: The application's domain.
        session: The authenticated session.
        body: Values for the variables the new version asks for, or force.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application was not installed from a
            recipe, 409 when it already runs the catalogue's version.
    """
    domain = strict_domain(domain)
    installed = get_installed(domain)
    if installed is None:
        raise HTTPException(status_code=404, detail=f"{domain} was not installed from a recipe")
    request = body or RecipeUpdateRequest()
    if not request.force and not status_of(installed).update_available:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "nothing_new",
                "detail": f"{domain} already runs the catalogue's version of {installed.recipe}",
                "hint": "Pass force to deploy it again.",
            },
        )
    job = get_job_manager().create_job(
        job_type=JobType.UPDATE,
        name=f"Update {domain} to its recipe's latest version",
        description=f"Updating {domain} ({installed.recipe})",
        func=update_recipe_job,
        kwargs={"domain": domain, "variables": request.variables, "force": request.force},
        metadata={"domain": domain, "recipe": installed.recipe},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Update of {domain} queued",
        job=job.to_dict(),
    )


@router.post("/private/check", response_model=PrivateRecipeResult)
def check_private_recipe(
    body: PrivateRecipeRequest, session: Annotated[dict, Depends(get_current_session)]
) -> PrivateRecipeResult:
    """
    Check a recipe against the catalogue's schema, adding nothing.

    Args:
        body: The recipe and its assets.
        session: The authenticated session.

    Returns:
        The recipe as the catalogue would show it.

    Raises:
        RecipeError: It is not valid, or its name belongs to the package (400);
            the message names the field.
    """
    with staged_document(body.document, body.assets) as folder:
        [bundle] = read_bundles(folder)
    return PrivateRecipeResult(recipe=RecipeOut(**_summary(bundle.recipe.to_dict())), added=False)


@router.post("/private", response_model=PrivateRecipeResult, status_code=201)
def add_private_recipe(
    body: PrivateRecipeRequest, session: Annotated[dict, Depends(require_elevated)]
) -> PrivateRecipeResult:
    """
    Add a recipe to the server's own recipes (sudo mode).

    It passes the schema first, and a name the package holds is refused. The
    recipe can pull any image the document names, so adding one is as weighty
    as deploying anything: it needs sudo mode and the server's permission.

    Args:
        body: The recipe and its assets.
        session: The authenticated session in sudo mode.

    Returns:
        The recipe as the catalogue now has it.

    Raises:
        RecipeError: It is not valid, its name is taken, or ``replace`` was
            not set for a recipe that exists (400).
    """
    with staged_document(body.document, body.assets) as folder:
        [recipe] = add_recipes(folder, replace=body.replace, actor=actor_label(session))
    return PrivateRecipeResult(recipe=RecipeOut(**_summary(recipe.to_dict())), added=True)


@router.delete("/private/{name}", status_code=204)
def remove_private_recipe(
    name: str, session: Annotated[dict, Depends(require_elevated)]
) -> Response:
    """
    Remove one of the server's own recipes (sudo mode).

    Applications installed from it keep running.

    Args:
        name: The recipe.
        session: The authenticated session in sudo mode.

    Returns:
        An empty 204.

    Raises:
        HTTPException: 404 when no recipe of the server's has that name.
        NoustError: The recipe is not the server's own (400).
    """
    try:
        remove_recipe(name)
    except RecipeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return Response(status_code=204)


@router.get("/{name}/requirements", response_model=RequirementListOut)
def get_recipe_requirements(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> RequirementListOut:
    """
    Say what a recipe needs from this server, and whether it has it.

    Looks at the tools its deployer runs, the language runtime it pins and the
    database engines it requires. Changes nothing.

    Args:
        name: The recipe.
        session: The authenticated session.

    Returns:
        Each requirement with whether it is met, and the command that provides
        what is not.

    Raises:
        HTTPException: 404 when no recipe has that name.
    """
    try:
        recipe = get_recipe(name)
    except RecipeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    found = check_requirements(recipe)
    return RequirementListOut(
        recipe=recipe.name,
        ready=all(item.met for item in found),
        items=[
            RequirementOut(
                kind=item.kind,
                name=item.name,
                met=item.met,
                detail=item.detail,
                fix=item.fix,
                installable=item.installable,
            )
            for item in found
        ],
    )


@router.get("/{name}", response_model=RecipeOut)
def get_recipe_detail(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> RecipeOut:
    """
    Describe one recipe.

    Args:
        name: The recipe.
        session: The authenticated session.

    Returns:
        The recipe in full.

    Raises:
        HTTPException: 404 when no recipe has that name.
    """
    try:
        recipe = get_recipe(name)
    except RecipeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return RecipeOut(**_summary(recipe.to_dict()))


@router.get(
    "/{name}/logo",
    response_class=Response,
    responses={
        200: {
            "description": "The recipe's logo, an SVG",
            "content": {"image/svg+xml": {"schema": {"type": "string"}}},
        }
    },
)
def get_recipe_logo(name: str, session: Annotated[dict, Depends(get_current_session)]) -> Response:
    """
    Serve a recipe's logo.

    The file is validated when the recipe loads (no script, no style, no link:
    :mod:`noust.recipes.logos`), so it needs nothing the console's CSP forbids,
    wherever it is shown, and it is sent ``nosniff``.

    Args:
        name: The recipe.
        session: The authenticated session.

    Returns:
        The SVG.

    Raises:
        HTTPException: 404 when no recipe has that name, or it has no logo.
    """
    try:
        svg = logo_svg(name)
    except RecipeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return Response(content=svg, media_type="image/svg+xml", headers=_LOGO_HEADERS)


@router.post("/{name}/install", response_model=JobAcceptedResponse, status_code=202)
def install_recipe(
    name: str,
    body: RecipeInstallRequest,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> JobAcceptedResponse:
    """
    Queue the installation of a recipe on a domain.

    The same deployment ``POST /api/apps`` with ``recipe`` queues, with what a
    recipe needs said up front: a ``required`` variable nobody gave is a 400
    naming every missing one, before a job exists and before anything is
    provisioned.

    Args:
        name: The recipe.
        body: The domain, the variables and the deployment options.
        request: The incoming request, for the audit record of a refusal.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when no recipe has that name, 409 when the domain
            is already deployed.
        RecipeError: The recipe is not available, or a required variable has
            no value (400).
    """
    try:
        recipe = get_recipe(name)
    except RecipeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    require_available(recipe)
    require_inputs(recipe.title, recipe.variables, body.variables)
    if body.install_requirements:
        # A package installed as root is the server's, whoever's application it is for.
        require_instance_scope(Permission.SERVER_MANAGE)
        check_permission(session, Permission.SERVER_MANAGE)
        ensure_elevated(request, session)
    return create_app(
        CreateAppRequest(
            domain=body.domain,
            recipe=recipe.name,
            port=body.port,
            ssl=body.ssl,
            env_vars=body.variables,
            default_backups=body.default_backups,
            install_requirements=body.install_requirements,
            include_www=body.include_www,
            health_path=body.health_path,
            health_expect=body.health_expect,
            health_timeout=body.health_timeout,
        ),
        request,
        session,
    )
