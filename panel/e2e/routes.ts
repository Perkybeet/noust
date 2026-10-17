/**
 * Every route of the console, with the seeded machine's names filled in. Shared by the
 * screenshot pass and the layout-shift gate, so a page added to one is measured by the other.
 */

import type { Page } from "@playwright/test";

/** The newest deployment of an app, as the API lists it: seeded ids are not fixed. */
function newestDeployment(domain: string) {
  return async (page: Page): Promise<string> => {
    const response = await page.request.get(`/api/deployments?domain=${domain}&limit=1`);
    const newest = ((await response.json()) as { items: { id: number }[] }).items[0];
    if (newest === undefined) throw new Error(`the seed has no deployment of ${domain}`);
    return `/apps/${domain}/deployments/${String(newest.id)}`;
  };
}

export interface ConsoleRoute {
  name: string;
  path: string | ((page: Page) => Promise<string>);
}

export const ROUTES: readonly ConsoleRoute[] = [
  { name: "overview", path: "/" },
  { name: "apps", path: "/apps" },
  { name: "apps-new", path: "/apps/new" },
  // 3.4: the catalog of recipes, the recipes installed from it, and one recipe's own page.
  { name: "catalog", path: "/catalog" },
  { name: "catalog-installed", path: "/catalog/installed" },
  { name: "catalog-recipe", path: "/catalog/vaultwarden" },
  { name: "app-overview", path: "/apps/shop.example.net" },
  { name: "app-deployments", path: "/apps/shop.example.net/deployments" },
  { name: "app-deployment", path: newestDeployment("tienda.example.org") },
  { name: "app-deployment-failed", path: newestDeployment("clientes.example.com") },
  { name: "app-logs", path: "/apps/shop.example.net/logs" },
  { name: "app-metrics", path: "/apps/tienda.example.org/metrics" },
  { name: "app-metrics-7d", path: "/apps/tienda.example.org/metrics?range=7d" },
  { name: "app-environment", path: "/apps/blog.example.org/environment" },
  { name: "app-domains", path: "/apps/shop.example.net/domains" },
  { name: "app-diagnose", path: "/apps/clientes.example.com/diagnose" },
  // 3.4: a shell as the application's account, and a command run once.
  { name: "app-terminal", path: "/apps/tienda.example.org/terminal" },
  { name: "app-settings", path: "/apps/shop.example.net/settings" },
  // 3.1: each subsection of an app's settings is a URL of its own.
  { name: "app-settings-source", path: "/apps/taller.example.com/settings/source" },
  { name: "app-settings-deploys", path: "/apps/tienda.example.org/settings/deploys" },
  { name: "app-settings-push", path: "/apps/tienda.example.org/settings/deploy-on-push" },
  { name: "app-settings-hooks", path: "/apps/tienda.example.org/settings/hooks" },
  { name: "app-settings-access", path: "/apps/tienda.example.org/settings/access" },
  // 3.4: the build commands of an application, the same page for every type that has a block.
  { name: "app-settings-build", path: "/apps/shop.example.net/settings/build" },
  { name: "app-settings-builds", path: "/apps/tienda.example.org/settings/builds" },
  { name: "app-settings-resources", path: "/apps/shop.example.net/settings/resources" },
  { name: "app-settings-disk", path: "/apps/shop.example.net/settings/disk" },
  // 3.4: what runs beside the web process, the sandbox of its unit and the files kept for it.
  { name: "app-settings-processes", path: "/apps/tienda.example.org/settings/processes" },
  { name: "app-settings-security", path: "/apps/tienda.example.org/settings/security" },
  { name: "app-settings-mounts", path: "/apps/shop.example.net/settings/mounts" },
  { name: "app-files", path: "/apps/tienda.example.org/files" },
  { name: "app-settings-delete", path: "/apps/shop.example.net/settings/delete" },
  // 2.2: an application in blue/green mode, and one with pull request previews.
  { name: "app-settings-zero-downtime", path: "/apps/pagos.example.org/settings/deploys" },
  { name: "app-settings-previews", path: "/apps/portal.example.org/settings/previews" },
  // 3.4: teams, projects and environments.
  { name: "projects", path: "/projects" },
  { name: "project", path: "/projects/default/default" },
  { name: "teams", path: "/teams" },
  { name: "team", path: "/teams/default" },
  { name: "team-members", path: "/teams/default/members" },
  { name: "team-settings", path: "/teams/default/settings" },
  // 3.4: shared variables by scope, and the secret managers they can refer to.
  { name: "team-variables", path: "/teams/default/variables" },
  { name: "settings-variables", path: "/settings/variables" },
  { name: "settings-secret-managers", path: "/settings/secret-managers" },
  { name: "databases", path: "/databases" },
  { name: "databases-engines", path: "/databases/engines" },
  { name: "database-engine-settings", path: "/databases/engines/postgresql/settings" },
  { name: "database", path: "/databases/postgresql/example_production" },
  { name: "database-data", path: "/databases/postgresql/example_production/data?schema=public&table=orders" },
  { name: "database-query", path: "/databases/postgresql/example_production/query" },
  { name: "database-backups", path: "/databases/postgresql/example_production/backups" },
  { name: "database-users", path: "/databases/postgresql/example_production/users" },
  { name: "database-connect", path: "/databases/postgresql/example_production/connect" },
  { name: "database-metrics", path: "/databases/postgresql/example_production/metrics" },
  { name: "app-database", path: "/apps/example.com/database" },
  { name: "services", path: "/server/services" },
  { name: "service", path: "/server/services/wasm-shop-example-net" },
  { name: "service-logs", path: "/server/services/wasm-shop-example-net/logs" },
  { name: "service-unit", path: "/server/services/wasm-shop-example-net/unit" },
  { name: "cron", path: "/cron" },
  { name: "domains", path: "/domains" },
  { name: "domains-sites", path: "/domains/sites" },
  { name: "domains-site", path: "/domains/sites/example.net" },
  { name: "domains-dns", path: "/domains/dns" },
  { name: "domains-tunnels", path: "/domains/tunnels" },
  { name: "domains-ports", path: "/domains/ports" },
  { name: "backups", path: "/backups" },
  { name: "backups-schedules", path: "/backups/schedules" },
  { name: "backups-volumes", path: "/backups/volumes" },
  { name: "backups-noust", path: "/backups/noust" },
  { name: "backups-destinations", path: "/backups/destinations" },
  { name: "activity", path: "/activity" },
  { name: "server", path: "/server" },
  { name: "server-updates", path: "/server/updates" },
  { name: "server-security", path: "/server/security" },
  { name: "server-security-ssh", path: "/server/security?view=ssh" },
  { name: "server-security-firewall", path: "/server/security?view=firewall" },
  { name: "server-security-bans", path: "/server/security?view=bans" },
  { name: "server-storage", path: "/server/storage" },
  { name: "server-logs", path: "/server/logs" },
  { name: "server-system", path: "/server/system" },
  { name: "server-terminal", path: "/server/terminal" },
  { name: "settings", path: "/settings" },
  { name: "settings-security", path: "/settings/security" },
  { name: "settings-notifications", path: "/settings/notifications" },
  { name: "settings-alerts", path: "/settings/alerts" },
  { name: "settings-tokens", path: "/settings/tokens" },
  { name: "settings-integrations", path: "/settings/integrations" },
  { name: "settings-console", path: "/settings/console" },
  // 3.4: where the logs of applications are shipped.
  { name: "settings-log-shipping", path: "/settings/log-shipping" },
  // 3.4: roles where they are needed, single sign-on, the MCP endpoint, a manifest and the API.
  { name: "settings-access", path: "/settings/access" },
  { name: "settings-sso", path: "/settings/sso" },
  { name: "settings-mcp", path: "/settings/mcp" },
  { name: "settings-manifest", path: "/settings/manifest" },
  { name: "settings-api", path: "/settings/api" },
  // The fleet, on a plain server with none added yet: fleet.spec.ts covers it with a node.
  { name: "fleet", path: "/fleet" },
  // 3.4: the applications that run on more than one server (none recorded on a plain server).
  { name: "fleet-placements", path: "/fleet/placements" },
  { name: "settings-servers", path: "/settings/servers" },
  // 3.4: servers made at a cloud provider, and the boot scripts they start with.
  { name: "settings-cloud", path: "/settings/cloud" },
  // Where GitHub sends the browser back. The states that change nothing: an installation an
  // organization owner has yet to approve, and an address that carries nothing to finish.
  // Creating and installing ask "Confirm it's you" first (integrations.spec.ts covers them).
  { name: "github-callback-requested", path: "/integrations/github/callback?setup_action=request" },
  { name: "github-callback-invalid", path: "/integrations/github/callback" },
  { name: "settings-about", path: "/settings/about" },
];

/** Resolves a route's path, asking the API when the route names seeded data by id. */
export async function routePath(page: Page, route: ConsoleRoute): Promise<string> {
  return typeof route.path === "string" ? route.path : route.path(page);
}
