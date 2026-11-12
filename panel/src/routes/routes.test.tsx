import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../app/locale";
import { drawnKeys, goCold } from "../test/coldCatalog";
import { COMPLIANCE } from "../test/compliance";
import { renderConsole } from "../test/console";
import { databaseRoutes } from "../features/databases/testFixtures";
import { fakeBackend, json, signedInRoutes } from "../test/fakes";

/**
 * The route tree is the contract with the CLI's deep links (src/noust/cli/panel_links.py):
 * every path answers with its own page, headed by its title.
 */
const PAGES: [path: string, heading: string, content: string][] = [
  ["/", "This server", "Needs attention"],
  ["/apps", "Applications", "Applications"],
  ["/apps/new", "New application", "Source"],
  ["/apps/shop.example.com", "shop.example.com", "Health"],
  ["/apps/shop.example.com/deployments", "shop.example.com", "No deploys recorded yet"],
  ["/apps/shop.example.com/deployments/42", "shop.example.com", "No deployment 42"],
  ["/apps/shop.example.com/logs", "shop.example.com", "Logs of shop.example.com"],
  ["/apps/shop.example.com/metrics", "shop.example.com", "CPU and memory"],
  ["/apps/shop.example.com/environment", "shop.example.com", "Environment variables of shop.example.com"],
  ["/apps/shop.example.com/domains", "shop.example.com", "Domains"],
  ["/apps/shop.example.com/diagnose", "shop.example.com", "Diagnosis of shop.example.com"],
  ["/apps/shop.example.com/code", "shop.example.com", "Build"],
  ["/apps/shop.example.com/triggers", "shop.example.com", "Releases and rollback"],
  ["/apps/shop.example.com/resources", "shop.example.com", "How it runs"],
  ["/apps/shop.example.com/access", "shop.example.com", "Access"],
  ["/apps/shop.example.com/export", "shop.example.com", "Export"],
  ["/apps/shop.example.com/servers", "shop.example.com", "On several servers"],
  ["/apps/shop.example.com/delete", "shop.example.com", "What is deleted"],
  ["/databases", "Databases", "Databases"],
  ["/databases/postgresql/example_production", "example_production", "Engine"],
  ["/backups", "Backups", "Applications and their backups"],
  ["/domains", "Domains", "A name is what your applications and sites answer on. Add one and Noust checks where it points and gets its HTTPS certificate."],
  ["/domains/certificates", "Certificates", "Certificates"],
  ["/services", "Services", "Services"],
  ["/services/shop-web", "shop-web", "How it runs"],
  ["/cron", "Scheduled tasks", "Scheduled tasks"],
  ["/activity", "Activity", "Activity"],
  ["/server", "web-01", "What is not well"],
  ["/server/services", "Services", "Services"],
  ["/server/services/shop-web", "shop-web", "How it runs"],
  ["/settings", "Settings", "People and access"],
  ["/settings/general", "General", "Policy"],
  ["/account", "Account", "API tokens"],
  ["/account/security", "Account", "Two-factor authentication"],
  ["/settings/notifications", "Notification channels", "Channels"],
  ["/settings/access-security", "Access security", "At the door"],
  ["/settings/variables", "Variables", "Server variables"],
  ["/settings/secret-managers", "Secret managers", "Secret managers"],
  ["/settings/tokens", "API tokens", "Issued tokens"],
  ["/settings/accounts", "Accounts", "Accounts"],
  ["/settings/approvals", "Approvals", "Requests"],
  ["/settings/audit", "Audit log", "Events"],
  ["/settings/compliance", "Compliance", "No framework is on"],
  ["/settings/about", "About Noust", "Version"],
  ["/settings/integrations", "Integrations", "GitHub"],
  ["/settings/registries", "Image registries", "Accounts"],
  ["/settings/cloud", "Cloud providers", "Provider accounts"],
  ["/fleet", "Fleet", "No servers in this fleet yet"],
];

/** A server with no GitHub App yet: Settings > Integrations and the wizard read it. */
const NO_GITHUB_APP = { configured: false, installations: [], hooks_url: null, hooks_active: false };

