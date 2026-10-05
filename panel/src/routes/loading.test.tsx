import { QueryClient } from "@tanstack/react-query";
import { act, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { buildRouter } from "../app/router";
import { renderConsole } from "../test/console";
import { FakeEventSource, FakeWebSocket, SESSION, json } from "../test/fakes";

/**
 * Every page of the console shows that it is loading (design 6.6). An operator who opens a tab
 * and sees nothing guesses that something will arrive, or that the page is finished and empty:
 * the pending state of a page is a skeleton shaped like its content, inside an `aria-busy`
 * region, never a blank area, and never the empty state ("No databases") drawn before the
 * answer is known.
 *
 * The check is made on the real route tree, against a server that answers the session and
 * leaves every other request in flight, so what is on screen is exactly the pending state. It
 * is a table of every route on purpose: a route added without a line here fails the first test,
 * so a page cannot ship without someone having looked at what it draws while it waits.
 */

/** Every route of the console and an address that opens it. */
const PAGES: readonly (readonly [route: string, address: string])[] = [
  ["/", "/"],
  ["/activity", "/activity"],
  ["/apps", "/apps"],
  ["/apps/new", "/apps/new"],
  ["/apps/$domain", "/apps/shop.example.com"],
  ["/apps/$domain/database", "/apps/shop.example.com/database"],
  ["/apps/$domain/deployments", "/apps/shop.example.com/deployments"],
  ["/apps/$domain/deployments/$id", "/apps/shop.example.com/deployments/42"],
  ["/apps/$domain/diagnose", "/apps/shop.example.com/diagnose"],
  ["/apps/$domain/domains", "/apps/shop.example.com/domains"],
  ["/apps/$domain/environment", "/apps/shop.example.com/environment"],
  ["/apps/$domain/logs", "/apps/shop.example.com/logs"],
  ["/apps/$domain/metrics", "/apps/shop.example.com/metrics"],
  ["/apps/$domain/settings", "/apps/shop.example.com/settings"],
  ["/apps/$domain/settings/builds", "/apps/shop.example.com/settings/builds"],
  ["/apps/$domain/settings/delete", "/apps/shop.example.com/settings/delete"],
  ["/apps/$domain/settings/deploy-on-push", "/apps/shop.example.com/settings/deploy-on-push"],
  ["/apps/$domain/settings/deploys", "/apps/shop.example.com/settings/deploys"],
  ["/apps/$domain/settings/export", "/apps/shop.example.com/settings/export"],
  ["/apps/$domain/settings/hooks", "/apps/shop.example.com/settings/hooks"],
  ["/apps/$domain/settings/previews", "/apps/shop.example.com/settings/previews"],
  ["/apps/$domain/settings/resources", "/apps/shop.example.com/settings/resources"],
  ["/backups", "/backups"],
  ["/backups/destinations", "/backups/destinations"],
  ["/backups/schedules", "/backups/schedules"],
  ["/cron", "/cron"],
  ["/databases", "/databases"],
  ["/databases/$engine/$name", "/databases/postgresql/example_production"],
  ["/databases/$engine/$name/backups", "/databases/postgresql/example_production/backups"],
  ["/databases/$engine/$name/connect", "/databases/postgresql/example_production/connect"],
  ["/databases/$engine/$name/data", "/databases/postgresql/example_production/data"],
  ["/databases/$engine/$name/metrics", "/databases/postgresql/example_production/metrics"],
  ["/databases/$engine/$name/query", "/databases/postgresql/example_production/query"],
  ["/databases/$engine/$name/users", "/databases/postgresql/example_production/users"],
  ["/databases/engines", "/databases/engines"],
  ["/databases/engines/$engine/settings", "/databases/engines/postgresql/settings"],
  ["/domains", "/domains"],
  ["/domains/sites", "/domains/sites"],
  ["/domains/sites/$site", "/domains/sites/shop.example.com"],
  ["/fleet", "/fleet"],
  ["/fleet/activity", "/fleet/activity"],
  ["/fleet/apps", "/fleet/apps"],
  ["/fleet/backups", "/fleet/backups"],
  ["/fleet/certificates", "/fleet/certificates"],
  ["/fleet/jobs/$id", "/fleet/jobs/a1b2c3d4"],
  ["/fleet/servers", "/fleet/servers"],
  ["/fleet/updates", "/fleet/updates"],
  ["/integrations/github/callback", "/integrations/github/callback"],
  ["/server", "/server"],
  ["/server/logs", "/server/logs"],
  ["/server/security", "/server/security"],
  ["/server/services", "/server/services"],
  ["/server/services/$name", "/server/services/wasm-shop"],
  ["/server/services/$name/logs", "/server/services/wasm-shop/logs"],
  ["/server/services/$name/unit", "/server/services/wasm-shop/unit"],
  ["/server/storage", "/server/storage"],
  ["/server/system", "/server/system"],
  ["/server/updates", "/server/updates"],
  // Old addresses that redirect: the page they land on is checked as that page.
  ["/services", "/services"],
  ["/services/$name", "/services/wasm-shop"],
  ["/settings", "/settings"],
  ["/settings/about", "/settings/about"],
  ["/settings/accounts", "/settings/accounts"],
  ["/settings/approvals", "/settings/approvals"],
  ["/settings/audit", "/settings/audit"],
  ["/settings/central", "/settings/central"],
  ["/settings/compliance", "/settings/compliance"],
  ["/settings/integrations", "/settings/integrations"],
  ["/settings/notifications", "/settings/notifications"],
  ["/settings/security", "/settings/security"],
  ["/settings/servers", "/settings/servers"],
  ["/settings/tokens", "/settings/tokens"],
];

/** Routes outside the console's shell: before there is a session there is no page to wait on. */
const BEFORE_SIGN_IN = ["/invite", "/login", "/setup", "/welcome"];

/**
 * Pages that read nothing the operator waits for. The page is the form or the notice itself,
 * drawn whole from the first frame, so there is nothing to put a skeleton over.
 */
const NOTHING_TO_WAIT_FOR: Readonly<Record<string, string>> = {
  "/apps/new": "a form: its fields are there from the first frame, and what it reads only feeds later steps",
  "/integrations/github/callback": "reads the address GitHub sent the browser back to, not the server",
};

/**
 * Pages inside a layout that are only a form or a notice about what the layout has already read:
 * once it has answered there is nothing left to wait for.
 */
const NOTHING_LEFT_TO_WAIT_FOR: Readonly<Record<string, string>> = {
  "/apps/$domain/settings/delete": "the danger zone of the application the layout read",
  "/apps/$domain/settings/export": "an action on the application the layout read",
  "/apps/$domain/settings/resources": "a form of the application the layout read; the machine's core count only refines a hint",
};

/**
 * Pages whose empty state is not an answer awaited but the idle state of a tool: the results of
 * a statement nobody has run yet. They still must show their own loading (the saved statements).
 */
const IDLE_BY_DESIGN: Readonly<Record<string, string>> = {
  "/databases/$engine/$name/query": "the results pane says there is nothing yet until a statement is run",
};

const APP = {
  domain: "shop.example.com",
  name: "shop",
  app_type: "nextjs",
  status: "running",
  active: true,
  enabled: true,
  port: 3000,
  layout: "inplace",
};

/**
 * What a layout reads before it shows the page inside it: answered, so the page under it is
 * mounted and its own pending state is what is on screen, not the layout's. The key of an
 * answer is a path, with the query parameters the request must carry.
 */
const BEHIND_A_LAYOUT: readonly (readonly [route: string, answers: Readonly<Record<string, unknown>>])[] = [
  [
    "/apps/$domain",
    {
      "/api/apps/shop.example.com": APP,
      // The layout's own read (the newest five), not the Deployments tab's history.
      "/api/deployments?limit=5": { items: [], total: 0, next_before_id: null },
    },
  ],
];

type FetchMock = ReturnType<typeof vi.fn>;

/** Fetches in flight for ever, but for the session and the answers a case gives. */
function pendingBackend(answers: Readonly<Record<string, unknown>>): FetchMock {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const href = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(href, "http://console.test");
    if (url.pathname === "/api/auth/session") return Promise.resolve(json(200, SESSION));
    for (const [route, body] of Object.entries(answers)) {
      const [path = "", query = ""] = route.split("?");
      const wanted = new URLSearchParams(query);
      if (url.pathname === path && [...wanted].every(([key, value]) => url.searchParams.get(key) === value)) return Promise.resolve(json(200, body));
    }
    return new Promise<Response>(() => undefined);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Waits until the page has asked for everything it is going to ask for and drawn what it draws. */
async function settle(fetchMock: FetchMock): Promise<void> {
  let before = -1;
  for (let round = 0; round < 40; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 25));
    });
    if (fetchMock.mock.calls.length === before) return;
    before = fetchMock.mock.calls.length;
  }
}

