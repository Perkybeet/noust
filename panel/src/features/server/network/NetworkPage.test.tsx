import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { onDesktop, serverRoutes } from "../testing";

beforeEach(onDesktop);

function network(path: string, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({ ...signedInRoutes(), ...serverRoutes(), ...extra });
  const harness = renderConsole(path);
  return { ...harness, backend };
}

describe("Network and ports", () => {
  it("lists every port with what holds it in the product's words, one holder for each", async () => {
    network("/server/network");
    const table = await screen.findByRole("table", { name: "Ports and what uses them" });
    const rows = within(table).getAllByRole("row").slice(1);
    const text = rows.map((row) => row.textContent);

    expect(text.find((row) => row.startsWith("22/tcp"))).toContain("SSH");
    expect(text.find((row) => row.startsWith("443/tcp"))).toContain("The web server");
    // The kernel's own report of the application's port is the same port, said once, as the application.
    const app = text.find((row) => row.startsWith("3000/tcp"));
    expect(app).toContain("Application");
    expect(app).toContain("shop.example.net");
    expect(app).not.toContain("Process");
    expect(within(table).getByRole("link", { name: "shop.example.net" })).toHaveAttribute("href", "/apps/shop.example.net");
    // A port nothing in Noust accounts for shows the process that holds it, and says who can reach it.
    const database = text.find((row) => row.startsWith("5432/tcp"));
    expect(database).toContain("docker-proxy");
    expect(database).toContain("Around the firewall");
  });

  it("does not list the machine's reservation of a port over UDP as a port in use", async () => {
    network("/server/network");
    const table = await screen.findByRole("table", { name: "Ports and what uses them" });

    expect(within(table).queryByText("443/udp")).toBeNull();
    expect(within(table).getByText("443/tcp")).toBeInTheDocument();
  });

  it("answers who uses a port when its number is typed", async () => {
    const { user } = network("/server/network");
    await user.type(await screen.findByRole("searchbox", { name: "Search ports" }), "5432");

    const table = await screen.findByRole("table", { name: "Ports and what uses them" });
    await waitFor(() => {
      expect(within(table).getAllByRole("row")).toHaveLength(2);
    });
    expect(within(table).getByText("docker-proxy")).toBeInTheDocument();
  });

  it("says a port is free and offers to open it, with the port already filled in", async () => {
    const { user, backend } = network("/server/network", {
      "POST /api/server/security/firewall/rules": () => json(202, { job_id: "j1", message: "queued", status: "pending" }),
    });
    await user.type(await screen.findByRole("searchbox", { name: "Search ports" }), "25565");
    expect(await screen.findByText("Nothing on this server uses port 25565.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open port 25565" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Open or close a port" });
    expect(within(dialog).getByLabelText("Port")).toHaveValue("25565");
    await user.click(within(dialog).getByRole("button", { name: "Add rule" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/security/firewall/rules")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/server/security/firewall/rules")[0]?.body).toMatchObject({ action: "allow", port: 25565, proto: "tcp", source: "any" });
  });

  it("opens a port from the header, and the primary action is that one", async () => {
    const { user } = network("/server/network");
    await user.click(await screen.findByRole("button", { name: "Open a port" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Open or close a port" });
    expect(within(dialog).getByLabelText("Port")).toHaveValue("");
  });

  it("offers to turn the firewall on, and no way to open a port, when it is off", async () => {
    network("/server/network", {
      "GET /api/server/security/firewall": () =>
        json(200, {
          firewall: { backend: "ufw", active: false, default_incoming: "deny", rules: [], installed: true, status: "inactive", others: [], warnings: [], error: "" },
          ports: [],
          protected_ports: [],
          session_sources: [],
        }),
    });

    expect(await screen.findByText("The firewall is off")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Turn it on" })).toHaveAttribute("href", "/network/firewall");
    expect(screen.queryByRole("button", { name: "Open a port" })).toBeNull();
  });

  it("shows the firewall and the interfaces as sections of Network, in its column, and none is called just Ports", async () => {
    const { user } = network("/server/network", {
      "GET /api/system/network": () => json(200, { interfaces: [] }),
    });
    const sections = await screen.findByRole("navigation", { name: "Network sections" });
    expect(within(sections).getAllByRole("link").map((link) => link.textContent.replace(/\d+$/, ""))).toEqual(["Published ports", "Tunnels", "Firewall", "Listening ports", "Interfaces"]);
    expect(screen.queryByRole("radio")).toBeNull();

    await user.click(within(sections).getByRole("link", { name: "Firewall" }));
    expect(await screen.findByText("Add rule")).toBeInTheDocument();
    await user.click(within(screen.getByRole("navigation", { name: "Network sections" })).getByRole("link", { name: "Interfaces" }));
    expect(await screen.findByText("No network interface was reported.")).toBeInTheDocument();
  });

  it("leads to Published ports, the first section of Network, from every view", async () => {
    const { user } = network("/server/network");
    // The page's own sentence and the column's entry say the same thing: the page's is the one in the main landmark.
    expect(await within(await screen.findByRole("main")).findByRole("link", { name: "Published ports" })).toHaveAttribute("href", "/network");

    await user.click(within(screen.getByRole("navigation", { name: "Network sections" })).getByRole("link", { name: "Firewall" }));
    expect(await within(screen.getByRole("main")).findByRole("link", { name: "Published ports" })).toHaveAttribute("href", "/network");
  });

  it("opens the firewall where the Security tab had it, and where the Network tab did", async () => {
    const { location } = network("/server/security?view=firewall");
    expect(await screen.findByText("Add rule")).toBeInTheDocument();
    expect(location().pathname).toBe("/network/firewall");
    expect(within(screen.getByRole("navigation", { name: "Network sections" })).getByRole("link", { name: /^Firewall/ })).toHaveAttribute("aria-current", "page");
  });

  it("shows what the firewall does about each port that listens, and says what Docker published around it", async () => {
    network("/network/firewall");

    expect(await screen.findByText("Docker publishes 1 port around the firewall")).toBeInTheDocument();
    expect(screen.getByText(/^active · incoming traffic/)).toHaveAttribute("data-state", "running");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("has no accessibility violations", async () => {
    network("/server/network");
    await screen.findByRole("table", { name: "Ports and what uses them" });

    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