describe("the route tree", () => {
  // A desktop's width: a list page's table is a table (a region), not a phone's card rows.
  beforeEach(() => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }));
  });

  // Each page opens as it does in a browser that has just arrived: the chrome of English and nothing
  // else, so the namespaces it draws are the ones the router loaded for it (i18n/routeNamespaces.ts).
  // A text a page draws that its route did not declare is a miss here, with the namespace named.
  let restoreCatalog: (() => Promise<void>) | undefined;
  afterEach(async () => {
    await restoreCatalog?.();
    restoreCatalog = undefined;
  });

  it.each(PAGES)("%s is %s", async (path, heading, content) => {
    const cold = goCold();
    restoreCatalog = cold.restore;
    // The databases area answers from its own fixtures (features/databases/testFixtures).
    const backend = fakeBackend(databaseRoutes());
    backend.on("GET /api/integrations/github", () => json(200, NO_GITHUB_APP));
    backend.on("GET /api/nodes", () => json(200, { items: [] }));
    // Compliance and Access security read the frameworks and their controls: none is on.
    backend.on("GET /api/compliance", () => json(200, COMPLIANCE));
    // The Domains list is made of the sites and the certificates: no site, no domain.
    backend.on("GET /api/sites", () => json(200, { sites: [], total: 0, webserver: "nginx" }));
    // An app's tab is shown once the app is read, by when a 404 of its own data has arrived
    // too: the environment answers, and the metrics stay on their loading frame.
    backend.on("GET /api/apps/shop.example.com/env", () =>
      json(200, { domain: "shop.example.com", variables: { NODE_ENV: "production" }, unmasked: false, secrets: { NODE_ENV: { secret: false, reason: "plain", marked: false } } }),
    );
    backend.on("GET /api/apps/shop.example.com/metrics", () => new Promise<Response>(() => undefined));
    // A history with nothing in it says what it is for; its heading is the page's second level.
    backend.on("GET /api/deployments", () => json(200, { items: [], total: 0, next_before_id: null }));
    // The page of deleting an application says what deleting it would touch, which the server answers.
    backend.on("GET /api/apps/shop.example.com/deletion", () => json(200, { domain: "shop.example.com", path: "/var/www/apps/shop-example-com", keeps_directory: false, has_volumes: false, databases: [] }));
    renderConsole(path);
    expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
    // A section heading, or for a page that is one table, the table's region, or for a page that has nothing yet, the
    // sentence of its first use (which has no heading of its own).
    await waitFor(() => {
      expect(
        screen.queryByRole("heading", { level: 2, name: content }) ?? screen.queryByRole("region", { name: content }) ?? screen.queryByText(content),
      ).toBeInTheDocument();
    });
    expect(cold.misses).toEqual([]);
    expect(drawnKeys()).toEqual([]);
  });

  it("/integrations/github/callback is where GitHub sends the browser back", async () => {
    // The App's redirect and setup URL (manifest.CALLBACK_PATH): opened without what GitHub
    // adds to it, it says there is nothing to finish rather than showing a blank page.
    fakeBackend(signedInRoutes());
    renderConsole("/integrations/github/callback");
    expect(await screen.findByRole("heading", { level: 1, name: "Connecting GitHub" })).toBeInTheDocument();
    expect(await screen.findByText("Nothing to finish here")).toBeInTheDocument();
  });

  it("/integrations/gitlab/callback is where GitLab sends the browser back", async () => {
    // The OAuth application's redirect URI (CALLBACK_PATH): opened without what GitLab adds to it,
    // it says there is nothing to finish rather than showing a blank page.
    fakeBackend(signedInRoutes());
    renderConsole("/integrations/gitlab/callback");
    expect(await screen.findByRole("heading", { level: 1, name: "GitLab" })).toBeInTheDocument();
    expect(await screen.findByText("Nothing to finish here")).toBeInTheDocument();
  });

  it("names the page in the browser tab, most specific first", async () => {
    fakeBackend(signedInRoutes());
    renderConsole("/apps/shop.example.com/logs");
    await screen.findByRole("region", { name: "Logs of shop.example.com" });
    expect(document.title).toBe("Logs - shop.example.com - web-01 - Noust");
  });

  it("answers an unknown address with a page, not a blank screen", async () => {
    fakeBackend(signedInRoutes());
    renderConsole("/no/such/page");
    expect(await screen.findByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
  });

  it("says so in Spanish once the language switches", async () => {
    fakeBackend(signedInRoutes());
    renderConsole("/no/such/page");
    await screen.findByRole("heading", { level: 1, name: "Page not found" });
    await act(async () => {
      await setLocale("es");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Página no encontrada" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "resumen" })).toBeInTheDocument();
  });
});
