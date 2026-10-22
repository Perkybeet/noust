import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { FakeWebSocket, fakeBackend, json } from "../../../test/fakes";
import { TAB_DOMAIN, appRoutes } from "../testRoutes";
import { journalLine } from "./journalLines";
import { siteLineLevel } from "./LogsTab";

/** What the API answers for a site's log: the end of the file, already masked. */
function siteLog(kind: string, lines: string[], extra: Record<string, unknown> = {}) {
  return { domain: TAB_DOMAIN, kind, path: `/var/log/nginx/${TAB_DOMAIN}.${kind}.log`, available: true, lines, size_bytes: 4096, truncated: false, matched: lines.length, ...extra };
}

const ACCESS = [
  '203.0.113.7 - - [25/Sep/2026:21:45:52 +0100] "GET / HTTP/1.1" 200 512 "-" "curl/8.5"',
  '203.0.113.7 - - [25/Sep/2026:21:45:53 +0100] "GET /missing HTTP/1.1" 404 153 "-" "curl/8.5"',
  '203.0.113.7 - - [25/Sep/2026:21:45:54 +0100] "GET /api HTTP/1.1" 502 157 "-" "curl/8.5"',
];

async function logsAt(app: Record<string, unknown> = {}, routes: Parameters<typeof fakeBackend>[0] = {}) {
  vi.stubGlobal("WebSocket", FakeWebSocket);
  // The virtualizer sizes its window from offsetHeight, which jsdom reports as 0.
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
  const backend = fakeBackend(appRoutes(app, { "POST /api/auth/ws-ticket": () => json(200, { ticket: "t1" }), ...routes }));
  const harness = renderConsole(`/apps/${TAB_DOMAIN}/logs`);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  return { ...harness, backend };
}

async function socket(): Promise<FakeWebSocket> {
  await waitFor(() => {
    expect(FakeWebSocket.instances.some((candidate) => candidate.url.includes(`/ws/logs/${TAB_DOMAIN}`))).toBe(true);
  });
  const found = FakeWebSocket.instances.find((candidate) => candidate.url.includes(`/ws/logs/${TAB_DOMAIN}`));
  if (!found) throw new Error("no log socket");
  return found;
}

describe("a web server log line", () => {
  it("reads the level of a request from its status, and of an error from its own word", () => {
    expect(siteLineLevel("access", ACCESS[0] ?? "")).toBeUndefined();
    expect(siteLineLevel("access", ACCESS[1] ?? "")).toBe("warn");
    expect(siteLineLevel("access", ACCESS[2] ?? "")).toBe("error");
    expect(siteLineLevel("access", "not a request line")).toBeUndefined();
    expect(siteLineLevel("error", "2026/09/25 21:45:54 [error] 41#41: *7 connect() failed (111: Connection refused)")).toBe("error");
    expect(siteLineLevel("error", "2026/09/25 21:45:54 [notice] 41#41: signal process started")).toBeUndefined();
  });
});

describe("a journal line", () => {
  it("moves systemd's time to the time column, drops the host, and keeps the rest verbatim", () => {
    expect(journalLine({ id: 1, text: "2026-09-25T21:45:52+0100 web-01 shop-example-com[41234]: GET / 200 in 38ms" })).toEqual({
      id: 1,
      ts: "21:45:52",
      text: "shop-example-com[41234]: GET / 200 in 38ms",
    });
  });

  it("leaves a line in another form as it is", () => {
    const line = { id: 2, text: "-- Boot 4f1c --" };
    expect(journalLine(line)).toBe(line);
  });
});

