import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { NOT_FOUND, catalogueRoutes } from "./testFixtures";

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
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

function recipePage(name: string) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [] }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...catalogueRoutes(),
  });
  backend.on("GET /api/recipes/nothing-here", NOT_FOUND);
  return { backend, harness: renderConsole(`/catalog/${name}`) };
}

describe("a recipe's page", () => {
  it("says what the recipe runs, asks, opens and tells, and shows no secret", { timeout: 30_000 }, async () => {
    const { harness } = recipePage("vaultwarden");
    expect(await screen.findByRole("heading", { level: 1, name: "Vaultwarden" })).toBeInTheDocument();
    expect(screen.getByText("A lightweight Bitwarden-compatible password manager server.")).toBeInTheDocument();

    const about = screen.getByRole("region", { name: "About" });
    expect(within(about).getByText("v1.37.4", { selector: "dd span, dd" })).toBeInTheDocument();
    expect(within(about).getByText("AGPL-3.0-only")).toBeInTheDocument();
    expect(within(about).getByText("amd64 and arm64")).toBeInTheDocument();
    expect(within(about).getByRole("link", { name: /Documentation|Docs/ })).toHaveAttribute("href", "https://github.com/dani-garcia/vaultwarden/wiki");

    const runs = screen.getByRole("region", { name: "What it runs" });
    expect(within(runs).getByText("In Docker, as a Compose stack Noust writes")).toBeInTheDocument();
    // The image, by its tag and the digest the registry served for it.
    expect(within(runs).getByText("ghcr.io/dani-garcia/vaultwarden:1.37.4")).toBeInTheDocument();
    expect(within(runs).getByText(/^sha256:efb3cde9/)).toBeInTheDocument();

    // Every variable, and where its value comes from: the generated secret has none to show.
    const variables = screen.getByRole("region", { name: "Variables" });
    const generated = within(variables).getByRole("row", { name: /ADMIN_TOKEN/ });
    expect(within(generated).getByText("Generated for you")).toBeInTheDocument();
    expect(within(generated).getByText("Secret")).toBeInTheDocument();
    const optional = within(variables).getByRole("row", { name: /SIGNUPS_ALLOWED/ });
    expect(within(optional).getByText("You may change it")).toBeInTheDocument();
    expect(within(optional).getByText("true")).toBeInTheDocument();

    // The notes as they will read, with the address still to come.
    const notes = screen.getByRole("region", { name: "After you install it" });
    expect(within(notes).getByText("Open https://<domain> and create your account now: anybody who reaches it can create one.")).toBeInTheDocument();
    expect(within(notes).getByText(/noust env show <domain> --unmask/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "What each version changed" })).getByText("Pinned to the 1.37.4 image.")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Use Vaultwarden" })).toBeEnabled();
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeInTheDocument();
    await expectNoAxeViolations(harness.container);
  });

  it("marks what a recipe cannot run without, and what it opens to the network on purpose", { timeout: 30_000 }, async () => {
    recipePage("freshrss");
    expect(await screen.findByRole("heading", { level: 1, name: "FreshRSS" })).toBeInTheDocument();
    const variables = screen.getByRole("region", { name: "Variables" });
    const required = within(variables).getByRole("row", { name: /ADMIN_EMAIL/ });
    expect(within(required).getByText("Needs a value")).toBeInTheDocument();
    expect(within(required).getByText("You must give it")).toBeInTheDocument();
  });

  it("warns about the ports a recipe opens besides the site", { timeout: 30_000 }, async () => {
    const { harness } = recipePage("syncthing");
    expect(await screen.findByRole("heading", { level: 1, name: "Syncthing" })).toBeInTheDocument();
    const ports = screen.getByRole("region", { name: "Open to the network" });
    expect(within(ports).getByText("This recipe opens ports to the network on purpose")).toBeInTheDocument();
    expect(within(ports).getByText("22000/tcp")).toBeInTheDocument();
    expect(within(ports).getByText("22000/udp")).toBeInTheDocument();
    await expectNoAxeViolations(harness.container);
  });

  it("says what a native recipe needs the server to have, and what Noust sets up", { timeout: 30_000 }, async () => {
    recipePage("wordpress");
    expect(await screen.findByRole("heading", { level: 1, name: "WordPress" })).toBeInTheDocument();
    const needs = screen.getByRole("region", { name: "What it needs" });
    expect(within(needs).getByText("A MySQL database (provisioned by Noust)")).toBeInTheDocument();
    expect(within(needs).getByText(/PHP-FPM 7\.4 or newer/)).toBeInTheDocument();
    const runs = screen.getByRole("region", { name: "What it runs" });
    expect(within(runs).getByText("Natively, built and started by Noust")).toBeInTheDocument();
    expect(within(runs).getByText("https://wordpress.org/latest.tar.gz")).toBeInTheDocument();
  });

  it("lists a recipe this release cannot deploy with its reason and no way to use it", { timeout: 30_000 }, async () => {
    recipePage("ghost");
    expect(await screen.findByRole("heading", { level: 1, name: "Ghost" })).toBeInTheDocument();
    expect(screen.getByText("Not available in this release")).toBeInTheDocument();
    expect(screen.getByText(/supports only MySQL 8 in production/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Use / })).not.toBeInTheDocument();
  });

  it("goes to the wizard's install form from its one primary action", { timeout: 30_000 }, async () => {
    const { harness } = recipePage("vaultwarden");
    await harness.user.click(await screen.findByRole("button", { name: "Use Vaultwarden" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Address and variables" })).toBeInTheDocument();
    expect(harness.location().search).toMatchObject({ start: "recipe", recipe: "vaultwarden" });
  });

  it("says there is no such recipe, with the server's words", { timeout: 20_000 }, async () => {
    recipePage("nothing-here");
    expect(await screen.findByText("There is no recipe called nothing-here")).toBeInTheDocument();
    expect(screen.getByText("No recipe called nothing-here")).toBeInTheDocument();
    expect(screen.getByText("Open the catalog to see the recipes this release ships.")).toBeInTheDocument();
  });
});
