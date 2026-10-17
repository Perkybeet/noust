import { describe, expect, it } from "vitest";

import { fleetViewerLines, paramsOf, searchCommand, stamp, validateLogsSearch, viewerLevel, viewerLines } from "./data";

describe("the log search's question", () => {
  it("reads the address, dropping anything malformed instead of failing", () => {
    expect(validateLogsSearch({ q: " timeout ", app: "shop.example.com", since: "24h", level: "err" })).toEqual({
      q: "timeout",
      app: "shop.example.com",
      since: "24h",
      level: "err",
    });
    expect(validateLogsSearch({ q: "   ", app: 5, since: "1y", level: "loud" })).toEqual({});
    // The hour is the default, so it is not carried in the address.
    expect(validateLogsSearch({ since: "1h" })).toEqual({});
    expect(validateLogsSearch({ q: "x".repeat(2000) }).q).toHaveLength(1000);
    // Every server is the one scope there is besides the server in view.
    expect(validateLogsSearch({ scope: "fleet" })).toEqual({ scope: "fleet" });
    expect(validateLogsSearch({ scope: "galaxy" })).toEqual({});
  });

  it("asks the API for the hour, every application and 300 lines when nothing is said", () => {
    expect(paramsOf({})).toEqual({ q: "", apps: [], since: "1h", level: undefined, limit: 300 });
    expect(paramsOf({ q: "a b", app: "shop.example.com", since: "7d", level: "warning" })).toEqual({
      q: "a b",
      apps: ["shop.example.com"],
      since: "7d",
      level: "warning",
      limit: 300,
    });
  });

  it("writes the command that asks the same thing from a terminal, quoting what needs it", () => {
    expect(searchCommand({})).toBe("noust log search --since 1h");
    expect(searchCommand({ q: "timeout", app: "shop.example.com", since: "24h", level: "err" })).toBe("noust log search timeout --app shop.example.com --since 24h --level err");
    expect(searchCommand({ q: "connection refused -health" })).toBe("noust log search 'connection refused -health' --since 1h");
    expect(searchCommand({ q: "it's" })).toBe("noust log search 'it'\\''s' --since 1h");
    // Every server has a command of its own, which has no application filter.
    expect(searchCommand({ scope: "fleet", q: "timeout", app: "shop.example.com", level: "err" })).toBe("noust fleet logs timeout --since 1h --level err");
  });
});

describe("the lines of every server", () => {
  it("reads the rows that are lines, oldest first, each naming its server, and leaves the notes out", () => {
    const lines = fleetViewerLines([
      { type: "line", ts: 1_790_000_100, node: "web-2", app: "shop.example.com", source: "journal", unit: "shop.service", level: "err", message: "newer" },
      { type: "line", ts: 1_790_000_000, node: "web-01", app: "blog.example.com", source: "container", unit: "web", level: null, message: "older" },
      { type: "note", ts: null, node: "old-1", message: "the scan stopped" },
      { type: "line", ts: "not a time", node: "web-2", message: "broken row" },
    ]);
    expect(lines.map((line) => line.text.replace(/^\S+ \S+ /, ""))).toEqual(["[web-01] blog.example.com/web: older", "[web-2] shop.example.com: newer"]);
    expect(lines.map((line) => line.level)).toEqual([undefined, "error"]);
  });
});

describe("a line of a log", () => {
  it("maps the journal's levels to the viewer's, and a container's missing one to none", () => {
    expect(["emerg", "alert", "crit", "err"].map(viewerLevel)).toEqual(["error", "error", "error", "error"]);
    expect(viewerLevel("warning")).toBe("warn");
    expect(["notice", "info"].map(viewerLevel)).toEqual(["info", "info"]);
    expect(viewerLevel("debug")).toBe("debug");
    expect(viewerLevel(null)).toBeUndefined();
    expect(viewerLevel("loud")).toBeUndefined();
  });

  it("writes the moment to the second in the browser's time", () => {
    expect(stamp(1_790_000_000)).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
  });

  it("says whose a line is: the application, the service of a container, or the unit of a system service", () => {
    const base = { ts: 1_790_000_000, process: "", message: "m", level: null };
    const lines = viewerLines([
      { ...base, app: "shop.example.com", source: "journal", unit: "shop.service" },
      { ...base, app: "blog.example.com", source: "container", unit: "web" },
      { ...base, app: "", source: "journal", unit: "nginx.service" },
    ]);
    expect(lines.map((item) => item.text.replace(/^\S+ \S+ /, ""))).toEqual(["shop.example.com: m", "blog.example.com/web: m", "nginx.service: m"]);
    expect(lines.map((item) => item.id)).toEqual([0, 1, 2]);
  });
});