// Whole-console renders: generous under a loaded machine or a slow CI runner.
describe("the logs tab", { timeout: 20_000 }, () => {
  it("follows the journal live, marks failures, and says when it is reconnecting", async () => {
    await logsAt();
    const ws = await socket();
    expect(ws.url).toContain("lines=200");
    act(() => {
      ws.open();
      ws.frame({ type: "connected", domain: TAB_DOMAIN, service: "shop-example-com" });
      ws.frame({ type: "log", data: "2026-09-25T21:45:52+0100 web-01 shop-example-com[41234]: GET / 200 in 38ms" });
      ws.frame({ type: "log", data: "2026-09-25T21:45:53+0100 web-01 shop-example-com[41234]: Error: connect ECONNREFUSED 127.0.0.1:6379" });
    });
    const journal = await screen.findByRole("region", { name: `Logs of ${TAB_DOMAIN}` });
    expect(await within(journal).findByText(/GET \/ 200 in 38ms/)).toBeInTheDocument();
    expect(within(journal).getByText(/ECONNREFUSED/).closest("[data-index]")).toHaveClass("bg-fail-soft");
    expect(screen.getByText("2 lines")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    // Live is the range: the tab opens on it.
    expect(screen.getByRole("combobox", { name: "How far back" })).toHaveTextContent("Live");

    act(() => {
      ws.drop();
    });
    expect(await screen.findByText("Reconnecting")).toBeInTheDocument();
  });

  it("shows the stream's own error verbatim", async () => {
    await logsAt();
    const ws = await socket();
    act(() => {
      ws.open();
      ws.frame({ type: "error", message: "journalctl not found. Log streaming requires systemd." });
    });
    expect(await screen.findByText("journalctl not found. Log streaming requires systemd.")).toBeInTheDocument();
    expect(screen.getByText("The log stream failed")).toBeInTheDocument();
  });

  it("narrows the lines to a level with the viewer's chips", async () => {
    const { user } = await logsAt();
    const ws = await socket();
    act(() => {
      ws.open();
      ws.frame({ type: "log", data: "2026-09-25T21:45:52+0100 web-01 shop-example-com[41234]: GET / 200 in 38ms" });
      ws.frame({ type: "log", data: "2026-09-25T21:45:53+0100 web-01 shop-example-com[41234]: Error: connect ECONNREFUSED 127.0.0.1:6379" });
    });
    const journal = await screen.findByRole("region", { name: `Logs of ${TAB_DOMAIN}` });
    await within(journal).findByText(/GET \/ 200 in 38ms/);
    await user.click(screen.getByRole("button", { name: "Error 1" }));
    expect(within(journal).queryByText(/GET \/ 200 in 38ms/)).not.toBeInTheDocument();
    expect(within(journal).getByText(/ECONNREFUSED/)).toBeInTheDocument();
    // The count is the stream's, not the filter's: the viewer says what it hides.
    expect(screen.getByText("2 lines")).toBeInTheDocument();
  });

  it("lets `/` search the journal, with the query box every log surface has", async () => {
    const { user } = await logsAt();
    await socket();
    const search = await screen.findByRole("searchbox", { name: "Search the logs" });
    expect(search).toHaveAttribute("data-page-search");
    await user.keyboard("/");
    expect(search).toHaveFocus();
    // The viewer has no box of its own: one place to ask.
    expect(screen.queryByRole("searchbox", { name: "Search output" })).not.toBeInTheDocument();
  });

  it("keeps the lines of the stream that match what is typed, and writes the query back as it was understood", async () => {
    const { user, location } = await logsAt();
    const ws = await socket();
    act(() => {
      ws.open();
      ws.frame({ type: "log", data: "2026-09-25T21:45:52+0100 web-01 shop-example-com[41234]: GET / 200 in 38ms" });
      ws.frame({ type: "log", data: "2026-09-25T21:45:53+0100 web-01 shop-example-com[41234]: Error: connect ECONNREFUSED 127.0.0.1:6379" });
    });
    const journal = await screen.findByRole("region", { name: `Logs of ${TAB_DOMAIN}` });
    await within(journal).findByText(/GET \/ 200 in 38ms/);

    await user.type(screen.getByRole("searchbox", { name: "Search the logs" }), "econnrefused -timeout");
    await waitFor(() => expect(within(journal).queryByText(/GET \/ 200 in 38ms/)).not.toBeInTheDocument());
    expect(within(journal).getByText(/ECONNREFUSED/)).toBeInTheDocument();
    expect(screen.getByText("1 line")).toBeInTheDocument();
    expect(screen.getByText("Understood as")).toBeInTheDocument();
    expect(screen.getByText("econnrefused AND NOT timeout")).toBeInTheDocument();
    expect(location().search).toEqual({ q: "econnrefused -timeout" });
  });

  it("says why a query cannot be read, in the grammar's words, and keeps every line meanwhile", async () => {
    const { user } = await logsAt();
    const ws = await socket();
    act(() => {
      ws.open();
      ws.frame({ type: "log", data: "2026-09-25T21:45:52+0100 web-01 shop-example-com[41234]: GET / 200 in 38ms" });
    });
    const journal = await screen.findByRole("region", { name: `Logs of ${TAB_DOMAIN}` });
    await within(journal).findByText(/GET \/ 200 in 38ms/);
    await user.type(screen.getByRole("searchbox", { name: "Search the logs" }), "(status");
    expect(await screen.findByText(/A parenthesis is not closed/)).toBeInTheDocument();
    expect(within(journal).getByText(/GET \/ 200 in 38ms/)).toBeInTheDocument();
  });

  it("reads a window of the past from the server, bound to this application, and writes the query back", async () => {
    const { user, backend, location } = await logsAt(
      {},
      {
        "GET /api/logs/search": () =>
          json(200, {
            lines: [
              { ts: 1_790_000_030, app: TAB_DOMAIN, source: "journal", unit: "shop-example-com.service", process: "node", level: "err", message: "connection refused: db:5432" },
              { ts: 1_790_000_000, app: TAB_DOMAIN, source: "journal", unit: "shop-example-com.service", process: "node", level: "info", message: "listening on :3000" },
            ],
            matched: 2,
            scanned: 120,
            truncated: false,
            since: 1_789_900_000,
            until: 1_790_000_100,
            notes: [],
            apps: [TAB_DOMAIN],
            understood: "",
          }),
      },
    );
    await socket();
    await user.click(screen.getByRole("combobox", { name: "How far back" }));
    await user.click(await screen.findByRole("option", { name: "24 hours" }));

    const journal = await screen.findByRole("region", { name: `Logs of ${TAB_DOMAIN}` });
    expect(await within(journal).findByText(/connection refused: db:5432/)).toBeInTheDocument();
    const asked = backend.callsTo("GET /api/logs/search").at(-1);
    expect(asked?.search.getAll("app")).toEqual([TAB_DOMAIN]);
    expect(asked?.search.get("since")).toBe("24h");
    expect(location().search).toEqual({ since: "24h" });
    expect(screen.getByText("2 of 2 matching lines shown, out of 120 examined.")).toBeInTheDocument();
    expect(screen.getByText("noust log search --app shop.example.com --since 24h")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("reads everything since the last deploy started, and marks where it started", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
    const backend = fakeBackend(
      appRoutes(
        {},
        {
          "GET /api/deployments": () =>
            json(200, {
              items: [{ id: 42, domain: TAB_DOMAIN, status: "success", started_at: "2026-09-26T08:00:00Z", triggered_by: "panel", cancelled: false, has_log: true, rollback_available: false, schema_changed: false }],
              total: 1,
              next_before_id: null,
            }),
          "GET /api/logs/search": () =>
            json(200, {
              lines: [{ ts: 1_790_000_000, app: TAB_DOMAIN, source: "journal", unit: "shop-example-com.service", process: "node", level: "info", message: "listening on :3000" }],
              matched: 1,
              scanned: 10,
              truncated: false,
              since: 1_789_900_000,
              until: 1_790_000_100,
              notes: [],
              apps: [TAB_DOMAIN],
              understood: "",
            }),
        },
      ),
    );
    // Opened from the address, as the deployment page's link does.
    renderConsole(`/apps/${TAB_DOMAIN}/logs?since=deploy`);
    const journal = await screen.findByRole("region", { name: `Logs of ${TAB_DOMAIN}` });
    expect(await within(journal).findByText(/Deploy #42 started here/)).toBeInTheDocument();
    expect(within(journal).getByText(/listening on :3000/)).toBeInTheDocument();
    expect(backend.callsTo("GET /api/logs/search").at(-1)?.search.get("since")).toBe(String(Date.parse("2026-09-26T08:00:00Z") / 1000));
    expect(screen.getByRole("combobox", { name: "How far back" })).toHaveTextContent("Since the last deploy");
  });

  it("says so when there is no deploy to read from", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    fakeBackend(appRoutes({}, { "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }) }));
    renderConsole(`/apps/${TAB_DOMAIN}/logs?since=deploy`);
    expect(await screen.findByText("This application has not been deployed yet, so there is no deploy to read from.")).toBeInTheDocument();
  });

  it("opens on the source the address names, and hides the range where a file has only an end", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    fakeBackend(appRoutes({}, { [`GET /api/apps/${TAB_DOMAIN}/site-logs`]: () => json(200, siteLog("error", ["2026/09/25 21:45:54 [error] 41#41: *7 connect() failed"])) }));
    renderConsole(`/apps/${TAB_DOMAIN}/logs?source=error`);
    expect(await screen.findByRole("radio", { name: "Web server errors" })).toBeChecked();
    expect(await screen.findByRole("region", { name: `Web server error log of ${TAB_DOMAIN}` })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "How far back" })).not.toBeInTheDocument();
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("filters the end of a web server's log with the same query", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
    fakeBackend(appRoutes({}, { [`GET /api/apps/${TAB_DOMAIN}/site-logs`]: () => json(200, siteLog("access", ACCESS)) }));
    renderConsole(`/apps/${TAB_DOMAIN}/logs?source=access&q=missing`);
    const log = await screen.findByRole("region", { name: `Web server access log of ${TAB_DOMAIN}` });
    expect(await within(log).findByText(/GET \/missing/)).toBeInTheDocument();
    expect(within(log).queryByText(/GET \/api /)).not.toBeInTheDocument();
    expect(screen.getByText("1 line")).toBeInTheDocument();
  });

  it("does not open a stream for a static site, which has no unit, and shows its web server's access log instead", async () => {
    const { backend } = await logsAt({ status: "static", active: false, port: null }, { [`GET /api/apps/${TAB_DOMAIN}/site-logs`]: () => json(200, siteLog("access", ACCESS)) });
    expect(await screen.findByText("A static site has no process to log")).toBeInTheDocument();
    const log = await screen.findByRole("region", { name: `Web server access log of ${TAB_DOMAIN}` });
    expect(await within(log).findByText(/"GET \/api HTTP\/1.1" 502/)).toBeInTheDocument();
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(backend.callsTo(`GET /api/apps/${TAB_DOMAIN}/site-logs`)[0]?.search.get("kind")).toBe("access");
    // The journal is not offered where there is none.
    expect(screen.queryByRole("radio", { name: "Application" })).toBeNull();
  });

  it("switches an application's logs between its journal, its requests and the web server's errors", async () => {
    const { user, backend } = await logsAt(
      {},
      {
        [`GET /api/apps/${TAB_DOMAIN}/site-logs`]: (call) =>
          json(200, call.search.get("kind") === "error" ? siteLog("error", ["2026/09/25 21:45:54 [error] 41#41: *7 connect() failed (111: Connection refused)"]) : siteLog("access", ACCESS)),
      },
    );
    await socket();
    await user.click(screen.getByRole("radio", { name: "Web server requests" }));
    const access = await screen.findByRole("region", { name: `Web server access log of ${TAB_DOMAIN}` });
    // A server error is marked like a failure, a client error like a warning.
    expect(within(access).getByText(/ 502 157/).closest("[data-index]")).toHaveClass("bg-fail-soft");
    expect(screen.getByText("3 lines")).toBeInTheDocument();
    // The viewer's chips say what an access log's levels are: a 5xx is a server error, a 4xx a client error.
    expect(screen.getByRole("button", { name: "Client errors 1" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Server errors 1" }));
    expect(within(access).queryByText(/ 200 512/)).not.toBeInTheDocument();
    expect(within(access).getByText(/ 502 157/)).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "Web server errors" }));
    const errors = await screen.findByRole("region", { name: `Web server error log of ${TAB_DOMAIN}` });
    expect(await within(errors).findByText(/Connection refused/)).toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${TAB_DOMAIN}/site-logs`).some((call) => call.search.get("kind") === "error")).toBe(true);
  });

  it("says where a log will appear when the web server has not written it yet", async () => {
    const { user } = await logsAt(
      {},
      { [`GET /api/apps/${TAB_DOMAIN}/site-logs`]: () => json(200, siteLog("access", [], { available: false, size_bytes: 0 })) },
    );
    await socket();
    await user.click(screen.getByRole("radio", { name: "Web server requests" }));
    expect(await screen.findByText("The web server has not written this log yet")).toBeInTheDocument();
    expect(screen.getByText(`/var/log/nginx/${TAB_DOMAIN}.access.log`)).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    await logsAt();
    const ws = await socket();
    act(() => {
      ws.open();
      ws.frame({ type: "log", data: "2026-09-25T21:45:52+0100 web-01 shop-example-com[41234]: Ready in 412ms" });
    });
    await screen.findByText(/Ready in 412ms/);
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("follows the journal live in Spanish", async () => {
    await act(() => setLocale("es"));
    await logsAt();
    const ws = await socket();
    act(() => {
      ws.open();
      ws.frame({ type: "connected", domain: TAB_DOMAIN, service: "shop-example-com" });
      ws.frame({ type: "log", data: "2026-09-25T21:45:52+0100 web-01 shop-example-com[41234]: GET / 200 in 38ms" });
    });
    const journal = await screen.findByRole("region", { name: `Registros de ${TAB_DOMAIN}` });
    expect(await within(journal).findByText(/GET \/ 200 in 38ms/)).toBeInTheDocument();
    expect(screen.getByText("Conectado")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Hasta cuándo atrás" })).toHaveTextContent("En directo");
    act(() => {
      ws.drop();
    });
    expect(await screen.findByText("Reconectando")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
