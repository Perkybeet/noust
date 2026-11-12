import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Watching } from "../../api/queries/watching";
import { WATCHING, WATCHING_NO_CHANNEL, WATCHING_NO_MONITOR, WATCHING_OFF } from "../../dev/avisos/fixtures";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, onNode, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

// The Alerts page draws charts elsewhere; this page draws none, but its sibling routes import uPlot.
vi.mock("uplot", () => ({ default: vi.fn(function () { return { destroy: vi.fn(), setData: vi.fn(), setScale: vi.fn(), setSize: vi.fn(), setCursor: vi.fn(), redraw: vi.fn(), bbox: { left: 0, top: 0, width: 400, height: 140 }, valToPos: () => 0 }; }) }));

function routes(watching: Watching, extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/alerts/watching": () => json(200, watching),
    "GET /api/alerts/rules": () => json(200, { items: [], metrics: [], firing: 0 }),
    "GET /api/config": () => json(200, { config: { notifications: { enabled: true }, monitor: { domain_expiry: false } }, path: "/etc/noust/config.yaml", writable: true }),
    ...extra,
  };
}

describe("the opening of Alerts: what Noust watches", () => {
  it("answers first what Noust warns of today and by which channels, before the rules", { timeout: 40_000 }, async () => {
    fakeBackend(routes(WATCHING));
    renderConsole("/alerts");
    const section = (await screen.findByRole("heading", { level: 2, name: "What Noust watches for you" })).closest("section");
    if (section === null) throw new Error("the section is missing");
    // Where it goes is said once, in the head.
    expect(within(section).getByText("It reaches you by telegram and email.")).toBeInTheDocument();
    // What is watched is a list that says what each looks at, with its number from the configuration.
    const list = within(section).getByRole("list", { name: "What Noust watches" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(11);
    expect(within(list).getByText("An application stops")).toBeInTheDocument();
    expect(within(list).getByText("It passes 90% of its space.")).toBeInTheDocument();
    // What is off by the operator's own switch is said, and what is on says it too.
    expect(within(list).getByText("Off")).toBeInTheDocument();
    expect(within(list).getAllByText("Watching")).toHaveLength(10);
    // The rules come after it, as they always did.
    const rules = await screen.findByText("No alert rules yet");
    expect(section.compareDocumentPosition(rules) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The same channels for every row are not repeated on every row.
    expect(within(list).queryByText(/Not sent to/)).not.toBeInTheDocument();
  });

  it("offers what ships off with what it asks and of whom, and turns it on in one press", { timeout: 40_000 }, async () => {
    let on = false;
    const backend = fakeBackend(
      routes(WATCHING, {
        "GET /api/alerts/watching": () =>
          json(200, { ...WATCHING, watches: (WATCHING.watches ?? []).map((entry) => (entry.id === "domain_expiry" && on ? { ...entry, state: "on", offer: null } : entry)) }),
        "PATCH /api/config": () => {
          on = true;
          return json(200, { ok: true });
        },
      }),
    );
    const { user } = renderConsole("/alerts");
    const offer = await screen.findByText("A domain registration is about to lapse");
    expect(screen.getByText(/What leaves this server is the domain name, to its registry\./)).toBeInTheDocument();
    expect(screen.getByText("It asks somebody else, so it is off until you say so.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Turn on: A domain registration is about to lapse" }));
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/config")).toHaveLength(1);
    });
    expect(backend.callsTo("PATCH /api/config")[0]?.body).toEqual({ path: "monitor.domain_expiry", value: true });
    // Turned on, it joins the list and the offer is gone.
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /^Turn on: / })).not.toBeInTheDocument();
    });
    const list = screen.getByRole("list", { name: "What Noust watches" });
    expect(within(list).getByText("A domain registration is about to lapse")).toBeInTheDocument();
    expect(offer).toBeDefined();
  });

  it("says by no channel yet, with the way to add one, when nothing can take it", { timeout: 40_000 }, async () => {
    fakeBackend(routes(WATCHING_NO_CHANNEL));
    renderConsole("/alerts");
    expect(await screen.findByText("It reaches you by no channel yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Notification channels" })).toHaveAttribute("href", "/settings/notifications");
  });

  it("says nothing leaves the server while notifications are off", { timeout: 40_000 }, async () => {
    fakeBackend(routes(WATCHING_OFF));
    renderConsole("/alerts");
    expect(await screen.findByText("Nothing it finds will reach you")).toBeInTheDocument();
    expect(screen.getByText("Notifications are off, so Noust watches and nothing leaves the server.")).toBeInTheDocument();
  });

  it("says first that the monitor is not running, and a channel that hears less than the rest", { timeout: 40_000 }, async () => {
    fakeBackend(routes(WATCHING_NO_MONITOR));
    renderConsole("/alerts");
    expect(await screen.findByText("The monitor is not running")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the monitor" })).toHaveAttribute("href", "/server/monitor");
    expect(screen.getByText("pager does not hear everything: a filter, a severity floor or quiet hours narrow it.")).toBeInTheDocument();
    // The rows the pager does not take say so, and the others stay quiet.
    expect(screen.getAllByText("Not sent to pager").length).toBeGreaterThan(0);
  });

  it("reads in Spanish with no accessibility violations", { timeout: 40_000 }, async () => {
    await act(() => setLocale("es"));
    fakeBackend(routes(WATCHING_NO_MONITOR));
    renderConsole("/alerts");
    expect(await screen.findByRole("heading", { level: 2, name: "Lo que Noust vigila por ti" })).toBeInTheDocument();
    expect(screen.getByText("Una aplicación deja de funcionar")).toBeInTheDocument();
    expect(screen.getByText("El monitor no está en marcha")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says a server before 4.3 cannot say it, instead of failing", { timeout: 40_000 }, async () => {
    const backend = fakeBackend({
      ...signedInRoutes(),
      "GET /api/nodes": () => json(200, { nodes: [{ name: "web-2", status: "reachable", version: "4.2.1" }], total: 1 }),
      ...onNode("web-2", {
        "GET /api/openapi.json": () => json(200, { paths: { "/api/alerts/rules": { get: {} } } }),
        "GET /api/alerts/rules": () => json(200, { items: [], metrics: [], firing: 0 }),
        "GET /api/config": () => json(200, { config: { notifications: { enabled: true } }, path: "/etc/noust/config.yaml", writable: true }),
      }),
    });
    renderConsole("/n/web-2/alerts");
    expect(await screen.findByText("This server runs a Noust that cannot say what it watches yet.")).toBeInTheDocument();
    expect(backend.callsTo("GET /api/alerts/watching")).toHaveLength(0);
    // The rules of that server are still its own page's.
    expect(await screen.findByText("No alert rules yet")).toBeInTheDocument();
  });
});
