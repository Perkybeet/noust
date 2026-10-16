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

from fastapi import APIRouter, Depends

from noust.core.exceptions import FleetUnavailableError
from noust.web.api.access import router as access_router
from noust.web.api.access import team_router as access_team_router
from noust.web.api.alerts import router as alerts_router
from noust.web.api.app_adopt import router as app_adopt_router
from noust.web.api.app_apply import router as app_apply_router
from noust.web.api.app_build import router as app_build_router
from noust.web.api.app_compose import router as app_compose_router
from noust.web.api.app_container import router as app_container_router
from noust.web.api.app_deploy import batch_router as deploy_batch_router
from noust.web.api.app_deploy import router as app_deploy_router
from noust.web.api.app_export import router as app_export_router
from noust.web.api.app_hooks import router as app_hooks_router
from noust.web.api.app_processes import router as app_processes_router
from noust.web.api.app_promote import router as app_promote_router
from noust.web.api.app_runtime import router as app_runtime_router
from noust.web.api.app_source import router as app_source_router
from noust.web.api.app_stack import router as app_stack_router
from noust.web.api.app_tags import app_router as app_tags_app_router
from noust.web.api.app_tags import router as app_tags_router
from noust.web.api.approvals import require_approval
from noust.web.api.approvals import router as approvals_router
from noust.web.api.apps import router as apps_router
from noust.web.api.audit import router as audit_router
from noust.web.api.auth import router as auth_router
from noust.web.api.backup_destinations import router as backup_destinations_router
from noust.web.api.backup_schedules import router as backup_schedules_router
from noust.web.api.backups import router as backups_router
from noust.web.api.central import router as central_router
from noust.web.api.cert_settings import apps_router as app_certificate_router
from noust.web.api.cert_settings import router as cert_settings_router
from noust.web.api.certs import router as certs_router
from noust.web.api.cloud import router as cloud_router
from noust.web.api.compose import router as compose_router
from noust.web.api.config import router as config_router
from noust.web.api.console_exposure import router as console_exposure_router
from noust.web.api.cron import router as cron_router
from noust.web.api.databases import apps_router as databases_apps_router
from noust.web.api.databases import router as databases_router
from noust.web.api.deploy_keys import keys_router as deploy_keys_router
from noust.web.api.deploy_keys import router as app_deploy_key_router
from noust.web.api.deployments import app_router as deployment_actions_router
from noust.web.api.deployments import router as deployments_router
from noust.web.api.deps import NoustErrorRoute, install_error_handlers
from noust.web.api.diagnose import router as diagnose_router
from noust.web.api.dns import apps_router as app_dns_router
from noust.web.api.dns import router as dns_providers_router
from noust.web.api.domains import dns_router
from noust.web.api.domains import router as domains_router
from noust.web.api.ens import router as ens_router
from noust.web.api.fleet import router as fleet_router
from noust.web.api.fleet_placements import router as fleet_placements_router
from noust.web.api.fleet_self import router as fleet_self_router
from noust.web.api.git_credentials import router as git_credentials_router
from noust.web.api.gitlab import router as gitlab_router
from noust.web.api.identity import router as identity_router
from noust.web.api.import_remote import router as import_remote_router
from noust.web.api.integrations import router as integrations_router
from noust.web.api.jobs import router as jobs_router
from noust.web.api.log_drains import router as log_drains_router
from noust.web.api.manifest import router as manifest_router
from noust.web.api.mcp import router as mcp_router
from noust.web.api.metrics import app_router as app_metrics_router
from noust.web.api.metrics import router as metrics_router
from noust.web.api.monitor import router as monitor_router
from noust.web.api.node_enroll import router as node_enroll_router
from noust.web.api.node_link import router as node_link_router
from noust.web.api.nodes import router as nodes_router
from noust.web.api.notifications import router as notifications_router
from noust.web.api.oidc import router as oidc_router
from noust.web.api.openapi import router as openapi_router
from noust.web.api.overview import router as overview_router
from noust.web.api.passkeys import router as passkeys_router
from noust.web.api.previews import router as previews_router
from noust.web.api.recipes import router as recipes_router
from noust.web.api.registries import apps_router as app_registry_logins_router
from noust.web.api.registries import router as registries_router
from noust.web.api.restore_drills import router as restore_drills_router
from noust.web.api.sandbox import router as sandbox_router
from noust.web.api.scope import enforce_scope
from noust.web.api.secret_providers import router as secret_providers_router
from noust.web.api.self_backup import router as self_backup_router
from noust.web.api.server import router as server_router
from noust.web.api.server_migration import router as server_migration_router
from noust.web.api.services import router as services_router
from noust.web.api.site_logs import router as site_logs_router
from noust.web.api.site_policy import ranges_router as policy_ranges_router
from noust.web.api.site_policy import router as site_policy_router
from noust.web.api.sites import router as sites_router
from noust.web.api.streams import router as streams_router
from noust.web.api.system import router as system_router
from noust.web.api.teams import flat_router as teams_flat_router
from noust.web.api.teams import move_router as teams_move_router
from noust.web.api.teams import router as teams_router
from noust.web.api.terminal import apps_router as terminal_apps_router
from noust.web.api.terminal import router as terminal_router
from noust.web.api.terminal import server_router as terminal_server_router
from noust.web.api.timeline import router as timeline_router
from noust.web.api.tunnels import router as tunnels_router
from noust.web.api.variables import router as variables_router
from noust.web.api.variables import server_router as server_variables_router
from noust.web.api.volume_backups import router as volume_backups_router
from noust.web.api.workspaces import router as workspaces_router
from noust.web.api.zero_downtime import router as zero_downtime_router
from noust.web.auth import require_auth

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


