import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, onNode, signedInRoutes } from "../../test/fakes";
import { DB1, SERVERS_VIEW, WEB2, centralSession } from "../fleet/testFixtures";

const APP_TYPES = { types: [{ type: "nextjs", name: "Next.js", default_port: 3000 }] };

/** A central with web-2 (reachable, admin) and db-1 (not answering); both answer the wizard's reads. */
function backend(nodes: unknown[] = [WEB2, DB1]) {
  const wizardReads = {
    "GET /api/apps": () => json(200, { total: 0, apps: [] }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/recipes": () => json(200, { recipes: [] }),
  };
  return fakeBackend({
    ...signedInRoutes(centralSession()),
    ...wizardReads,
    ...onNode("web-2", { ...signedInRoutes(), ...wizardReads }),
    "GET /api/nodes": () => json(200, { items: nodes }),
    "GET /api/fleet/servers": () => json(200, SERVERS_VIEW),
  });
}

function step(): string | null {
  return document.querySelector('[data-slot="stepper"] [aria-current="step"]')?.textContent ?? null;
}

describe("the New application wizard on a fleet", () => {
  it("asks on which server first, with this one chosen and a server that cannot take it said so", { timeout: 30_000 }, async () => {
    backend();
    const { container, user } = renderConsole("/apps/new");
    expect(await screen.findByRole("heading", { level: 2, name: "Which server deploys it" })).toBeInTheDocument();
    expect(step()).toContain("Server");
    const group = screen.getByRole("radiogroup", { name: "Server" });
    expect(within(group).getByRole("radio", { name: /web-01/ })).toBeChecked();
    const db1 = within(group).getByRole("radio", { name: /db-1/ });
    expect(db1).toHaveAttribute("aria-disabled", "true");
    expect(db1).toHaveAccessibleDescription(/db-1 is not answering\./);
    await expectNoAxeViolations(container, { page: true });

    // The server on screen: on to the source, here.
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => {
      expect(step()).toContain("Source");
    });
  });

  it("moves the wizard to the server chosen, and starts there on the next step", { timeout: 30_000 }, async () => {
    const calls = backend();
    const { user, history } = renderConsole("/apps/new");
    const group = await screen.findByRole("radiogroup", { name: "Server" });
    await user.click(within(group).getByRole("radio", { name: /web-2/ }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => {
      expect(history.location.pathname).toBe("/n/web-2/apps/new");
    });
    await waitFor(() => {
      expect(step()).toContain("Source");
    });
    // Every later step reads web-2.
    await waitFor(() => {
      expect(calls.callsTo("GET /api/nodes/web-2/api/apps").length).toBeGreaterThan(0);
    });
  });

  it("is not there on a lone server", { timeout: 30_000 }, async () => {
    backend([]);
    renderConsole("/apps/new");
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await waitFor(() => {
      expect(step()).toContain("Source");
    });
    expect(screen.queryByRole("radiogroup", { name: "Server" })).toBeNull();
  });
});
