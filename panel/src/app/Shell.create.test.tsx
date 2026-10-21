/**
 * The palette's create mode and the pages it finds inside an application (checklist M-24, M-04).
 *
 * "New backup", "New site" and "Issue a certificate" arrive at their page with `?new`, which opens
 * the dialog and is spent, exactly as "New database" does; and on an application's page the palette
 * also finds that application's pages and the sections of its settings, from the lists that draw them.
 */

import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { frameRoutes } from "../features/backups/frameRoutes";
import { renderConsole } from "../test/console";
import { fakeBackend, json } from "../test/fakes";
import type { RouteHandler } from "../test/fakes";

const SITES = { sites: [], total: 0, webserver: "nginx" };

function routes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...frameRoutes(),
    "GET /api/sites": () => json(200, SITES),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/databases/databases": () => json(200, { databases: [], total: 0 }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    "GET /api/apps/types": () => json(200, { types: [] }),
    ...extra,
  };
}

async function search(user: ReturnType<typeof renderConsole>["user"], text: string) {
  await user.keyboard("{Control>}k{/Control}");
  const input = await screen.findByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
  await user.type(input, text);
  return input;
}

describe("create mode", () => {
  it("opens the backup dialog from the palette and spends the link", async () => {
    fakeBackend(routes());
    const { user, router } = renderConsole("/");
    await screen.findByRole("heading", { level: 1 });

    await search(user, "new backup");
    await user.click(await screen.findByRole("option", { name: /New backup/ }));

    expect(await screen.findByRole("dialog", { name: "Back up an application" })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty("new"));
    expect(router.state.location.pathname).toBe("/backups");
  });

  it("opens the site dialog from the palette", async () => {
    fakeBackend(routes());
    const { user, router } = renderConsole("/");
    await screen.findByRole("heading", { level: 1 });

    await search(user, "new site");
    await user.click(await screen.findByRole("option", { name: /New site/ }));

    expect(await screen.findByRole("dialog", { name: "Create a site" })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty("new"));
    expect(router.state.location.pathname).toBe("/domains/sites");
  });

  it("opens the certificate dialog from the palette", async () => {
    fakeBackend(routes());
    const { user, router } = renderConsole("/");
    await screen.findByRole("heading", { level: 1 });

    await search(user, "certificate");
    await user.click(await screen.findByRole("option", { name: /Issue a certificate/ }));

    expect(await screen.findByRole("dialog", { name: "Issue a certificate" })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty("new"));
    expect(router.state.location.pathname).toBe("/domains");
  });

  it("opens each dialog when its page is reached by a link with ?new, once", async () => {
    fakeBackend(routes());
    const { router } = renderConsole("/domains/sites?new=true");

    expect(await screen.findByRole("dialog", { name: "Create a site" })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty("new"));
  });

  it("is found by its Spanish name", async () => {
    fakeBackend(routes());
    const { user } = renderConsole("/");
    await screen.findByRole("heading", { level: 1 });
    const { setLocale } = await import("./locale");
    await act(() => setLocale("es"));

    await user.keyboard("{Control>}k{/Control}");
    const input = await screen.findByRole("combobox");
    await user.type(input, "nueva copia");

    expect(await screen.findByRole("option", { name: /Nueva copia de seguridad/ })).toBeInTheDocument();
  });
});

describe("inside an application", () => {
  it("finds its pages and the sections of its settings, named with the application", async () => {
    fakeBackend(routes());
    const { user } = renderConsole("/apps/shop.example.com");
    await screen.findByRole("heading", { level: 1 });

    await search(user, "build");

    expect(await screen.findByRole("option", { name: /Settings, Build: shop.example.com/ })).toBeInTheDocument();
  });

  it("opens the section it finds", async () => {
    fakeBackend(routes());
    const { user, router } = renderConsole("/apps/shop.example.com");
    await screen.findByRole("heading", { level: 1 });

    await search(user, "resources");
    await user.click(await screen.findByRole("option", { name: /Settings, Resources: shop.example.com/ }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/apps/shop.example.com/settings/resources"));
  });

  it("finds the application's own tabs by their words", async () => {
    fakeBackend(routes());
    const { user } = renderConsole("/apps/shop.example.com");
    await screen.findByRole("heading", { level: 1 });

    await search(user, "logs");

    expect(await screen.findByRole("option", { name: /Logs: shop.example.com/ })).toBeInTheDocument();
  });

  it("does not offer another application's sections on a page that is not an application", async () => {
    fakeBackend(routes());
    const { user } = renderConsole("/cron");
    await screen.findByRole("heading", { level: 1 });

    await search(user, "build");

    await waitFor(() => expect(screen.queryByRole("option", { name: /Build: / })).not.toBeInTheDocument());
  });
});
