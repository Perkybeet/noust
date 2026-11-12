import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { act, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import type { FleetServer } from "./fleetServers";
import { ScopeLine } from "./ScopeLine";

const HOUR = 3_600_000;

const WEB_01: FleetServer = { name: "web-01" };
const WEB_02_STALE: FleetServer = { name: "web-02", answer: { kind: "stale", since: new Date(Date.now() - 3 * HOUR - 30 * 60_000 - 20_000) } };
const DB_02: FleetServer = { name: "db-02" };
const VPS_03_OLD: FleetServer = { name: "vps-03", answer: { kind: "unsupported", version: "4.2.1" } };
const NAS_01_DOWN: FleetServer = { name: "nas-01", answer: { kind: "unreachable" } };

async function renderRouted(ui: ReactNode) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "$", component: () => <main>{ui}</main> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const view = render(<RouterProvider router={router} />);
  await screen.findByRole("main");
  return view;
}

describe("ScopeLine", () => {
  it("says whose the page is: the servers that have what it shows, counted", () => {
    render(<ScopeLine scope={{ kind: "all", servers: [WEB_01, DB_02, WEB_02_STALE, VPS_03_OLD, NAS_01_DOWN] }} />);
    // Stale rows are shown (the last it gave); a Noust that does not know the view and a server with nothing are not.
    expect(screen.getByText("From the 3 servers that have it.")).toBeInTheDocument();
  });

  it("says the one server, and nobody when there is none", () => {
    const { rerender } = render(<ScopeLine scope={{ kind: "all", servers: [WEB_01] }} />);
    expect(screen.getByText("From the one server that has it.")).toBeInTheDocument();
    rerender(<ScopeLine scope={{ kind: "all", servers: [VPS_03_OLD] }} />);
    expect(screen.getByText("No server has it.")).toBeInTheDocument();
  });

  it("names the server of a page on one, in mono, and says how to see them all", () => {
    render(<ScopeLine scope={{ kind: "server", server: WEB_01 }} />);
    const line = screen.getByText(/To see them all, choose All servers above\./).closest("p");
    expect(line).toHaveTextContent("From web-01. To see them all, choose All servers above.");
    expect(within(line as HTMLElement).getByText("web-01")).toHaveAttribute("translate", "no");
  });

  it("leaves out the way to see them all when the page says it elsewhere", () => {
    render(<ScopeLine scope={{ kind: "server", server: WEB_01 }} hint={false} />);
    expect(screen.getByText("web-01").closest("p")).toHaveTextContent(/^From web-01\.$/);
  });

  it("says who did not answer fresh, with how long ago they last did, as a warning with its own shape", () => {
    render(<ScopeLine scope={{ kind: "all", servers: [WEB_01, WEB_02_STALE] }} />);
    const row = screen.getByText("web-02").closest("li");
    expect(row).toHaveTextContent("web-02 has not answered for 3h 30m: what it last gave is shown.");
    expect(row?.querySelector("[data-glyph='triangle']")).not.toBeNull();
    // The fresh one says nothing: the page is about it and it answered.
    expect(screen.queryByText("web-01")).not.toBeInTheDocument();
  });

  it("says a stale server without an age when there is no moment to count from", () => {
    render(<ScopeLine scope={{ kind: "all", servers: [{ name: "web-02", answer: { kind: "stale" } }] }} />);
    expect(screen.getByText("web-02").closest("li")).toHaveTextContent("web-02 has not answered: what it last gave is shown.");
  });

  it("says who runs a Noust that does not know the view, with its version, as the grey of what it cannot say", () => {
    render(<ScopeLine scope={{ kind: "all", servers: [WEB_01, VPS_03_OLD] }} />);
    const row = screen.getByText("vps-03").closest("li");
    expect(row).toHaveTextContent("vps-03 runs Noust 4.2.1, which does not know this view: it is not listed here.");
    expect(row?.querySelector("[data-glyph='question']")).not.toBeNull();
  });

  it("says a version it does not know without inventing one", () => {
    render(<ScopeLine scope={{ kind: "all", servers: [{ name: "vps-03", answer: { kind: "unsupported" } }] }} />);
    expect(screen.getByText("vps-03").closest("li")).toHaveTextContent("vps-03 runs a Noust that does not know this view: it is not listed here.");
  });

  it("says who gave nothing, as a failure", () => {
    render(<ScopeLine scope={{ kind: "all", servers: [WEB_01, NAS_01_DOWN] }} />);
    const row = screen.getByText("nas-01").closest("li");
    expect(row).toHaveTextContent("nas-01 does not answer: nothing of it is shown.");
    expect(row?.querySelector("[data-glyph='cross']")).not.toBeNull();
  });

  it("tells the one server of a page on it when that one is the one that did not answer", () => {
    render(<ScopeLine scope={{ kind: "server", server: WEB_02_STALE }} />);
    expect(screen.getByText("web-02", { selector: "li *" }).closest("li")).toHaveTextContent("web-02 has not answered for 3h 30m");
  });

  it("says a library is the same on every server and has nothing to say of who answered", () => {
    render(<ScopeLine scope={{ kind: "library" }} />);
    expect(screen.getByText("The same on every server: the server is chosen when you use it.")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("opens the page of a server it names when the page says where", async () => {
    await renderRouted(<ScopeLine scope={{ kind: "all", servers: [WEB_02_STALE] }} serverTarget={(server) => ({ to: "/server", search: { node: server.name } })} />);
    expect(screen.getByRole("link", { name: "web-02" })).toHaveAttribute("href", "/server?node=web-02");
  });

  it("draws no link where the page gives no place to go", () => {
    render(<ScopeLine scope={{ kind: "all", servers: [WEB_02_STALE] }} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("draws nothing coloured but the glyph of a state: the sentences stay in the stone's text", () => {
    const { container } = render(<ScopeLine scope={{ kind: "all", servers: [WEB_02_STALE, VPS_03_OLD, NAS_01_DOWN] }} />);
    for (const sentence of container.querySelectorAll("li > span")) {
      expect(sentence.className).not.toMatch(/text-(warn|fail|ok|idle)/);
    }
    for (const glyph of container.querySelectorAll("[data-glyph]")) expect(glyph.getAttribute("aria-hidden")).toBe("true");
  });

  it("says it in Spanish with the same shapes", async () => {
    await act(() => setLocale("es"));
    render(<ScopeLine scope={{ kind: "all", servers: [WEB_01, DB_02, WEB_02_STALE, VPS_03_OLD] }} />);
    expect(screen.getByText("De los 3 servidores que lo tienen.")).toBeInTheDocument();
    expect(screen.getByText("web-02").closest("li")).toHaveTextContent("web-02 no responde desde hace 3 h 30 min: se ve lo último que dio.");
    expect(screen.getByText("vps-03").closest("li")).toHaveTextContent("vps-03 tiene Noust 4.2.1, que no conoce esta vista: no sale aquí.");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<ScopeLine scope={{ kind: "all", servers: [WEB_01, WEB_02_STALE, VPS_03_OLD, NAS_01_DOWN] }} />);
    await expectNoAxeViolations(container);
  });
});
