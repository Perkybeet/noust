import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { fleetRoutes } from "../fleet/testFixtures";
import { screenWidth } from "../app/testRoutes";

const NOW = 1_790_000_000;

function line(extra: Record<string, unknown> = {}) {
  return { ts: NOW, app: "shop.example.com", source: "journal", unit: "shop-example-com.service", process: "node", level: "info", message: "listening on :3001", ...extra };
}

/** Newest first, as the server answers. */
const LINES = [
  line({ ts: NOW, level: "err", message: "connection refused: db:5432" }),
  line({ ts: NOW - 30, source: "container", unit: "web", app: "blog.example.com", level: null, message: "GET /health 200" }),
  line({ ts: NOW - 60, app: "", unit: "nginx.service", level: "warning", message: "upstream timed out" }),
];

function found(extra: Record<string, unknown> = {}) {
  return { lines: LINES, matched: 5, scanned: 340, truncated: false, since: NOW - 3600, until: NOW, notes: [], apps: ["shop.example.com", "blog.example.com"], understood: "", ...extra };
}

const APPS = {
  apps: [
    { domain: "shop.example.com", app_name: "shop-example-com", app_type: "node", port: 3001, status: "running", enabled: true },
    { domain: "blog.example.com", app_name: "blog-example-com", app_type: "static", port: 0, status: "running", enabled: true },
  ],
  total: 2,
};

async function pageAt(path = "/logs", extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/apps": () => json(200, APPS),
    "GET /api/logs/search": () => json(200, found()),
    ...extra,
  });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: "Logs" });
  return { ...harness, backend };
}

