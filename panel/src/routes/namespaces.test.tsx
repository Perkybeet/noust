import { act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../app/App";
import { buildRouter } from "../app/router";
import { CHROME, hasNamespace } from "../i18n/catalogs";
import { goCold, drawnKeys } from "../test/coldCatalog";
import { renderConsole } from "../test/console";
import { SESSION, json } from "../test/fakes";

/**
 * A page never paints a text its catalog has not loaded.
 *
 * The console ships the chrome of English and nothing else; a page's namespaces are chunks that the
 * router fetches before it shows the page (routes/__root.tsx, i18n/routeNamespaces.ts). These tests
 * open every route of the console on a cold catalog, as a browser that has just arrived, and hold the
 * promise from both sides: no `t()` found its namespace missing, and no message key is on the screen.
 * A page that starts to draw another feature's texts fails here until `npm run i18n:routes` has been
 * run, and says which namespace it was.
 */

/** An address for each `$parameter`: any value will do, the page is only asked to open. */
const SAMPLES: Readonly<Record<string, string>> = {
  $domain: "shop.example.com",
  $id: "42",
  $engine: "postgresql",
  $name: "wasm-shop",
  $site: "shop.example.com",
  $team: "acme",
  $project: "web",
};

/** Every route of the console as an address that opens it, found in the route tree and not in a list to keep. */
function addresses(): string[] {
  const router = buildRouter(createQueryClient());
  const found = new Set<string>();
  const routes = Object.values(router.routesById) as unknown as readonly { readonly id: string; readonly fullPath: string }[];
  for (const route of routes) {
    const path = route.fullPath.replace(/\$\w+/g, (name: string) => SAMPLES[name] ?? "sample");
    if (route.id === "__root__") continue;
    found.add(path.length > 1 ? path.replace(/\/$/, "") : path);
  }
  return [...found].sort();
}

/** The session is answered; every other request is in flight for ever, so the page is on its first frame. */
function pendingBackend(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const href = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (new URL(href, "http://console.test").pathname === "/api/auth/session") return Promise.resolve(json(200, SESSION));
      return new Promise<Response>(() => undefined);
    }),
  );
}

/** Lets the router load and the page draw what it draws before any of its data has arrived. */
async function settle(): Promise<void> {
  for (let round = 0; round < 12; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

describe("a cold console", () => {
  let restore: (() => Promise<void>) | undefined;

  afterEach(async () => {
    await restore?.();
    restore = undefined;
  });

  it("starts with the chrome and nothing else", () => {
    const cold = goCold();
    restore = cold.restore;
    expect(hasNamespace("en", "common")).toBe(true);
    expect(CHROME).toContain("common");
    expect(hasNamespace("en", "databases")).toBe(false);
  });

  it("finds a route for every address it opens", () => {
    // A guard on the test itself: the route tree is read, not listed, and it is not empty.
    expect(addresses().length).toBeGreaterThan(100);
  });

  it.each(addresses())("%s opens without a text missing", async (address) => {
    const cold = goCold();
    restore = cold.restore;
    pendingBackend();
    renderConsole(address);
    await settle();
    expect(cold.misses).toEqual([]);
    expect(drawnKeys()).toEqual([]);
  });
});
