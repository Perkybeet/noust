import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../test/console";
import { fakeBackend, json, signedInRoutes } from "../test/fakes";

/**
 * The route tree is the contract with the CLI's deep links (src/wasm/cli/panel_links.py):
 * every path answers with its own page, headed by its title.
 */
const PAGES: [path: string, heading: string, content: string][] = [
  ["/", "Overview", "Needs attention"],
  ["/apps", "Applications", "Applications"],
  ["/apps/new", "New application", "Source"],
  ["/apps/shop.example.com", "shop.example.com", "Runtime"],
  ["/apps/shop.example.com/deployments", "shop.example.com", "History"],
  ["/apps/shop.example.com/deployments/42", "shop.example.com", "No deployment 42"],
  ["/apps/shop.example.com/logs", "shop.example.com", "Journal of shop.example.com"],
  ["/apps/shop.example.com/metrics", "shop.example.com", "CPU and memory"],
  ["/apps/shop.example.com/environment", "shop.example.com", "Variables"],
  ["/apps/shop.example.com/domains", "shop.example.com", "Domains"],
  ["/apps/shop.example.com/diagnose", "shop.example.com", "Diagnosis of shop.example.com"],
  ["/apps/shop.example.com/settings", "shop.example.com", "Source and runtime"],
  ["/databases", "Databases", "Engines"],
  ["/databases/postgresql/shop", "shop", "SQL console"],
  ["/backups", "Backups", "Schedules"],
  ["/domains", "Domains and certificates", "Certificates"],
  ["/services", "Services", "Services"],
  ["/services/wasm-shop", "wasm-shop", "Overview"],
  ["/cron", "Cron", "Cron jobs"],
  ["/activity", "Activity", "Activity"],
  ["/server", "Server", "Health"],
  ["/settings", "Settings", "Applications directory"],
  ["/settings/security", "Settings", "Two-factor authentication"],
  ["/settings/notifications", "Settings", "Where alerts go"],
  ["/settings/tokens", "Settings", "Tokens for automation"],
  ["/settings/about", "Settings", "Version and updates"],
  ["/settings/integrations", "Settings", "GitHub"],
];

/** A server with no GitHub App yet: Settings > Integrations and the wizard read it. */
const NO_GITHUB_APP = { configured: false, installations: [], hooks_url: null, hooks_active: false };

describe("the route tree", () => {
  it.each(PAGES)("%s is %s", async (path, heading, content) => {
    fakeBackend(signedInRoutes()).on("GET /api/integrations/github", () => json(200, NO_GITHUB_APP));
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
    await screen.findByRole("region", { name: "Journal of shop.example.com" });
    expect(document.title).toBe("Logs - shop.example.com - web-01 - WASM");
  });

  it("answers an unknown address with a page, not a blank screen", async () => {
    fakeBackend(signedInRoutes());
    renderConsole("/no/such/page");
    expect(await screen.findByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
  });
});
