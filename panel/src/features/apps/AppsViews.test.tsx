import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { APPS, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";

/** Four applications: one wrong, one that serves with tags and a preview of it, and one that is stopped. */
const LIST = [
  { ...APPS[0], tags: ["production", "web", "eu"] },
  { ...APPS[1] },
  { domain: "pr-4.shop.example.com", name: "pr-4", app_type: "nextjs", status: "running", active: true, enabled: true, port: 3004, layout: "releases", preview_parent: "shop.example.com" },
  { domain: "old.example.com", name: "old", app_type: "static", status: "stopped", active: false, enabled: false, port: null, layout: "inplace" },
];

const DEPLOYS = [
  { id: 9, domain: "shop.example.com", status: "success", triggered_by: "cli", has_log: true, started_at: "2026-11-05T10:00:00", finished_at: "2026-11-05T10:02:40", duration_s: 160, commit_message: "Upgrade Next.js to 15.2", git_commit: "c07d5e3" },
  { id: 8, domain: "admin.example.com", status: "failed", cancelled: false, triggered_by: "webhook", has_log: true, started_at: "2026-11-05T09:00:00", finished_at: "2026-11-05T09:00:20", duration_s: 20, commit_message: null, git_commit: null },
];

function appsAt(path: string, extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/apps": () => json(200, { apps: LIST, total: LIST.length }),
    "GET /api/deployments": () => json(200, { items: DEPLOYS, total: DEPLOYS.length, next_before_id: null }),
    ...extra,
  });
  const harness = renderConsole(path);
  return { ...harness, backend };
}

describe("a name and its tags, on one line", () => {
  it("draws the tags after the domain, the first two as pebbles and the rest behind +N", async () => {
    appsAt("/apps");
    const shop = await screen.findByRole("link", { name: "shop.example.com" });
    const line = shop.closest('[data-slot="name-with-tags"]');
    if (!(line instanceof HTMLElement)) throw new Error("the name has no line of tags");
    expect(within(line).getByText("production")).toBeInTheDocument();
    expect(within(line).getByText("web")).toBeInTheDocument();
    expect(within(line).getByRole("button", { name: "1 more tag: eu" })).toHaveTextContent("+1");
    // A row without tags is the link alone.
    const admin = screen.getByRole("link", { name: "admin.example.com" });
    expect(admin.closest('[data-slot="name-with-tags"]')).toBeNull();
  });

  it("calls a pull request preview a preview in the tag's place, and says whose it is to a screen reader", async () => {
    appsAt("/apps");
    const preview = await screen.findByRole("link", { name: "pr-4.shop.example.com" });
    const row = preview.closest("tr");
    if (row === null) throw new Error("no row");
    expect(within(row).getByText("preview")).toBeInTheDocument();
    expect(within(row).getByText("Preview of shop.example.com")).toHaveClass("sr-only");
  });
});

describe("the views of the applications column", () => {
  it("lists the applications by state, what needs a person first", async () => {
    appsAt("/apps?view=states");
    expect(await screen.findByRole("heading", { level: 1, name: "Applications by state" })).toBeInTheDocument();
    const headings = await screen.findAllByRole("heading", { level: 2 });
    expect(headings.map((heading) => heading.textContent)).toEqual(["Needs attention", "Running", "Stopped"]);
    const problems = headings[0]?.closest("section");
    if (!problems) throw new Error("no group");
    expect(within(problems).getByRole("link", { name: "admin.example.com" })).toBeInTheDocument();
    expect(within(problems).queryByRole("link", { name: "shop.example.com" })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("lists them by project with the way to each project, and one slab for what no project holds", async () => {
    appsAt("/apps?view=projects");
    expect(await screen.findByRole("heading", { level: 1, name: "Applications by project" })).toBeInTheDocument();
    // Nothing is organised on this server: every application is in the one place nobody named.
    expect(await screen.findByRole("heading", { level: 2, name: "Without a project" })).toBeInTheDocument();
    // The slab says how many it holds, and so does the line of the table's own.
    expect(screen.getAllByText("4 applications").length).toBeGreaterThan(0);
  });

  it("lists the deployments of every application, the newest first, with who started them", async () => {
    appsAt("/apps?view=deployments");
    expect(await screen.findByRole("heading", { level: 1, name: "Deployments of all applications" })).toBeInTheDocument();
    const table = await screen.findByRole("region", { name: "Deployments of every application, newest first" });
    const shop = within(table).getByRole("link", { name: "shop.example.com" });
    expect(shop).toHaveAttribute("href", "/apps/shop.example.com/deployments/9");
    expect(within(table).getByText("Upgrade Next.js to 15.2")).toBeInTheDocument();
    expect(within(table).getByText("Push")).toBeInTheDocument();
    // The filters of the list narrow applications, not a history: no bar over it.
    expect(screen.queryByRole("searchbox", { name: "Search applications" })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says its views in the column, with how many applications there are beside All", async () => {
    appsAt("/apps");
    const column = await screen.findByRole("navigation", { name: "Applications sections" });
    // The count arrives with the list.
    await waitFor(() => {
      expect(within(column).getAllByRole("link").map((link) => link.textContent)).toEqual(["All4", "By project", "By state", "Deployments of all"]);
    });
    expect(within(column).getByRole("link", { name: "By state" })).toHaveAttribute("href", "/apps?view=states");
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    appsAt("/apps?view=states");
    expect(await screen.findByRole("heading", { level: 1, name: "Aplicaciones por estado" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 2, name: "Requiere atención" })).toBeInTheDocument();
  });
});