/** Whether the element, or anything around it up to the page, is not drawn (`hidden`, `display: none`). */
function isHidden(node: Element, page: Element): boolean {
  for (let current: Element | null = node; current !== null && current !== page; current = current.parentElement) {
    if (current.hasAttribute("hidden") || getComputedStyle(current).display === "none") return true;
  }
  return false;
}

/** What the page says while it waits, for a failing test to print. */
function describePage(page: Element): string {
  return page.textContent.replace(/\s+/g, " ").slice(0, 160);
}

async function expectLoadingShown(route: string, address: string, answers: Readonly<Record<string, unknown>>): Promise<void> {
  const fetchMock = pendingBackend(answers);
  renderConsole(address);
  const page = await screen.findByRole("main");
  await settle(fetchMock);

  // The header names the page and may hold a skeleton of its own (the state pill); the body is
  // what must show that something is coming.
  const skeletons = [...page.querySelectorAll('[data-slot="skeleton"]')].filter(
    (node) => node.closest('[data-slot="header"]') === null && !isHidden(node, page),
  );
  expect(skeletons.length, `nothing is drawn where the page loads; it says: "${describePage(page)}"`).toBeGreaterThan(0);
  // Screen readers hear a region that is busy, not a shape: a skeleton is decoration. The shell
  // around the page (the machine strip, the sidebar) is held to it as much as the page is.
  const silent = [...document.querySelectorAll('[data-slot="skeleton"]')].filter(
    (node) => node.closest('[data-slot="header"]') === null && !isHidden(node, document.body) && node.closest('[aria-busy="true"]') === null,
  );
  expect(
    silent.length,
    `a skeleton outside an aria-busy region is silent to a screen reader: ${silent
      .slice(0, 2)
      .map((node) => (node.parentElement?.outerHTML ?? "").slice(0, 260))      .join(" ||| ")}`,
  ).toBe(0);
  const empty = [...page.querySelectorAll('[data-slot="empty-state"]')].filter((node) => !isHidden(node, page));
  if (!(route in IDLE_BY_DESIGN)) expect(empty.length, "an empty state is drawn before the answer is known").toBe(0);
}

