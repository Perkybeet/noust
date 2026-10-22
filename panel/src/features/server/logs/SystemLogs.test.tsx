/**
 * Logs, from the system (checklist M-40): what is typed reaches the server as the query, the
 * server's reading of it is shown, a query it cannot read is shown in its own words, a line opens
 * its context (and a link can point at one), the search says when it did not look at everything,
 * "Live" asks again, and it joins the timeline of a stretch.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { onDesktop, serverRoutes } from "../testing";

beforeEach(() => {
  onDesktop();
  // The viewer shows only the rows its scroller has room for, and jsdom reports no room.
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
});

const ENTRIES = [
  { timestamp: "2026-09-29T09:40:00+00:00", priority: 6, unit: "nginx.service", message: "GET / 200", pid: 1021, cursor: "c1" },
  { timestamp: "2026-09-29T09:41:00+00:00", priority: 3, unit: "nginx.service", message: "connect() failed (111: Connection refused)", pid: 1021, cursor: "c2" },
  { timestamp: "2026-09-29T09:42:00+00:00", priority: 4, unit: "ssh.service", message: "Invalid user admin", pid: 41110, cursor: "c3" },
];

function search(overrides: Record<string, unknown> = {}) {
  return {
    entries: ENTRIES,
    matched: 3,
    scanned: 40,
    truncated: false,
    scan_limit_reached: false,
    oldest: "2026-09-29T08:00:00+00:00",
    normalised: "",
    ...overrides,
  };
}

function logs(path: string, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    ...serverRoutes(),
    "GET /api/server/logs/search": () => json(200, search()),
    ...extra,
  });
  const harness = renderConsole(path);
  return { ...harness, backend };
}

const SYSTEM = "/logs?source=system";
const SEARCH = "Search the logs";

describe("Logs from the system", () => {
  it("sends what is typed as the query, and says it as the server understood it", async () => {
    const { user, backend } = logs(SYSTEM, {
      "GET /api/server/logs/search": (call) => json(200, search({ normalised: call.search.get("query") === "refused" ? "refused" : "" })),
    });
    const box = await screen.findByRole("searchbox", { name: SEARCH });
    await user.type(box, "refused");

    await waitFor(() => expect(backend.callsTo("GET /api/server/logs/search").at(-1)?.search.get("query")).toBe("refused"));
    expect(await screen.findByText("Understood as")).toBeInTheDocument();
    expect(screen.getByText("3 of 40 entries match", { exact: false })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is one of the two sources of the page that searches the applications", async () => {
    logs(SYSTEM);
    expect(await screen.findByRole("heading", { level: 1, name: "Logs" })).toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: "System" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Applications" })).not.toBeChecked();
    expect(await screen.findByText("noust server logs --since -1h")).toBeInTheDocument();
  });

  it("puts the pickers into the query as fields", async () => {
    const { backend } = logs("/logs?source=system&unit=nginx.service&level=err&q=timeout");
    await waitFor(() => expect(backend.callsTo("GET /api/server/logs/search")).not.toHaveLength(0));
    expect(backend.callsTo("GET /api/server/logs/search").at(-1)?.search.get("query")).toBe("@unit:nginx.service @level:err timeout");
  });

  it("asks for the range chosen, and Live asks for the newest lines", async () => {
    const { user, backend, location } = logs(SYSTEM);
    await waitFor(() => expect(backend.callsTo("GET /api/server/logs/search").at(-1)?.search.get("since")).toBe("-1h"));

    await user.click(await screen.findByRole("combobox", { name: "How far back" }));
    // The lines of the viewer are options too: the ranges are told by their names.
    for (const name of ["Live", "15 minutes", "1 hour", "6 hours", "24 hours", "7 days", "This boot", "Previous boot"]) {
      expect(await screen.findByRole("option", { name })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("option", { name: "Live" }));
    await waitFor(() => expect(backend.callsTo("GET /api/server/logs/search").at(-1)?.search.get("since")).toBe("-15min"));
    expect(location().search).toEqual({ source: "system", since: "live" });

    await user.click(screen.getByRole("combobox", { name: "How far back" }));
    await user.click(await screen.findByRole("option", { name: "This boot" }));
    await waitFor(() => expect(backend.callsTo("GET /api/server/logs/search").at(-1)?.search.get("boot")).toBe("0"));
  });

  it("has no switch of its own to follow: Live is a range, and Follow is the viewer's", async () => {
    logs(SYSTEM);
    await screen.findByRole("listbox", { name: "System logs" });
    expect(screen.queryByRole("switch", { name: "Live" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Follow" })).toHaveLength(1);
  });

  it("shows a query the server cannot read in the server's own words", async () => {
    logs("/logs?source=system&q=%28unclosed", {
      "GET /api/server/logs/search": () => problem(400, "validation_error", "A parenthesis is not closed (at character 1)", { hint: "Words, \"phrases\", @unit:..." }),
    });
    expect(await screen.findByText("Could not load the system logs")).toBeInTheDocument();
    expect(await screen.findByText(/A parenthesis is not closed \(at character 1\)/)).toBeInTheDocument();
  });

  it("explains the syntax on request", async () => {
    const { user } = logs(SYSTEM);
    await user.click(await screen.findByRole("button", { name: "Query syntax" }));
    const popover = await screen.findByRole("dialog");
    expect(within(popover).getByText("@unit:nginx")).toBeInTheDocument();
    expect(within(popover).getByText("error OR fatal")).toBeInTheDocument();
    await expectNoAxeViolations(popover);
  });

  it("opens the entries around a line, with the one chosen marked, can keep to its service, and puts the line in the address", async () => {
    const calls: URLSearchParams[] = [];
    const { user, location } = logs(SYSTEM, {
      "GET /api/server/logs/context": (call) => {
        calls.push(call.search);
        return json(200, {
          entries: [
            { timestamp: "2026-09-29T09:40:00+00:00", priority: 6, unit: "nginx.service", message: "GET / 200", pid: 1021, cursor: "c1" },
            ENTRIES[1],
            { timestamp: "2026-09-29T09:41:30+00:00", priority: 6, unit: "cron.service", message: "CMD (run-parts)", pid: 880, cursor: "c9" },
          ],
          target: 1,
        });
      },
    });
    const viewer = await screen.findByRole("listbox", { name: "System logs" });
    await user.click(await within(viewer).findByRole("option", { name: /Connection refused/ }));

    const drawer = await screen.findByRole("dialog", { name: "Around this entry" });
    expect(await within(drawer).findByText(/CMD \(run-parts\)/)).toBeInTheDocument();
    expect(calls[0]?.get("cursor")).toBe("c2");
    expect(calls[0]?.get("unit")).toBeNull();
    // The entry that was chosen is the one marked, among its neighbours.
    expect(within(drawer).getByText(/connect\(\) failed/).closest("[aria-current]")).toHaveAttribute("aria-current", "true");
    // A link to this line: the address names it.
    expect(location().search).toEqual({ source: "system", line: "c2" });
    await expectNoAxeViolations(drawer);

    await user.click(within(drawer).getByRole("checkbox", { name: "Only nginx.service" }));
    await waitFor(() => expect(calls.at(-1)?.get("unit")).toBe("nginx.service"));
  });

  it("opens a line a link points at, even though it names only its cursor", async () => {
    const calls: URLSearchParams[] = [];
    logs("/logs?source=system&line=c2", {
      "GET /api/server/logs/context": (call) => {
        calls.push(call.search);
        return json(200, { entries: [ENTRIES[0], ENTRIES[1]], target: 1 });
      },
    });
    const drawer = await screen.findByRole("dialog", { name: "Around this entry" });
    expect(await within(drawer).findByText(/connect\(\) failed/)).toBeInTheDocument();
    expect(calls[0]?.get("cursor")).toBe("c2");
    // The moment and the service are read from the entry the server found.
    expect(await within(drawer).findByText(/^What was logged just before and after 2026-09-29 09:41:00/)).toBeInTheDocument();
    expect(within(drawer).getByRole("checkbox", { name: "Only nginx.service" })).toBeInTheDocument();
  });

  it("closes the drawer by taking the line out of the address", async () => {
    const { user, location } = logs("/logs?source=system&line=c2", {
      "GET /api/server/logs/context": () => json(200, { entries: [ENTRIES[1]], target: 0 }),
    });
    const drawer = await screen.findByRole("dialog", { name: "Around this entry" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(location().search).toEqual({ source: "system" }));
    expect(drawer).not.toBeInTheDocument();
  });

  it("says so when the entry has left the journal", async () => {
    const { user } = logs(SYSTEM, {
      "GET /api/server/logs/context": () => json(200, { entries: [ENTRIES[0]], target: null }),
    });
    const viewer = await screen.findByRole("listbox", { name: "System logs" });
    await user.click(await within(viewer).findByRole("option", { name: /GET \/ 200/ }));
    expect(await screen.findByText("That entry is no longer in the journal; these are the nearest.")).toBeInTheDocument();
  });

  it("says when older entries were not looked at, and looks further on request", async () => {
    const { user, backend } = logs(SYSTEM, {
      "GET /api/server/logs/search": () => json(200, search({ scan_limit_reached: true, scanned: 5000 })),
    });
    expect(await screen.findByText("Older entries were not examined")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Look through 20000" }));
    await waitFor(() => expect(backend.callsTo("GET /api/server/logs/search").at(-1)?.search.get("scan")).toBe("20000"));
  });

  it("shows a stretch picked elsewhere in place of the range, and goes back to the last hour", async () => {
    const { user, backend } = logs("/logs?source=system&from=1790000000&to=1790000600&q=%40app%3Ashop.example.com");
    expect(await screen.findByText(/^Showing .* to .*\.$/)).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "How far back" })).not.toBeInTheDocument();
    await waitFor(() => expect(backend.callsTo("GET /api/server/logs/search").at(-1)?.search.get("since")).toBe("@1790000000"));
    expect(backend.callsTo("GET /api/server/logs/search").at(-1)?.search.get("until")).toBe("@1790000600");

    await user.click(screen.getByRole("button", { name: "Back to the last hour" }));
    await waitFor(() => expect(backend.callsTo("GET /api/server/logs/search").at(-1)?.search.get("since")).toBe("-1h"));
    expect(screen.getByRole("combobox", { name: "How far back" })).toHaveTextContent("1 hour");
  });

  it("opens what happened in the stretch it is looking at", async () => {
    const { user, backend } = logs(SYSTEM, {
      "GET /api/timeline": () => json(200, { start: 1, end: 2, events: [], processes: null, sources: [] }),
    });
    await user.click(await screen.findByRole("button", { name: "What happened here" }));
    expect(await screen.findByRole("dialog", { name: "What happened in this stretch" })).toBeInTheDocument();
    await waitFor(() => expect(backend.callsTo("GET /api/timeline")).toHaveLength(1));
  });

  it("is where the old address of the system's logs leads", async () => {
    const { backend, location } = logs("/server/logs?unit=nginx.service&priority=err&range=24h&q=timeout");
    await screen.findByRole("heading", { level: 1, name: "Logs" });
    await waitFor(() => expect(backend.callsTo("GET /api/server/logs/search")).not.toHaveLength(0));
    expect(location().pathname).toBe("/logs");
    expect(location().search).toEqual({ source: "system", unit: "nginx.service", level: "err", since: "24h", q: "timeout" });
    expect(backend.callsTo("GET /api/server/logs/search").at(-1)?.search.get("since")).toBe("-24h");
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    logs(SYSTEM);
    expect(await screen.findByRole("button", { name: "Sintaxis de la búsqueda" })).toBeInTheDocument();
    expect(await screen.findByText("3 de 40 entradas coinciden", { exact: false })).toBeInTheDocument();
  });
});
