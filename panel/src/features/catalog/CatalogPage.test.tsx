import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { catalogueRoutes, recipesRoute } from "./testFixtures";

function catalogue(extra: Parameters<typeof fakeBackend>[0] = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [{ type: "docker-compose", name: "Docker Compose", default_port: 0 }] }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...catalogueRoutes(),
    ...extra,
  });
  return { backend, harness: renderConsole("/catalog") };
}

async function cardOf(title: string): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { level: 2, name: title });
  return heading.closest("li") as HTMLElement;
}

describe("the catalog page", () => {
  it("holds a page of cards while the recipes are read, so the command hint under them does not move", async () => {
    // A page of the catalogue is twenty cards: a skeleton of fewer left the hint on screen
    // until the cards pushed it out of it.
    catalogue({ "GET /api/recipes": () => new Promise<Response>(() => undefined) });
    const busy = await waitFor(() => {
      const grid = document.querySelector('[aria-busy="true"].grid');
      if (grid === null) throw new Error("no skeleton yet");
      return grid;
    });
    expect(busy.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(20);
  });

  it("draws a card for each recipe with what the 3.4 schema says about it", { timeout: 20_000 }, async () => {
    const { harness } = catalogue();
    expect(await screen.findByRole("heading", { level: 1, name: "Applications" })).toBeInTheDocument();

    const vault = await cardOf("Vaultwarden");
    // Its logo is the one the server serves, as an image and never inline.
    const logo = vault.querySelector("img");
    expect(logo).toHaveAttribute("src", "/api/recipes/vaultwarden/logo");
    expect(within(vault).getByText("A lightweight Bitwarden-compatible password manager server.")).toBeInTheDocument();
    expect(within(vault).getByText("v1.37.4")).toBeInTheDocument();
    expect(within(vault).getByText("Security")).toBeInTheDocument();
    expect(within(vault).getByText("Runs in Docker")).toBeInTheDocument();
    // The first tags, then how many more there are.
    const tags = within(vault).getByRole("list", { name: "Tags" });
    expect(within(tags).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["#passwords", "#bitwarden", "#vault", "+1"]);
    expect(within(vault).getByRole("link", { name: /github\.com/ })).toHaveAttribute("href", "https://github.com/dani-garcia/vaultwarden");
    expect(within(vault).getByRole("link", { name: /Documentation of Vaultwarden/ })).toHaveAttribute("href", "https://github.com/dani-garcia/vaultwarden/wiki");
    expect(within(vault).getByText("Updated Oct 6, 2026")).toBeInTheDocument();
    expect(within(vault).getByRole("button", { name: "Use Vaultwarden" })).toBeEnabled();

    // A recipe that runs natively says what it needs instead.
    const wordpress = await cardOf("WordPress");
    expect(within(wordpress).queryByText("Runs in Docker")).not.toBeInTheDocument();
    expect(within(wordpress).getByText("A MySQL database")).toBeInTheDocument();
    expect(within(wordpress).getByText(/PHP-FPM 7\.4 or newer/)).toBeInTheDocument();
    // A recipe fixed on its own shows its revision.
    expect(within(await cardOf("Syncthing")).getByText("v2.1.6-r2")).toBeInTheDocument();
    await expectNoAxeViolations(harness.container);
  });

  it("lists a recipe this release cannot deploy with the reason, and no way to use it", { timeout: 20_000 }, async () => {
    const { harness } = catalogue();
    const { user } = harness;
    await screen.findByRole("heading", { level: 2, name: "Vaultwarden" });
    // 25 recipes, the deployable ones first: the ones this release cannot run are on the last page.
    await user.click(screen.getByRole("button", { name: "Next page" }));
    const ghost = await cardOf("Ghost");
    expect(within(ghost).getByText("Not available in this release")).toBeInTheDocument();
    expect(within(ghost).getByText(/supports only MySQL 8 in production/)).toBeInTheDocument();
    expect(within(ghost).queryByRole("button")).not.toBeInTheDocument();
    await expectNoAxeViolations(harness.container);
  });

  it("searches with the server's own search, keeps it in the address, and counts what it keeps", { timeout: 20_000 }, async () => {
    const { backend, harness } = catalogue();
    const { user } = harness;
    await screen.findByRole("heading", { level: 2, name: "Vaultwarden" });
    expect(screen.getByText("25 recipes")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "Search recipes" }), "bitwarden");
    await waitFor(() => expect(screen.queryByRole("heading", { level: 2, name: "WordPress" })).not.toBeInTheDocument());
    expect(backend.callsTo("GET /api/recipes").some((call) => call.search.get("q") === "bitwarden")).toBe(true);
    expect(screen.getByRole("heading", { level: 2, name: "Vaultwarden" })).toBeInTheDocument();
    expect(screen.getByText("1 of 25 recipes")).toBeInTheDocument();
    expect(harness.location().search).toEqual({ q: "bitwarden" });

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByRole("heading", { level: 2, name: "WordPress" })).toBeInTheDocument();
    expect(harness.location().search).toEqual({});
  });

  it("filters by category, by tag and by what it runs on, and says when nothing matches", { timeout: 30_000 }, async () => {
    const { backend, harness } = catalogue();
    const { user } = harness;
    await screen.findByRole("heading", { level: 2, name: "Vaultwarden" });

    await user.click(screen.getByRole("combobox", { name: "Category" }));
    // Every category that holds a recipe, in the closed list's order, with how many.
    expect(await screen.findByRole("option", { name: "Content (2)" })).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "Files (1)" }));
    await waitFor(() => expect(backend.callsTo("GET /api/recipes").some((call) => call.search.get("category") === "files")).toBe(true));
    expect(await screen.findByRole("heading", { level: 2, name: "Syncthing" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Vaultwarden" })).not.toBeInTheDocument();
    expect(harness.location().search).toEqual({ category: "files" });

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await user.click(await screen.findByRole("combobox", { name: "Runs" }));
    await user.click(await screen.findByRole("option", { name: "Natively" }));
    await waitFor(() => expect(backend.callsTo("GET /api/recipes").some((call) => call.search.get("kind") === "native")).toBe(true));
    expect(await screen.findByRole("heading", { level: 2, name: "WordPress" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Vaultwarden" })).not.toBeInTheDocument();

    // A search that keeps nothing says so and offers to undo itself.
    await user.type(screen.getByRole("searchbox", { name: "Search recipes" }), "zzzz");
    expect(await screen.findByText("No recipe matches these filters.")).toBeInTheDocument();
    await expectNoAxeViolations(harness.container);
  });

  it("offers every tag, found by typing, most used first", { timeout: 20_000 }, async () => {
    const { backend, harness } = catalogue();
    const { user } = harness;
    await screen.findByRole("heading", { level: 2, name: "Vaultwarden" });
    await user.click(screen.getByRole("combobox", { name: "Tag" }));
    const options = await screen.findAllByRole("option");
    // "tools" is on twenty recipes; the rest on fewer.
    expect(options[0]).toHaveTextContent("Every tag");
    expect(options[1]).toHaveTextContent("tools (20)");
    await user.click(screen.getByRole("option", { name: "mesh (1)" }));
    await waitFor(() => expect(backend.callsTo("GET /api/recipes").some((call) => call.search.get("tag") === "mesh")).toBe(true));
    expect(await screen.findByRole("heading", { level: 2, name: "Syncthing" })).toBeInTheDocument();
    expect(harness.location().search).toEqual({ tag: "mesh" });
  });

  it("pages at the operator's size, 20 to start with, and remembers it", { timeout: 20_000 }, async () => {
    const { harness } = catalogue();
    const { user } = harness;
    const list = await screen.findByRole("list", { name: "Recipes" });
    expect(within(list).getAllByRole("heading", { level: 2 })).toHaveLength(20);
    expect(screen.getByText(/Showing 1–20 of 25/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(within(screen.getByRole("list", { name: "Recipes" })).getAllByRole("heading", { level: 2 })).toHaveLength(5));
    expect(screen.getByText(/Showing 21–25 of 25/)).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Rows per page" }));
    await user.click(await screen.findByRole("option", { name: "All" }));
    await waitFor(() => expect(within(screen.getByRole("list", { name: "Recipes" })).getAllByRole("heading", { level: 2 })).toHaveLength(25));
    expect(window.localStorage.getItem("noust.pageSize.recipes")).toBe("all");
  });

  it("opens a recipe's own page from its card", { timeout: 20_000 }, async () => {
    const { harness } = catalogue();
    const { user } = harness;
    const vault = await cardOf("Vaultwarden");
    await user.click(within(vault).getByRole("link", { name: "Details of Vaultwarden" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Vaultwarden" })).toBeInTheDocument();
    expect(harness.location().pathname).toBe("/catalog/vaultwarden");
  });

  it("leads a chosen recipe to the wizard's install form, which opens on it", { timeout: 20_000 }, async () => {
    const { backend, harness } = catalogue();
    const { user } = harness;
    const wordpress = await cardOf("WordPress");
    await user.click(within(wordpress).getByRole("button", { name: "Use WordPress" }));
    // The address says which recipe; the wizard reads it and is on its one step for a recipe.
    expect(await screen.findByRole("heading", { level: 2, name: "Address and variables" })).toBeInTheDocument();
    expect(harness.location().pathname).toBe("/apps/new");
    expect(harness.location().search).toMatchObject({ start: "recipe", recipe: "wordpress" });
    expect(backend.callsTo("GET /api/recipes/wordpress")).toHaveLength(1);
  });

  it("says when the catalog could not be read, with the server's words, and tries again", { timeout: 20_000 }, async () => {
    let failing = true;
    const { harness } = catalogue({
      "GET /api/recipes": (call) =>
        failing ? json(500, { error: "internal", detail: "The recipes could not be read.", hint: null, fields: null, output: null }) : recipesRoute(call),
    });
    expect(await screen.findByText("Could not load the recipes")).toBeInTheDocument();
    expect(screen.getByText("The recipes could not be read.")).toBeInTheDocument();
    failing = false;
    await harness.user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Vaultwarden" })).toBeInTheDocument();
  });
});
