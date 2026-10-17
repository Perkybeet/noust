import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { readingText } from "./FleetMetricsCard";
import { METRICS_VIEW, fleetRoutes } from "./testFixtures";

function card(): HTMLElement {
  const heading = screen.getByRole("heading", { name: "Compare servers", level: 2 });
  const section = heading.closest("section");
  if (section === null) throw new Error("no card");
  return section;
}

async function summary(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({ ...fleetRoutes(), ...extra });
  const harness = renderConsole("/fleet");
  await screen.findByRole("heading", { level: 1, name: "Fleet" });
  return { ...harness, backend };
}

describe("comparing the servers of a fleet", () => {
  it("puts one metric of every server side by side, the busiest first, and says how many did not answer", { timeout: 30_000 }, async () => {
    const { backend } = await summary();
    const table = await screen.findByRole("list", { name: "CPU of every server, last 24 hours" });
    const items = within(table).getAllByRole("listitem");
    // web-01 is at 72.5%, web-2 at 7.5%: the busiest leads.
    expect(items[0]).toHaveTextContent("web-01");
    expect(items[0]).toHaveTextContent("73%");
    expect(items[1]).toHaveTextContent("web-2");
    expect(items[1]).toHaveTextContent("7.5%");
    expect(within(card()).getByText("2 servers did not answer, so they are missing here.")).toBeInTheDocument();
    const call = backend.callsTo("GET /api/fleet/metrics")[0];
    expect(call?.search.get("metric")).toBe("cpu");
    expect(call?.search.get("window")).toBe("24h");
  });

  it("asks for another measurement and another window when told", { timeout: 40_000 }, async () => {
    const { user, backend } = await summary();
    await screen.findByRole("list", { name: "CPU of every server, last 24 hours" });
    await user.click(within(card()).getByRole("combobox", { name: "What to compare" }));
    await user.click(await screen.findByRole("option", { name: "Memory" }));
    await waitFor(() => {
      expect(backend.callsTo("GET /api/fleet/metrics").some((call) => call.search.get("metric") === "memory")).toBe(true);
    });
    await user.click(within(card()).getByRole("radio", { name: "7 days" }));
    await waitFor(() => {
      expect(backend.callsTo("GET /api/fleet/metrics").some((call) => call.search.get("window") === "7d")).toBe(true);
    });
  });

  it("says when no server recorded it, and when the comparison could not be made", { timeout: 40_000 }, async () => {
    await summary({ "GET /api/fleet/metrics": () => json(200, { ...METRICS_VIEW, items: [] }) });
    expect(await within(await screen.findByRole("heading", { name: "Compare servers", level: 2 }).then(() => card())).findByText("No server recorded this in the window.")).toBeInTheDocument();
  });

  it("shows the central's refusal in its words when it cannot compare", { timeout: 30_000 }, async () => {
    await summary({
      "GET /api/fleet/metrics": () => json(400, { error: "validation_error", detail: "metric must be one of: cpu, memory", hint: null, fields: null, output: null }),
    });
    expect(await screen.findByText("Could not compare the servers")).toBeInTheDocument();
    expect(screen.getByText("metric must be one of: cpu, memory")).toBeInTheDocument();
  });

  it("reads in Spanish, with no accessibility violations", { timeout: 30_000 }, async () => {
    await act(async () => {
      await setLocale("es");
    });
    fakeBackend(fleetRoutes());
    const { container } = renderConsole("/fleet");
    expect(await screen.findByRole("heading", { name: "Comparar servidores", level: 2 })).toBeInTheDocument();
    expect(await screen.findByRole("list", { name: "CPU de todos los servidores, últimas 24 horas" })).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });
});

describe("a reading, in the unit the server said", () => {
  it("writes a share, a rate and a plain number, and nothing for no reading", () => {
    expect(readingText(72.5, "percent", "en")).toBe("73%");
    expect(readingText(2048, "bytes_s", "en")).toBe("2.0 KB/s");
    expect(readingText(0.4567, "number", "en")).toBe("0.46");
    expect(readingText(null, "percent", "en")).toBeNull();
  });
});
