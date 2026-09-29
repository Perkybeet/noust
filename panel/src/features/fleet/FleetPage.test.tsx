import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import { SSH_TIMEOUT, centralSession, fleetRoutes } from "./testFixtures";

function rowOf(table: HTMLElement, name: string): HTMLElement {
  const row = within(table)
    .getAllByRole("row")
    .find((candidate) => within(candidate).queryByText(name, { selector: "[translate=no]" }) !== null);
  if (row === undefined) throw new Error(`no row for ${name}`);
  return row;
}

describe("the Fleet page", () => {
  it("shows every server with its reachability, version, readings and problems, and passes axe", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { container } = renderConsole("/fleet");
    expect(await screen.findByRole("heading", { level: 1, name: "Fleet" })).toBeInTheDocument();

    const table = await screen.findByRole("region", { name: "Servers of this fleet" });
    const local = await waitFor(() => rowOf(table, "web-01"));
    expect(within(local).getByText("This server")).toBeInTheDocument();
    const web2 = rowOf(table, "web-2");
    await within(web2).findByText("7.5%");
    expect(within(web2).getByText("Reachable")).toBeInTheDocument();
    expect(within(web2).getByText("3 running")).toBeInTheDocument();
    await within(web2).findByText("1 expiring");
    expect(within(web2).queryByText("Other version")).not.toBeInTheDocument();
    const db1 = rowOf(table, "db-1");
    await within(db1).findByText("Unreachable");
    // db-1 runs 1.9.0 against this central's 2.0.0: said in words, not only in colour.
    expect(within(db1).getByText("Other version")).toBeInTheDocument();
    expect(within(db1).getByText("Runs 1.9.0; this central runs 2.0.0")).toBeInTheDocument();
    expect(within(web2).getByRole("link", { name: "Open web-2" })).toHaveAttribute("href", "/n/web-2");

    const attention = screen.getByRole("region", { name: /Needs attention/ });
    // The unreachable server first, with ssh's own words, verbatim, under the fix.
    await within(attention).findByText("The central cannot reach this server");
    expect(within(attention).getByText(SSH_TIMEOUT)).toBeInTheDocument();
    expect(within(attention).getByText(/Check that the node is up/)).toBeInTheDocument();
    expect(within(attention).getByRole("button", { name: "Test db-1" })).toBeInTheDocument();
    // web-2's own problems, opening on web-2.
    await within(attention).findByRole("link", { name: "api.example.com" });
    expect(within(attention).getByRole("link", { name: "api.example.com" })).toHaveAttribute("href", "/n/web-2/apps/api.example.com");
    expect(within(attention).getByRole("link", { name: "Diagnose api.example.com" })).toHaveAttribute(
      "href",
      "/n/web-2/apps/api.example.com/diagnose",
    );
    expect(within(attention).getByText("Certificate expires in 5 days")).toBeInTheDocument();
    expect(within(attention).getAllByText("web-2").length).toBeGreaterThan(0);

    await expectNoAxeViolations(container, { page: true });
  });

  it("explains what a fleet is when there is no server yet, and links to add one", { timeout: 20_000 }, async () => {
    fakeBackend(fleetRoutes(centralSession(), []));
    renderConsole("/fleet");
    expect(await screen.findByRole("heading", { level: 2, name: "No servers in this fleet yet" })).toBeInTheDocument();
    expect(screen.getByText(/A fleet is several Noust servers run from one console/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add a server" })).toHaveAttribute("href", "/settings/servers?add=true");
  });

  it("reads nothing through the tunnels while the central is locked, and says why", { timeout: 20_000 }, async () => {
    window.sessionStorage.setItem("noust.central.continue-locked", "1");
    const backend = fakeBackend(fleetRoutes(centralSession({ sealed: true, locked: true })));
    renderConsole("/fleet");
    expect(await screen.findByText("Its servers are out of reach until you unlock its sealed secrets.")).toBeInTheDocument();
    const table = await screen.findByRole("region", { name: "Servers of this fleet" });
    await waitFor(() => {
      expect(within(rowOf(table, "web-2")).getAllByText("Locked").length).toBeGreaterThan(0);
    });
    expect(backend.calls.some((call) => call.path.startsWith("/api/nodes/web-2/"))).toBe(false);
    window.sessionStorage.clear();
  });

  it("speaks Spanish, around the servers' own words, with no accessibility violations", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { container } = renderConsole("/fleet");
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    await act(async () => {
      await setLocale("es");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Flota" })).toBeInTheDocument();
    const attention = screen.getByRole("region", { name: /Requiere atención/ });
    await within(attention).findByText("La central no puede llegar a este servidor");
    // ssh's words are never translated.
    expect(within(attention).getByText(SSH_TIMEOUT)).toBeInTheDocument();
    expect(await screen.findByText("Otra versión")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });
});

describe("a hub", () => {
  it("opens the fleet instead of its own applications, and says to pick a server", { timeout: 20_000 }, async () => {
    fakeBackend(fleetRoutes(centralSession({ role: "hub" })));
    const { location } = renderConsole("/apps");
    expect(await screen.findByRole("heading", { level: 1, name: "Fleet" })).toBeInTheDocument();
    expect(location().pathname).toBe("/fleet");
    expect(screen.getByText("This central deploys nothing itself")).toBeInTheDocument();
    const table = await screen.findByRole("region", { name: "Servers of this fleet" });
    const local = await waitFor(() => rowOf(table, "web-01"));
    expect(within(local).getByText("Hub")).toBeInTheDocument();
    expect(within(local).getAllByText("Deploys nothing").length).toBeGreaterThan(0);
    // Nothing in the sidebar leads to a page a hub only redirects from.
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Fleet" })).toBeInTheDocument();
    for (const name of ["Overview", "Applications", "Databases", "Services", "Backups"]) {
      expect(within(nav).queryByRole("link", { name })).toBeNull();
    }
  });

  it("keeps a server's own pages: /n/web-2/apps is web-2's", { timeout: 20_000 }, async () => {
    fakeBackend({
      ...fleetRoutes(centralSession({ role: "hub" })),
      "GET /api/nodes/web-2/api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    });
    const { location } = renderConsole("/n/web-2/apps");
    expect(await screen.findByRole("heading", { level: 1, name: "Applications" })).toBeInTheDocument();
    expect(location().pathname).not.toBe("/fleet");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Applications" })).toBeInTheDocument();
  });

  it("is still a plain server's overview when the central is not a hub", async () => {
    fakeBackend(fleetRoutes());
    renderConsole("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
  });
});

describe("a server that answers but cannot be read", () => {
  it("says so under its name, in the server's own words", { timeout: 20_000 }, async () => {
    const backend = fakeBackend(fleetRoutes());
    backend.on("GET /api/nodes/web-2/api/system/machine", () =>
      json(500, { error: "internal", detail: "psutil.AccessDenied: /proc/stat", hint: null, fields: null, output: null }),
    );
    renderConsole("/fleet");
    const attention = await screen.findByRole("region", { name: /Needs attention/ });
    expect(await within(attention).findByText("Could not read this server's machine")).toBeInTheDocument();
    expect(within(attention).getByText("psutil.AccessDenied: /proc/stat")).toBeInTheDocument();
  });
});
