import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { act, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import type { FleetServer } from "./fleetServers";
import { ServersTable } from "./ServersTable";
import type { ServersTableRow } from "./ServersTable";

const STALE: FleetServer = { name: "web-02", answer: { kind: "stale", since: new Date(Date.now() - 3 * 3_600_000 - 30 * 60_000 - 20_000) } };

const ROWS: readonly ServersTableRow[] = [
  { server: { name: "central-01", central: true }, summary: "Active, 7 rules", note: "Changed 3 d ago", state: { status: "running", label: "Filters" } },
  { server: { name: "web-01" }, summary: "Active, 9 rules", note: "Changed 12 d ago", state: { status: "running", label: "Filters" } },
  { server: STALE, summary: "Active, 9 rules", state: { status: "running", label: "Filters" } },
  { server: { name: "vps-03" }, summary: "ufw installed, off", state: { status: "failed", label: "Does not filter" } },
  { server: { name: "nas-01", answer: { kind: "unsupported", version: "4.2.1" } } },
  { server: { name: "db-02", answer: { kind: "unreachable" } } },
];

async function renderRouted(ui: ReactNode) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "$", component: () => <main>{ui}</main> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const view = render(<RouterProvider router={router} />);
  await screen.findByRole("main");
  return view;
}

/** A window wide enough for a table: jsdom has no layout, and under 640px a table is cards. */
function stubWidth(wide: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

function table(rows: readonly ServersTableRow[] | undefined = ROWS, extra: Partial<Parameters<typeof ServersTable>[0]> = {}) {
  return <ServersTable caption="Firewall of every server" summaryHeader="Firewall" rows={rows} open={(server) => ({ to: "/network", search: { node: server.name } })} {...extra} />;
}

function rowOf(name: string): HTMLElement {
  return screen.getByRole("row", { name: new RegExp(name) });
}

describe("ServersTable", () => {
  beforeEach(() => {
    stubWidth(true);
  });

  it("is one row for each server, in the order it was given, with what the section says of it", async () => {
    await renderRouted(table());
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Server", "Firewall", "State", "Actions"]);
    const body = screen.getAllByRole("rowgroup")[1];
    if (body === undefined) throw new Error("no body");
    expect(within(body).getAllByRole("row")).toHaveLength(6);
    const row = rowOf("web-01");
    expect(within(row).getByText("web-01")).toHaveAttribute("translate", "no");
    expect(within(row).getByText("Active, 9 rules")).toBeInTheDocument();
    expect(within(row).getByText("Changed 12 d ago")).toBeInTheDocument();
    expect(within(row).getByText("Filters")).toBeInTheDocument();
  });

  it("says Open on the server, as a link that goes where the page says, and never as a button", async () => {
    await renderRouted(table());
    const link = screen.getByRole("link", { name: "Open on web-01" });
    expect(link).toHaveAttribute("href", "/network?node=web-01");
    expect(link.className).toContain("elev-raised");
    expect(screen.queryByRole("button", { name: /Open on/ })).not.toBeInTheDocument();
    // One for every server of the table: nothing is left without a way in.
    expect(screen.getAllByRole("link")).toHaveLength(6);
  });

  it("marks this central's own row with the word central", async () => {
    await renderRouted(table());
    expect(within(rowOf("central-01")).getByText("central")).toBeInTheDocument();
  });

  it("says that a server that did not answer now shows the last it gave, and how old that is", async () => {
    await renderRouted(table());
    const row = rowOf("web-02");
    expect(within(row).getByText("Data from 3h 30m ago")).toBeInTheDocument();
    expect(within(row).getByText("Active, 9 rules")).toBeInTheDocument();
    expect(screen.getAllByText(/Data from/)).toHaveLength(1);
  });

  it("says that a Noust that does not know the view has nothing of it, as a state that is no fault", async () => {
    await renderRouted(table());
    const row = rowOf("nas-01");
    expect(within(row).getByText("Its Noust does not have this view")).toBeInTheDocument();
    expect(within(row).getByText("Not available")).toBeInTheDocument();
    expect(row.querySelector("[data-glyph='question']")).not.toBeNull();
    // Its page still opens: it says there what it lacks.
    expect(within(row).getByRole("link", { name: "Open on nas-01" })).toBeInTheDocument();
  });

  it("says that a server that gave nothing is not answering, as a failure", async () => {
    await renderRouted(table());
    const row = rowOf("db-02");
    expect(within(row).getByText("Nothing to show: it does not answer.")).toBeInTheDocument();
    expect(within(row).getByText("Not answering")).toBeInTheDocument();
    expect(row.querySelector("[data-glyph='cross']")).not.toBeNull();
  });

  it("draws no column of state that nobody has anything to say in", async () => {
    await renderRouted(table([{ server: { name: "web-01" }, summary: "Active, 9 rules" }, { server: { name: "web-02" }, summary: "Active, 6 rules" }]));
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Server", "Firewall", "Actions"]);
  });

  it("draws the state of a server that did not answer even where no row has one: it is the way that row says it", async () => {
    await renderRouted(table([{ server: { name: "web-01" }, summary: "Active, 9 rules" }, { server: { name: "nas-01", answer: { kind: "unsupported", version: "4.2.1" } } }]));
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Server", "Firewall", "State", "Actions"]);
    expect(within(rowOf("web-01")).queryByText("Not available")).not.toBeInTheDocument();
  });

  it("is a door and nothing more when the section is a tool of one machine: the server and the way in", async () => {
    await renderRouted(<ServersTable caption="Files of every server" rows={[{ server: { name: "central-01", central: true } }, { server: { name: "web-01" } }]} open={(server) => ({ to: "/server/files", search: { node: server.name } })} />);
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Server", "Actions"]);
    expect(screen.getByRole("link", { name: "Open on web-01" })).toHaveAttribute("href", "/server/files?node=web-01");
  });

  it("draws the skeleton while the servers have not been read", async () => {
    // Not `table(undefined)`: an undefined argument is the default's.
    await renderRouted(<ServersTable caption="Firewall of every server" summaryHeader="Firewall" rows={undefined} open={() => ({ to: "/network" })} />);
    expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
  });

  it("says it in Spanish", async () => {
    await act(() => setLocale("es"));
    await renderRouted(table());
    expect(screen.getByRole("link", { name: "Abrir en web-01" })).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader")[0]).toHaveTextContent("Servidor");
    expect(within(rowOf("nas-01")).getByText("Su Noust no tiene esta vista")).toBeInTheDocument();
    expect(screen.getByText(/Dato de hace 3 h 30 min/)).toBeInTheDocument();
  });

  it("has no accessibility violations as a table", async () => {
    const { container } = await renderRouted(table());
    await expectNoAxeViolations(container);
  });

  it("has no accessibility violations as cards on a phone, with its way in on every card", async () => {
    stubWidth(false);
    const { container } = await renderRouted(table());
    expect(screen.getAllByRole("link", { name: /^Open on / })).toHaveLength(6);
    await expectNoAxeViolations(container);
  });
});
