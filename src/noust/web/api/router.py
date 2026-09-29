"""
The API router, assembled from one module per resource.

Every sub-router is built with
:class:`~noust.web.api.deps.NoustErrorRoute`, so a manager error becomes an HTTP
response with a status that matches what went wrong, instead of the ``500`` a
per-handler ``except Exception`` used to produce.

:func:`~noust.web.api.deps.install_error_handlers` is re-exported here so the
application can register the same translation for anything raised outside a
route, such as in a dependency.
"""

from fastapi import APIRouter

from noust.core.exceptions import FleetUnavailableError
from noust.web.api.app_export import router as app_export_router
from noust.web.api.apps import router as apps_router
from noust.web.api.audit import router as audit_router
from noust.web.api.auth import router as auth_router
from noust.web.api.backup_destinations import router as backup_destinations_router
from noust.web.api.backup_schedules import router as backup_schedules_router
from noust.web.api.backups import router as backups_router
from noust.web.api.central import router as central_router
from noust.web.api.certs import router as certs_router
from noust.web.api.config import router as config_router
from noust.web.api.cron import router as cron_router
from noust.web.api.databases import router as databases_router
from noust.web.api.deployments import app_router as deployment_actions_router
from noust.web.api.deployments import router as deployments_router
from noust.web.api.deps import NoustErrorRoute, install_error_handlers
from noust.web.api.diagnose import router as diagnose_router
from noust.web.api.domains import dns_router
from noust.web.api.domains import router as domains_router
from noust.web.api.integrations import router as integrations_router
from noust.web.api.jobs import router as jobs_router
from noust.web.api.metrics import router as metrics_router
from noust.web.api.monitor import router as monitor_router
from noust.web.api.nodes import router as nodes_router
from noust.web.api.openapi import router as openapi_router
from noust.web.api.previews import router as previews_router
from noust.web.api.recipes import router as recipes_router
from noust.web.api.services import router as services_router
from noust.web.api.sites import router as sites_router
from noust.web.api.system import router as system_router
from noust.web.api.zero_downtime import router as zero_downtime_router

__all__ = ["install_error_handlers", "router"]


def node_proxy_router() -> APIRouter:
    """
    The proxy to every node, or a stand-in that says why there is none.

    The proxy needs httpx, which only a central uses: a server without it
    still serves its own console, and a call to a node answers 503 with the
    package to install instead of the whole API failing to import.

    Returns:
        The router to mount under ``/nodes``.
    """
    try:
        from noust.web.api.node_proxy import router as proxy
    except FleetUnavailableError as exc:
        missing = exc
        stand_in = APIRouter(route_class=NoustErrorRoute)

        @stand_in.api_route(
            "/{node}/api/{path:path}",
            methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
            include_in_schema=False,
        )
        async def fleet_unavailable(node: str, path: str) -> None:
            raise missing

        return stand_in
    return proxy


router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
router.include_router(audit_router, prefix="/audit", tags=["Audit"])
# Before apps_router: POST /apps/import must not reach a route that reads
# "import" as a domain. It also owns GET "/{domain}/export", which apps.py
# does not define.
router.include_router(app_export_router, prefix="/apps", tags=["Applications"])
router.include_router(apps_router, prefix="/apps", tags=["Applications"])
router.include_router(services_router, prefix="/services", tags=["Services"])
router.include_router(sites_router, prefix="/sites", tags=["Sites"])
router.include_router(certs_router, prefix="/certs", tags=["Certificates"])
router.include_router(system_router, prefix="/system", tags=["System"])
router.include_router(monitor_router, prefix="/monitor", tags=["Monitor"])
router.include_router(metrics_router, prefix="/metrics", tags=["Metrics"])
# The jobs router carries its own "/jobs" prefix.
router.include_router(jobs_router, tags=["Jobs"])
router.include_router(config_router, prefix="/config", tags=["Configuration"])
router.include_router(backups_router, prefix="/backups", tags=["Backups"])
router.include_router(
    backup_schedules_router, prefix="/backup-schedules", tags=["Backup Schedules"]
)
router.include_router(databases_router, prefix="/databases", tags=["Databases"])
router.include_router(cron_router, prefix="/cron", tags=["Cron Jobs"])
router.include_router(deployments_router, prefix="/deployments", tags=["Deployments"])
# Mounted at the same "/apps" prefix as apps_router: diagnose.py owns exactly
# one path, "/{domain}/diagnose", that apps.py does not define, so the two
# routers compose without colliding. Kept separate because apps.py is owned
# by another agent while this task was in flight.
router.include_router(diagnose_router, prefix="/apps", tags=["Applications"])
# Same composition: deployments.py's actions on one deployment are under
# "/{domain}/deployments/{id}/", which apps.py does not define.
router.include_router(deployment_actions_router, prefix="/apps", tags=["Deployments"])
# Same composition: domains.py owns only paths under "/{domain}/domains".
router.include_router(domains_router, prefix="/apps", tags=["Domains"])
# domains.py's second router: a DNS check with no application yet, for the
# new-app wizard. GET /api/domains/dns.
router.include_router(dns_router, prefix="/domains", tags=["Domains"])
router.include_router(
    backup_destinations_router, prefix="/backup-destinations", tags=["Backup Destinations"]
)
router.include_router(integrations_router, prefix="/integrations", tags=["Integrations"])
# Same composition as diagnose: each owns paths under "/{domain}/..." that
# apps.py does not define ("/zero-downtime", "/previews").
router.include_router(zero_downtime_router, prefix="/apps", tags=["Applications"])
router.include_router(previews_router, prefix="/apps", tags=["Previews"])
router.include_router(recipes_router, prefix="/recipes", tags=["Recipes"])
# The fleet, on a central: the registry of nodes, and the proxy that makes
# every other route here reachable on a node as /nodes/{node}/api/...
router.include_router(central_router, prefix="/central", tags=["Central"])
router.include_router(nodes_router, prefix="/nodes", tags=["Nodes"])
router.include_router(node_proxy_router(), prefix="/nodes", tags=["Nodes"])
# No prefix: the route is declared as "/openapi.json" and this router mounts
# directly under "/api", giving GET /api/openapi.json.
router.include_router(openapi_router, tags=["OpenAPI"])
