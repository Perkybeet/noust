/**
 * The map of this server (checklist M-69): read from the server, drawn in fixed columns, the same
 * connections as a table with a link to each thing, empty and failing states, and the ways to find
 * it (the palette and the Overview's menu; the sidebar is the navigation's to decide).
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

const MAP = {
  layers: [
    {
      id: "names",
      kind: "domain",
      nodes: [
        { id: "domain:shop.example.com", kind: "domain", label: "shop.example.com", domain: "shop.example.com" },
        { id: "domain:www.shop.example.com", kind: "domain", label: "www.shop.example.com", domain: "shop.example.com" },
      ],
    },
    {
      id: "applications",
      kind: "application",
      nodes: [{ id: "app:shop.example.com", kind: "application", label: "shop.example.com", detail: "nextjs", state: "ok", state_word: "running", domain: "shop.example.com" }],
    },
    { id: "databases", kind: "database", nodes: [{ id: "db:postgresql:shop_prod", kind: "database", label: "shop_prod", detail: "postgresql", engine: "postgresql", domain: "shop.example.com" }] },
    { id: "scheduled", kind: "scheduled", nodes: [{ id: "cron:shop-nightly", kind: "scheduled", label: "shop-nightly", detail: "*-*-* 02:00:00", domain: "shop.example.com" }] },
    { id: "data", kind: "storage", nodes: [{ id: "path:shop.example.com:storage", kind: "storage", label: "storage", domain: "shop.example.com" }] },
  ],
  edges: [
    { source: "domain:shop.example.com", target: "app:shop.example.com", relation: "serves" },
    { source: "domain:www.shop.example.com", target: "app:shop.example.com", relation: "serves" },
    { source: "app:shop.example.com", target: "db:postgresql:shop_prod", relation: "uses" },
    { source: "app:shop.example.com", target: "cron:shop-nightly", relation: "runs" },
    { source: "app:shop.example.com", target: "path:shop.example.com:storage", relation: "keeps" },
  ],
  truncated: false,
  projects: 1,
};

function map(extra: Record<string, RouteHandler> = {}, body: unknown = MAP) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("min-width"),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/apps/types": () => json(200, { types: [{ type: "nextjs", name: "Next.js", default_port: 3000 }] }),
    "GET /api/map": () => json(200, body),
    ...extra,
  });
  return { backend, ...renderConsole("/map") };
}

describe("the map", () => {
  it("is the second view of the Overview: its header, its tabs, the map as the body", async () => {
    map();
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    const tabs = await screen.findByRole("navigation", { name: "Overview sections" });
    expect(within(tabs).getByRole("link", { name: "Overview" })).toHaveAttribute("href", "/");
    expect(within(tabs).getByRole("link", { name: "Map" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("figure", { name: "How this server fits together" })).toBeInTheDocument();
  });

  it("draws each layer as a column with what is in it, and says how many there are", async () => {
    map();
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    const figure = await screen.findByRole("figure", { name: "How this server fits together" });
    for (const title of ["Names", "Applications", "Databases", "Scheduled work", "Data"]) {
      expect(within(figure).getByRole("list", { name: title })).toBeInTheDocument();
    }
    expect(within(figure).getByRole("button", { name: "Application shop.example.com, Next.js: Running" })).toBeInTheDocument();
    expect(within(figure).getByRole("button", { name: "Database shop_prod, postgresql" })).toBeInTheDocument();
    expect(screen.getByText("Applications: 1. Databases: 1. Scheduled jobs: 1.")).toBeInTheDocument();
    expect(screen.getByText(/Each line is a relation, not traffic/)).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("offers the same connections as a table, each thing a link to where it is", async () => {
    const { user, location } = map();
    await user.click(await screen.findByRole("button", { name: "Show the connections as a table" }));
    const table = await screen.findByRole("table", { name: "Connections of How this server fits together" });
    for (const relation of ["answers on", "uses", "runs", "keeps"]) expect(within(table).getAllByText(relation).length).toBeGreaterThan(0);
    const database = within(table).getByRole("link", { name: "shop_prod" });
    expect(database).toHaveAttribute("href", "/databases/postgresql/shop_prod");
    expect(within(table).getByRole("link", { name: "shop-nightly" })).toHaveAttribute("href", "/cron?q=shop-nightly");
    const [first] = within(table).getAllByRole("link", { name: "shop.example.com" });
    expect(first).toBeDefined();
    await user.click(first ?? table);
    await waitFor(() => expect(location().pathname).toBe("/apps/shop.example.com"));
  });

  it("stands still when it is read again: the columns are the same and nothing is placed by the server", async () => {
    const { backend, queryClient } = map();
    const figure = await screen.findByRole("figure");
    const before = figure.innerHTML;
    await act(() => queryClient.invalidateQueries({ queryKey: ["map"] }));
    await waitFor(() => expect(backend.callsTo("GET /api/map")).toHaveLength(2));
    expect((await screen.findByRole("figure")).innerHTML).toBe(before);
  });

  it("says there is nothing to map on an empty server, and where to start", async () => {
    map({}, { layers: [], edges: [], truncated: false, projects: 0 });
    expect(await screen.findByText("Nothing to map yet")).toBeInTheDocument();
    expect(screen.getByText("noust create")).toBeInTheDocument();
  });

  it("says when the map is larger than it draws", async () => {
    map({}, { ...MAP, truncated: true });
    expect(await screen.findByText(/The map is larger than it draws/)).toBeInTheDocument();
  });

  it("shows why it could not be read, in the server's words", async () => {
    map({ "GET /api/map": () => problem(500, "internal", "systemctl did not answer", { hint: "Check that systemd is running." }) });
    expect(await screen.findByText(/systemctl did not answer/)).toBeInTheDocument();
  });

  it("is found from the palette", async () => {
    const { user, location } = map();
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    await user.keyboard("{Control>}k{/Control}");
    await user.type(await screen.findByRole("combobox"), "map of this");
    // It says where it lives beside its name: the Map is a tab of the Overview.
    await user.click(await screen.findByRole("option", { name: /^Open the map of this server/ }));
    await waitFor(() => expect(location().pathname).toBe("/map"));
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    map();
    expect(await screen.findByRole("heading", { level: 1, name: "Resumen" })).toBeInTheDocument();
    expect(await screen.findByRole("figure", { name: "Cómo encaja este servidor" })).toBeInTheDocument();
    expect(screen.getByText("Aplicaciones: 1. Bases de datos: 1. Tareas programadas: 1.")).toBeInTheDocument();
  });
});