# Every route below authenticates and is held to the permission its
# noust.web.permissions map declares, whether or not its handler asks for the
# payload: a route cannot forget either, and one no map names is refused.
# The teams' guard sits beside it for the same reason: a route is held to the
# teams of whoever calls it (noust.web.api.scope), and it comes before the
# four-eyes guard, which must never open an approval for a resource the caller
# may not know exists. The four-eyes guard too: a root-equivalent route cannot
# forget it (noust.web.api.approvals).
router = APIRouter(
    dependencies=[Depends(require_auth), Depends(enforce_scope), Depends(require_approval)]
)

router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
router.include_router(audit_router, prefix="/audit", tags=["Audit"])
router.include_router(ens_router, prefix="/ens", tags=["Compliance"])
# Before apps_router: POST /apps/import must not reach a route that reads
# "import" as a domain. It also owns GET "/{domain}/export", which apps.py
# does not define.
router.include_router(app_export_router, prefix="/apps", tags=["Applications"])
router.include_router(apps_router, prefix="/apps", tags=["Applications"])
router.include_router(services_router, prefix="/services", tags=["Services"])
# The machine itself: updates, power, disks, swap, clock, journal. /services stays as it is.
router.include_router(server_router, prefix="/server", tags=["Server"])
# Moving this server's applications, data and state to another server (3.4).
router.include_router(server_migration_router, prefix="/server/migration", tags=["Server"])
router.include_router(sites_router, prefix="/sites", tags=["Sites"])
# cert_settings.py owns "/acme" and "/uploaded": before the certificates router, or
# they would be read as a certificate named "acme".
router.include_router(cert_settings_router, prefix="/certs", tags=["Certificates"])
router.include_router(certs_router, prefix="/certs", tags=["Certificates"])
router.include_router(system_router, prefix="/system", tags=["System"])
router.include_router(monitor_router, prefix="/monitor", tags=["Monitor"])
router.include_router(metrics_router, prefix="/metrics", tags=["Metrics"])
# Rules on the recorded metrics (3.4), and the channels their messages leave by.
router.include_router(alerts_router, prefix="/alerts", tags=["Alerts"])
router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])
router.include_router(overview_router, prefix="/overview", tags=["Overview"])
router.include_router(timeline_router, prefix="/timeline", tags=["Timeline"])
# Same composition as diagnose: one path under "/{domain}/metrics" that apps.py
# does not define.
router.include_router(app_metrics_router, prefix="/apps", tags=["Metrics"])
# The jobs router carries its own "/jobs" prefix.
router.include_router(jobs_router, tags=["Jobs"])
router.include_router(config_router, prefix="/config", tags=["Configuration"])
# The console's own reach: published on a name of its own, over TLS (3.4).
router.include_router(console_exposure_router, prefix="/console", tags=["Console"])
router.include_router(backups_router, prefix="/backups", tags=["Backups"])
router.include_router(
    backup_schedules_router, prefix="/backup-schedules", tags=["Backup Schedules"]
)
router.include_router(volume_backups_router, prefix="/volume-backups", tags=["Volume Backups"])
router.include_router(self_backup_router, prefix="/self-backup", tags=["Self Backup"])
router.include_router(databases_router, prefix="/databases", tags=["Databases"])
router.include_router(databases_apps_router, prefix="/apps", tags=["Databases"])
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
# Networking (3.4). site_policy.py owns only paths under "/{domain}/policy"; its second
# router the server's list of Cloudflare ranges.
router.include_router(site_policy_router, prefix="/apps", tags=["Domains"])
router.include_router(policy_ranges_router, prefix="/policy", tags=["Domains"])
# DNS accounts and records; and, under an application, its certificate and its DNS.
router.include_router(dns_providers_router, prefix="/dns", tags=["DNS"])
# TCP and UDP ports published through nginx's stream module, and the firewall rules made for them.
router.include_router(streams_router, prefix="/streams", tags=["Streams"])
# Cloudflare Tunnels: an application's way in with no public address and no open port.
router.include_router(tunnels_router, prefix="/tunnels", tags=["Tunnels"])
router.include_router(app_dns_router, prefix="/apps", tags=["DNS"])
router.include_router(app_certificate_router, prefix="/apps", tags=["Certificates"])
router.include_router(
    backup_destinations_router, prefix="/backup-destinations", tags=["Backup Destinations"]
)
router.include_router(integrations_router, prefix="/integrations", tags=["Integrations"])
# gitlab.py owns "/integrations/gitlab/**".
router.include_router(gitlab_router, prefix="/integrations", tags=["Integrations"])
router.include_router(log_drains_router, prefix="/log-drains", tags=["Log drains"])
# Same composition as diagnose: each owns paths under "/{domain}/..." that
# apps.py does not define ("/zero-downtime", "/previews").
router.include_router(zero_downtime_router, prefix="/apps", tags=["Applications"])
# compose.py looks at a pasted Compose document before it is deployed.
router.include_router(compose_router, prefix="/compose", tags=["Compose"])
# restore_drills.py owns only paths under "/{domain}/restore-drills".
router.include_router(restore_drills_router, prefix="/apps", tags=["Backups"])
router.include_router(previews_router, prefix="/apps", tags=["Previews"])
# Same composition: sandbox.py owns only paths under "/{domain}/sandbox".
router.include_router(sandbox_router, prefix="/apps", tags=["Applications"])
# identity.py owns only paths under "/{domain}/identity".
router.include_router(identity_router, prefix="/apps", tags=["Applications"])
# app_runtime.py gathers the runtime area's paths under "/{domain}/...": the unit's
# sandbox ("/hardening").
router.include_router(app_runtime_router, prefix="/apps", tags=["Applications"])
# app_stack.py owns only "/{domain}/backup-before-update" and "/{domain}/headless".
router.include_router(app_stack_router, prefix="/apps", tags=["Applications"])
# site_logs.py owns only "/{domain}/site-logs".
router.include_router(site_logs_router, prefix="/apps", tags=["Applications"])
# app_hooks.py owns only paths under "/{domain}/hooks".
router.include_router(app_hooks_router, prefix="/apps", tags=["Applications"])
# terminal.py owns "/{domain}/terminal" and "/{domain}/exec" under "/apps", the one
# "/terminal" under "/server", and everything under "/terminal".
router.include_router(terminal_apps_router, prefix="/apps", tags=["Terminal"])
router.include_router(terminal_server_router, prefix="/server", tags=["Terminal"])
router.include_router(terminal_router, prefix="/terminal", tags=["Terminal"])
# deploy_keys.py owns only paths under "/{domain}/deploy-key", and its second
# router the whole of "/deploy-keys".
router.include_router(app_deploy_key_router, prefix="/apps", tags=["Applications"])
router.include_router(deploy_keys_router, prefix="/deploy-keys", tags=["Deploy keys"])
# git_credentials.py owns only paths under "/{domain}/git-credential".
router.include_router(git_credentials_router, prefix="/apps", tags=["Applications"])
# app_container.py owns only "/{domain}/container".
router.include_router(app_container_router, prefix="/apps", tags=["Applications"])
# app_build.py owns only paths under "/{domain}/build".
router.include_router(app_build_router, prefix="/apps", tags=["Applications"])
# app_deploy.py owns only "/{domain}/deploy" under "/apps", and the one "/deploy".
router.include_router(app_deploy_router, prefix="/apps", tags=["Applications"])
router.include_router(deploy_batch_router, tags=["Applications"])
# registries.py owns the whole of "/registries", and "/{domain}/registry-logins" under "/apps".
router.include_router(registries_router, prefix="/registries", tags=["Registries"])
router.include_router(app_registry_logins_router, prefix="/apps", tags=["Applications"])
# import_remote.py owns the whole of "/import": reading another platform's server.
router.include_router(import_remote_router, prefix="/import", tags=["Applications"])
# workspaces.py owns the whole of "/workspaces".
router.include_router(workspaces_router, prefix="/workspaces", tags=["Applications"])
# app_apply.py owns only "/{domain}/declaration" and "/{domain}/apply".
router.include_router(app_apply_router, prefix="/apps", tags=["Applications"])
# app_compose.py owns only "/{domain}/compose".
router.include_router(app_compose_router, prefix="/apps", tags=["Applications"])
# app_processes.py owns only "/{domain}/processes".
router.include_router(app_processes_router, prefix="/apps", tags=["Applications"])
# app_source.py owns only "/{domain}/source-options".
router.include_router(app_source_router, prefix="/apps", tags=["Applications"])
# app_adopt.py owns only "/adopt", which no "/{domain}" route of apps.py takes.
router.include_router(app_adopt_router, prefix="/apps", tags=["Applications"])
router.include_router(recipes_router, prefix="/recipes", tags=["Recipes"])
# Teams, projects and environments. move_router owns only "/{domain}/move" under "/apps";
# flat_router owns "/projects" and "/environments".
router.include_router(teams_router, prefix="/teams", tags=["Teams"])
router.include_router(access_team_router, prefix="/teams", tags=["Access"])
router.include_router(access_router, prefix="/access", tags=["Access"])
router.include_router(mcp_router, prefix="/mcp", tags=["MCP"])
router.include_router(manifest_router, prefix="/manifest", tags=["Manifest"])
router.include_router(teams_flat_router, tags=["Teams"])
router.include_router(teams_move_router, prefix="/apps", tags=["Teams"])
# app_promote.py owns only "/{domain}/promotion" and "/{domain}/promote".
router.include_router(app_promote_router, prefix="/apps", tags=["Applications"])
# app_tags.py owns only "/{domain}/tags" under "/apps", and the whole of "/tags".
router.include_router(app_tags_app_router, prefix="/apps", tags=["Tags"])
router.include_router(app_tags_router, prefix="/tags", tags=["Tags"])
# Shared variables of a team, a project and an environment sit under their path in
# "/teams"; the server's own under "/variables". The secret managers they can name.
router.include_router(variables_router, prefix="/teams", tags=["Variables"])
router.include_router(server_variables_router, prefix="/variables", tags=["Variables"])
router.include_router(secret_providers_router, prefix="/secret-providers", tags=["Variables"])
# The fleet, on a central: the registry of nodes, and the proxy that makes
# every other route here reachable on a node as /nodes/{node}/api/...
router.include_router(central_router, prefix="/central", tags=["Central"])
router.include_router(fleet_self_router, prefix="/auth/fleet", tags=["Fleet"])
# Under /auth like the rest of one's own credentials, so no fleet token reaches it.
router.include_router(passkeys_router, prefix="/auth/passkeys", tags=["Passkeys"])
# Single sign-in through an identity provider (3.4): the same place, for the same reason.
router.include_router(oidc_router, prefix="/auth/oidc", tags=["Single sign-on"])
router.include_router(approvals_router, prefix="/approvals", tags=["Approvals"])
router.include_router(fleet_router, prefix="/fleet", tags=["Fleet"])
router.include_router(fleet_placements_router, prefix="/fleet/placements", tags=["Fleet"])
# Guided enrollment of a server: its host key, then the job that installs and joins it.
router.include_router(node_enroll_router, prefix="/fleet/enroll", tags=["Fleet"])
# Servers created at a cloud provider: accounts, the cloud-init library and the machines.
router.include_router(cloud_router, prefix="/fleet/cloud", tags=["Fleet"])
# Servers behind NAT: they dial in to this central, which invites them and accepts their code.
router.include_router(node_link_router, prefix="/fleet/link", tags=["Fleet"])
router.include_router(nodes_router, prefix="/nodes", tags=["Nodes"])
router.include_router(node_proxy_router(), prefix="/nodes", tags=["Nodes"])
# No prefix: the route is declared as "/openapi.json" and this router mounts
# directly under "/api", giving GET /api/openapi.json.
router.include_router(openapi_router, tags=["OpenAPI"])