describe("Logs", () => {
  it("searches the last hour of every application, oldest line first, and says how much was examined", { timeout: 30_000 }, async () => {
    const { backend } = await pageAt();
    expect(await screen.findByText("3 of 5 matching lines shown, out of 340 examined.")).toBeInTheDocument();
    const call = backend.callsTo("GET /api/logs/search")[0];
    expect(call?.search.get("q")).toBe("");
    expect(call?.search.get("since")).toBe("1h");
    expect(call?.search.get("limit")).toBe("300");
    expect(call?.search.has("app")).toBe(false);
    expect(call?.search.has("level")).toBe(false);
    // The lines are drawn by the viewer, which only lays out the rows that fit; how a line reads
    // (whose it is, then the message verbatim) is held by data.test.ts.
    expect(screen.getByRole("region", { name: "Matching log lines" })).toBeInTheDocument();
    expect(screen.getByText("noust log search --since 1h")).toBeInTheDocument();
    // A lone server has no other servers to search.
    expect(screen.queryByRole("radio", { name: "Every server" })).toBeNull();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is in the sidebar and reads its question from the address", { timeout: 30_000 }, async () => {
    const { backend } = await pageAt("/logs?q=timeout&app=shop.example.com&since=24h&level=err");
    await screen.findByText("3 of 5 matching lines shown, out of 340 examined.");
    const call = backend.callsTo("GET /api/logs/search")[0];
    expect(call?.search.get("q")).toBe("timeout");
    expect(call?.search.getAll("app")).toEqual(["shop.example.com"]);
    expect(call?.search.get("since")).toBe("24h");
    expect(call?.search.get("level")).toBe("err");
    expect(screen.getByRole("searchbox", { name: "Search the logs" })).toHaveValue("timeout");
    expect(screen.getByText("noust log search timeout --app shop.example.com --since 24h --level err")).toBeInTheDocument();
    // The sidebar has it, as a destination of its own.
    expect(screen.getAllByRole("link", { name: "Logs" }).some((link) => link.getAttribute("href") === "/logs")).toBe(true);
  });

  it("asks again with the words typed, the window, the level and the application chosen", { timeout: 40_000 }, async () => {
    const { user, backend, location } = await pageAt();
    await screen.findByText("3 of 5 matching lines shown, out of 340 examined.");
    await user.type(screen.getByRole("searchbox", { name: "Search the logs" }), "refused");
    await waitFor(() => {
      expect(backend.callsTo("GET /api/logs/search").at(-1)?.search.get("q")).toBe("refused");
    });

    await user.click(screen.getByRole("radio", { name: "24 hours" }));
    await waitFor(() => {
      expect(backend.callsTo("GET /api/logs/search").at(-1)?.search.get("since")).toBe("24h");
    });

    await user.click(screen.getByRole("combobox", { name: "Level" }));
    await user.click(await screen.findByRole("option", { name: "Errors" }));
    await waitFor(() => {
      expect(backend.callsTo("GET /api/logs/search").at(-1)?.search.get("level")).toBe("err");
    });

    await user.click(screen.getByRole("combobox", { name: "Application" }));
    await user.click(await screen.findByRole("option", { name: "blog.example.com" }));
    await waitFor(() => {
      expect(backend.callsTo("GET /api/logs/search").at(-1)?.search.getAll("app")).toEqual(["blog.example.com"]);
    });
    // The question is in the address, so it can be sent to somebody.
    expect(location().search).toEqual({ q: "refused", since: "24h", level: "err", app: "blog.example.com" });

    await user.click(screen.getByRole("button", { name: "Clear the search" }));
    await waitFor(() => {
      expect(location().search).toEqual({});
    });
  });

  it("says when the scan stopped before the window did, and shows what the server left out, verbatim", { timeout: 30_000 }, async () => {
    await pageAt("/logs", {
      "GET /api/logs/search": () => json(200, found({ truncated: true, notes: ["The unit of shop.example.com has no journal in this window."] })),
    });
    expect(await screen.findByText(/The search stopped after 340 lines, so older matches may exist/)).toBeInTheDocument();
    expect(screen.getByText("The unit of shop.example.com has no journal in this window.")).toBeInTheDocument();
  });

  it("says the query as the server understood it, and nothing when there is no query", { timeout: 30_000 }, async () => {
    await pageAt("/logs?q=timeout%20-healthcheck", {
      "GET /api/logs/search": () => json(200, found({ understood: "timeout AND NOT healthcheck" })),
    });
    expect(await screen.findByText("Understood as")).toBeInTheDocument();
    expect(screen.getByText("timeout AND NOT healthcheck")).toBeInTheDocument();
  });

  it("says plainly when nothing matches", { timeout: 30_000 }, async () => {
    await pageAt("/logs?q=zzz", { "GET /api/logs/search": () => json(200, found({ lines: [], matched: 0 })) });
    expect(await screen.findByText("No line matches, out of 340 examined.")).toBeInTheDocument();
    expect(screen.getByText("No line matches this search.")).toBeInTheDocument();
  });

  it("shows the server's refusal of a query it cannot read, verbatim", { timeout: 30_000 }, async () => {
    await pageAt("/logs?q=level:loud", {
      "GET /api/logs/search": () => problem(400, "validation_error", "level:loud is not a level", { hint: "The levels are err, warning, info." }),
    });
    expect(await screen.findByText("The search was not understood")).toBeInTheDocument();
    expect(screen.getByText("level:loud is not a level")).toBeInTheDocument();
  });

  it("asks nothing to be searched when there are no applications", { timeout: 30_000 }, async () => {
    await pageAt("/logs", { "GET /api/logs/search": () => json(200, found({ lines: [], matched: 0, scanned: 0, apps: [] })) });
    expect(await screen.findByText("No applications to search yet")).toBeInTheDocument();
  });

  describe("on a central", () => {
    async function central(path = "/logs") {
      screenWidth(1440);
      const backend = fakeBackend({
        ...fleetRoutes(),
        "GET /api/apps": () => json(200, APPS),
        "GET /api/logs/search": () => json(200, found()),
      });
      const harness = renderConsole(path);
      await screen.findByRole("heading", { level: 1, name: "Logs" });
      return { ...harness, backend };
    }

    it("searches every server at once, naming the one each line came from and what a server left out", { timeout: 40_000 }, async () => {
      const { user, backend, location } = await central();
      await screen.findByText("3 of 5 matching lines shown, out of 340 examined.");
      expect(backend.callsTo("GET /api/fleet/logs")).toHaveLength(0);

      await user.click(screen.getByRole("radio", { name: "Every server" }));

      expect(await screen.findByText(/2 matching lines from 4 servers\. Up to 100 lines from each server\./)).toBeInTheDocument();
      const call = backend.callsTo("GET /api/fleet/logs")[0];
      expect(call?.search.get("q")).toBe("");
      expect(call?.search.get("since")).toBe("1h");
      expect(call?.search.get("limit")).toBe("100");
      expect(location().search).toEqual({ scope: "fleet" });
      // A server that could not search, and one that did not answer, are said rather than hidden.
      expect(screen.getByText("old-1: The search stopped at its scan limit; older matches may exist.")).toBeInTheDocument();
      expect(screen.getByText("2 of 4 servers did not answer fully")).toBeInTheDocument();
      expect(screen.getByText("noust fleet logs --since 1h")).toBeInTheDocument();
      // One server's applications are not another's: the filter waits until a single server is searched.
      expect(screen.getByRole("combobox", { name: "Application" })).toBeDisabled();
    });

    it("opens on every server from the address and goes back to this one", { timeout: 40_000 }, async () => {
      const { user, location } = await central("/logs?scope=fleet&q=refused");
      expect(await screen.findByText(/2 matching lines from 4 servers/)).toBeInTheDocument();
      expect(screen.getByRole("radio", { name: "Every server" })).toBeChecked();
      await user.click(screen.getByRole("radio", { name: "This server" }));
      await waitFor(() => {
        expect(location().search).toEqual({ q: "refused" });
      });
    });
  });

  it("reads in Spanish, with no accessibility violations", { timeout: 30_000 }, async () => {
    await act(async () => {
      await setLocale("es");
    });
    screenWidth(1440);
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/apps": () => json(200, APPS),
      "GET /api/logs/search": () => json(200, found()),
    });
    renderConsole("/logs");
    expect(await screen.findByRole("heading", { level: 1, name: "Registros" })).toBeInTheDocument();
    expect(await screen.findByText("3 de 5 líneas que coinciden, de 340 examinadas.")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
