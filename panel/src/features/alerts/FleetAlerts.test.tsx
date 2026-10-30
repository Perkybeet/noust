import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
import { centralSession, fleetRoutes } from "../fleet/testFixtures";
import { AUDIT_REFUSAL, REFUSED_NODES, envelope, row } from "../operation/testFixtures";

const NOW = Math.floor(Date.now() / 1000);

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

function rule(extra: Record<string, unknown>) {
  return {
    metric: "cpu",
    scope: "server",
    unit: "percent",
    comparator: ">",
    threshold: 90,
    app_domain: null,
    duration_s: 300,
    severity: "warning",
    enabled: true,
    states: [],
    firing: false,
    ...extra,
  };
}

/** web-2's disk fires and nobody took it; web-01's CPU rule is calm; db-1's is switched off. */
const ITEMS = [
  row("web-2", "/alerts", {
    ...rule({ name: "disk-almost-full", metric: "disk", threshold: 85, firing: true, severity: "failed" }),
    states: [{ subject: "", state: "firing", since: NOW - 3600, last_value: 91, announced: true, acknowledged_by: null, acknowledged_at: null }],
  }),
  row("web-2", "/alerts", {
    ...rule({ name: "shop-memory", metric: "app_memory", scope: "app", unit: "bytes", app_domain: "shop.example.com", threshold: 536_870_912, firing: true }),
    states: [{ subject: "shop.example.com", state: "firing", since: NOW - 600, last_value: 620_000_000, announced: true, acknowledged_by: "fleet-web-01 on behalf of marta", acknowledged_at: NOW - 60 }],
  }),
  row("web-01", "/alerts", rule({ name: "cpu-saturated" })),
  row("db-1", "/alerts", rule({ name: "cpu-db", enabled: false })),
];

async function alertsAt(answer: (call: RecordedCall) => Response, path = "/fleet/alerts") {
  const backend = fakeBackend({ ...fleetRoutes(centralSession()), "GET /api/views/alerts": answer });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: /^(Alerts|Alertas)$/ });
  return { ...harness, backend };
}

describe("alerts on every server", () => {
  it("lists the rules of each server, the ones firing first, each naming its server and what fires in it", async () => {
    await alertsAt(() => json(200, envelope("alerts", ITEMS)));
    const table = await screen.findByRole("region", { name: "Alert rules of every server" });
    const rows = await within(table).findAllByRole("row");
    // The header, then the rows in the order the central gave them: firing first.
    expect(rows[1]).toHaveTextContent("disk-almost-full");
    expect(rows[1]).toHaveTextContent("web-2");
    expect(rows[1]).toHaveTextContent("Disk above 85% for 5m 00s");
    expect(rows[1]).toHaveTextContent("The server reads 91%, firing");
    // Somebody has acknowledged the other one, and the person is named first, in the console's language.
    expect(rows[2]).toHaveTextContent("shop.example.com reads 591 MB, firing");
    expect(rows[2]).toHaveTextContent("Acknowledged by marta (from the central web-01)");
    expect(within(table).getByText("Normal")).toBeInTheDocument();
    expect(within(table).getByText("Off")).toBeInTheDocument();
    expect(screen.getByText("2 alerts are firing")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("opens a rule on the server it lives on, where it is edited and taken", async () => {
    await alertsAt(() => json(200, envelope("alerts", ITEMS)));
    const link = await screen.findByRole("link", { name: "disk-almost-full" });
    expect(link).toHaveAttribute("href", "/n/web-2/alerts");
  });

  it("narrows to what is firing, and to one server by asking the central for it", async () => {
    const asked: string[][] = [];
    const { user } = await alertsAt((call) => {
      asked.push(call.search.getAll("node"));
      return json(200, envelope("alerts", ITEMS));
    });
    await screen.findByRole("link", { name: "disk-almost-full" });
    await user.click(screen.getByRole("combobox", { name: "State" }));
    await user.click(await screen.findByRole("option", { name: "Off" }));
    await waitFor(() => {
      expect(screen.queryByRole("link", { name: "disk-almost-full" })).not.toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "cpu-db" })).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Servers" }));
    await user.click(await screen.findByRole("option", { name: "web-2" }));
    await waitFor(() => {
      expect(asked).toContainEqual(["web-2"]);
    });
  });

  it("says once which servers do not let the operator read their alerts", async () => {
    await alertsAt(() => json(200, envelope("alerts", ITEMS.slice(0, 1), { nodes: REFUSED_NODES, partial: true, refusals: [{ ...AUDIT_REFUSAL, resource: "alerts", permission: "server.read" }] })));
    expect(await screen.findByText("2 servers do not let you see their alerts")).toBeInTheDocument();
    expect(screen.getByText("db-1")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('"error":"permission_denied"');
  });

  it("reads in Spanish", async () => {
    await act(() => setLocale("es"));
    await alertsAt(() => json(200, envelope("alerts", ITEMS)));
    const table = await screen.findByRole("region", { name: "Reglas de alerta de todos los servidores" });
    expect(await within(table).findByText("Normal")).toBeInTheDocument();
    expect(screen.getByText("2 alertas están activas")).toBeInTheDocument();
    expect(table).toHaveTextContent("Reconocida por marta (desde la central web-01)");
  });
});
