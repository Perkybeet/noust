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
  { name: "app-settings", path: "/apps/shop.example.net/settings" },
  // 3.1: each subsection of an app's settings is a URL of its own.
  { name: "app-settings-deploys", path: "/apps/tienda.example.org/settings/deploys" },
  { name: "app-settings-push", path: "/apps/tienda.example.org/settings/deploy-on-push" },
  { name: "app-settings-hooks", path: "/apps/tienda.example.org/settings/hooks" },
  { name: "app-settings-builds", path: "/apps/tienda.example.org/settings/builds" },
  { name: "app-settings-resources", path: "/apps/shop.example.net/settings/resources" },
  { name: "app-settings-delete", path: "/apps/shop.example.net/settings/delete" },
  // 2.2: an application in blue/green mode, and one with pull request previews.
  { name: "app-settings-zero-downtime", path: "/apps/pagos.example.org/settings/deploys" },
  { name: "app-settings-previews", path: "/apps/portal.example.org/settings/previews" },
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
  { name: "backups", path: "/backups" },
  { name: "backups-schedules", path: "/backups/schedules" },
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
  { name: "settings", path: "/settings" },
  { name: "settings-security", path: "/settings/security" },
  { name: "settings-notifications", path: "/settings/notifications" },
  { name: "settings-tokens", path: "/settings/tokens" },
  { name: "settings-integrations", path: "/settings/integrations" },
  // The fleet, on a plain server with none added yet: fleet.spec.ts covers it with a node.
  { name: "fleet", path: "/fleet" },
  { name: "settings-servers", path: "/settings/servers" },
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