describe("a page that is loading", () => {
  beforeEach(() => {
    // A desktop's width: a list is a table, not a phone's card rows.
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
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  it("is declared for every route of the console", () => {
    const routes = Object.keys(buildRouter(new QueryClient()).routesByPath).sort();
    const declared = [...PAGES.map(([route]) => route), ...BEFORE_SIGN_IN].sort();
    expect(declared).toEqual(routes);
  });

  it("is not left looking finished when it is the session that has not answered", async () => {
    // Before the shell exists there is no page to be loading: the router's own wait is what the
    // operator sees, and past a second it is a skeleton that says what it is doing.
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
    renderConsole("/databases");
    const label = await screen.findByText("Loading the page", undefined, { timeout: 3000 });
    expect(label.closest('[aria-busy="true"]')).not.toBeNull();
    expect(document.querySelector('[aria-busy="true"] [data-slot="skeleton"]')).not.toBeNull();
  });

  it("has the router draw a skeleton for any route that keeps it waiting, not a blank page", () => {
    const router = buildRouter(new QueryClient());
    expect(router.options.defaultPendingComponent).toBeDefined();
    // A second: quicker and nothing blinks (6.6).
    expect(router.options.defaultPendingMs).toBe(1000);
    // Where there is no shell around the placeholder (it waits on the session), it brings the
    // page's own margins.
    const routes = router.routesById;
    for (const id of ["/_console", "/login", "/setup", "/welcome"] as const) expect(routes[id].options.pendingComponent, id).toBeDefined();
  });

  it("gives a reason only for routes that exist", () => {
    const known = PAGES.map(([route]) => route);
    for (const route of [...Object.keys(NOTHING_TO_WAIT_FOR), ...Object.keys(NOTHING_LEFT_TO_WAIT_FOR), ...Object.keys(IDLE_BY_DESIGN)]) {
      expect(known).toContain(route);
    }
  });

  const checked = PAGES.filter(([route]) => !(route in NOTHING_TO_WAIT_FOR));

  it.each(checked)("%s shows a skeleton in a busy region", async (route, address) => {
    await expectLoadingShown(route, address, {});
  });

  const behind = checked
    .filter(([route]) => !(route in NOTHING_LEFT_TO_WAIT_FOR))
    .flatMap(([route, address]) =>
      BEHIND_A_LAYOUT.filter(([layout]) => route.startsWith(`${layout}/`) || route === layout).map(([, answers]) => [route, address, answers] as const),
    );

  it.each(behind)("%s shows one once the layout around it has answered", async (route, address, answers) => {
    await expectLoadingShown(route, address, answers);
  });
});
