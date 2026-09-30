import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../app/locale";
import { renderConsole } from "../test/console";
import { databaseRoutes } from "../features/databases/testFixtures";
import { fakeBackend, json, signedInRoutes } from "../test/fakes";

/**
 * The route tree is the contract with the CLI's deep links (src/noust/cli/panel_links.py):
 * every path answers with its own page, headed by its title.
 */
const PAGES: [path: string, heading: string, content: string][] = [
  ["/", "Overview", "Needs attention"],
  ["/apps", "Applications", "Applications"],
  ["/apps/new", "New application", "Source"],
  ["/apps/shop.example.com", "shop.example.com", "How it runs"],
  ["/apps/shop.example.com/deployments", "shop.example.com", "History"],
  ["/apps/shop.example.com/deployments/42", "shop.example.com", "No deployment 42"],
  ["/apps/shop.example.com/logs", "shop.example.com", "Logs of shop.example.com"],
  ["/apps/shop.example.com/metrics", "shop.example.com", "CPU and memory"],
  ["/apps/shop.example.com/environment", "shop.example.com", "Environment variables of shop.example.com"],
  ["/apps/shop.example.com/domains", "shop.example.com", "Domains"],
  ["/apps/shop.example.com/diagnose", "shop.example.com", "Diagnosis of shop.example.com"],
  ["/apps/shop.example.com/settings", "shop.example.com", "General"],
  ["/apps/shop.example.com/settings/deploys", "shop.example.com", "Deploys"],
  ["/apps/shop.example.com/settings/deploy-on-push", "shop.example.com", "Deploy on push"],
  ["/apps/shop.example.com/settings/builds", "shop.example.com", "Builds"],
  ["/apps/shop.example.com/settings/resources", "shop.example.com", "Resources"],
  ["/apps/shop.example.com/settings/previews", "shop.example.com", "Pull request previews"],
  ["/apps/shop.example.com/settings/export", "shop.example.com", "Export"],
  ["/apps/shop.example.com/settings/delete", "shop.example.com", "Delete"],
  ["/databases", "Databases", "Databases"],
  ["/databases/postgresql/example_production", "example_production", "Used by"],
  ["/backups", "Backups", "Applications and their backups"],
  ["/domains", "Domains and certificates", "Certificates"],
  ["/services", "Server", "Services"],
  ["/services/wasm-shop", "wasm-shop", "How it runs"],
  ["/cron", "Cron", "Cron jobs"],
  ["/activity", "Activity", "Activity"],
  ["/server", "Server", "Needs attention"],
  ["/server/services", "Server", "Services"],
  ["/server/services/wasm-shop", "wasm-shop", "How it runs"],
  ["/settings", "Settings", "This server"],
  ["/settings/security", "Settings", "Two-factor authentication"],
  ["/settings/notifications", "Settings", "Channels"],
  ["/settings/tokens", "Settings", "Issued tokens"],
  ["/settings/accounts", "Settings", "Accounts"],
  ["/settings/approvals", "Settings", "Requests"],
  ["/settings/audit", "Settings", "Events"],
  ["/settings/compliance", "Settings", "ENS category MEDIUM"],
  ["/settings/about", "Settings", "Version"],
  ["/settings/integrations", "Settings", "GitHub"],
  ["/settings/servers", "Settings", "Servers"],
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

  it.each(PAGES)("%s is %s", async (path, heading, content) => {
    // The databases area answers from its own fixtures (features/databases/testFixtures).
    const backend = fakeBackend(databaseRoutes());
    backend.on("GET /api/integrations/github", () => json(200, NO_GITHUB_APP));
    backend.on("GET /api/nodes", () => json(200, { items: [] }));
    // An app's tab is shown once the app is read, by when a 404 of its own data has arrived
    // too: the environment answers, and the metrics stay on their loading frame.
    backend.on("GET /api/apps/shop.example.com/env", () =>
      json(200, { domain: "shop.example.com", variables: { NODE_ENV: "production" }, unmasked: false, secrets: { NODE_ENV: { secret: false, reason: "plain", marked: false } } }),
    );
    backend.on("GET /api/apps/shop.example.com/metrics", () => new Promise<Response>(() => undefined));
    renderConsole(path);
    expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
    // A section heading, or for a page that is one table, the table's region.
    await waitFor(() => {
      expect(
        screen.queryByRole("heading", { level: 2, name: content }) ?? screen.queryByRole("region", { name: content }),
      ).toBeInTheDocument();
    });
  });

  it("/integrations/github/callback is where GitHub sends the browser back", async () => {
    // The App's redirect and setup URL (manifest.CALLBACK_PATH): opened without what GitHub
    // adds to it, it says there is nothing to finish rather than showing a blank page.
    fakeBackend(signedInRoutes());
    renderConsole("/integrations/github/callback");
    expect(await screen.findByRole("heading", { level: 1, name: "Connecting GitHub" })).toBeInTheDocument();
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
