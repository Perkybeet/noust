import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";

const SITES = {
  sites: [
    {
      name: "shop.example.com",
      site_name: "shop.example.com",
      webserver: "nginx",
      enabled: true,
      config_path: "/etc/nginx/sites-available/shop.example.com",
      has_ssl: true,
      server_names: ["shop.example.com"],
      noust_managed: true,
      app: "shop.example.com",
    },
    {
      name: "proggest.es",
      site_name: "proggest",
      webserver: "nginx",
      enabled: true,
      config_path: "/etc/nginx/sites-available/proggest",
      has_ssl: true,
      server_names: ["proggest.es", "www.proggest.es"],
      noust_managed: false,
      app: "proggest.es",
    },
    {
      name: "tools.example.net",
      site_name: "tools.example.net",
      webserver: "nginx",
      enabled: false,
      config_path: "/etc/nginx/sites-available/tools.example.net",
      has_ssl: false,
      server_names: [],
      noust_managed: false,
      app: null,
    },
  ],
  total: 3,
  webserver: "nginx",
};

function sites() {
  screenWidth(1440);
  fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/sites": () => json(200, SITES),
  });
  return renderConsole("/domains/sites");
}

function rowOf(table: HTMLElement, name: string): HTMLElement {
  const row = within(table)
    .getAllByRole("row")
    .find((candidate) => within(candidate).queryAllByText(name).length > 0);
  if (row === undefined) throw new Error(`No row for ${name}`);
  return row;
}

describe("the web server's sites", { timeout: 20_000 }, () => {
  it("says who wrote each site, and which application it serves", async () => {
    sites();
    const table = await screen.findByRole("region", { name: /sites/i });
    await within(table).findByText("tools.example.net");
    const shop = rowOf(table, "shop.example.com");
    expect(within(shop).getByText("Noust")).toBeInTheDocument();
    expect(within(shop).getByRole("link", { name: "shop.example.com" })).toHaveAttribute("href", "/apps/shop.example.com");
    const proggest = rowOf(table, "proggest.es");
    expect(within(proggest).getByText("By hand")).toBeInTheDocument();
    // Its file is not named after the domain: the name is said.
    expect(within(proggest).getByText("proggest")).toBeInTheDocument();
    const tools = rowOf(table, "tools.example.net");
    expect(within(tools).getByText("Not an application's site")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says it in Spanish", async () => {
    await act(() => setLocale("es"));
    sites();
    expect(await screen.findByRole("columnheader", { name: /Escrito por/ })).toBeInTheDocument();
    expect((await screen.findAllByText("A mano")).length).toBe(2);
  });
});
