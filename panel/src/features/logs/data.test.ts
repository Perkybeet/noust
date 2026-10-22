import { describe, expect, it } from "vitest";

import {
  effectiveQuery,
  fleetViewerLines,
  isLive,
  journalFilters,
  paramsOf,
  rangeOf,
  searchCommand,
  stamp,
  stretchOf,
  validateLogsSearch,
  viewerLevel,
  viewerLines,
} from "./data";

describe("the log search's question", () => {
  it("reads the address, dropping anything malformed instead of failing", () => {
    expect(validateLogsSearch({ q: "timeout", app: "shop.example.com", since: "24h", level: "err" })).toEqual({
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

  it("keeps what was typed as it was typed, so a space between two words survives the round trip through the address", () => {
    expect(validateLogsSearch({ q: "error " })).toEqual({ q: "error " });
    expect(validateLogsSearch({ q: "   " })).toEqual({});
  });

  it("reads the system as a source, with its service, its range and a line a link points at", () => {
    expect(validateLogsSearch({ source: "system", unit: " nginx.service ", since: "boot", line: "s=abc;i=1" })).toEqual({
      source: "system",
      unit: "nginx.service",
      since: "boot",
      line: "s=abc;i=1",
    });
    expect(validateLogsSearch({ source: "galaxy", since: "live" })).toEqual({ since: "live" });
    // The journal's priority "notice" is a level the system can be narrowed to.
    expect(validateLogsSearch({ level: "notice" })).toEqual({ level: "notice" });
  });

  it("reads a stretch picked elsewhere only when it has both ends and runs forward", () => {
    expect(validateLogsSearch({ from: "1790000000", to: "1790000600" })).toEqual({ from: 1790000000, to: 1790000600 });
    expect(validateLogsSearch({ from: 1790000000, to: 1790000600 })).toEqual({ from: 1790000000, to: 1790000600 });
    expect(validateLogsSearch({ from: "1790000000" })).toEqual({});
    expect(validateLogsSearch({ from: "1790000600", to: "1790000000" })).toEqual({});
    expect(validateLogsSearch({ from: "yesterday", to: "now" })).toEqual({});
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
    // Live reads the newest quarter of an hour, again and again.
    expect(paramsOf({ since: "live" }).since).toBe("15m");
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

describe("the range of a search", () => {
  it("is the last hour unless the address says otherwise, and live is a range", () => {
    expect(rangeOf({})).toBe("1h");
    expect(rangeOf({ since: "live" })).toBe("live");
    expect(isLive({ since: "live" })).toBe(true);
    expect(isLive({})).toBe(false);
    // A stretch chosen elsewhere is not a stream.
    expect(isLive({ since: "live", from: 100, to: 200 })).toBe(false);
  });

  it("offers a boot only to the system: an application's lines have none", () => {
    expect(rangeOf({ since: "boot" })).toBe("1h");
    expect(rangeOf({ source: "system", since: "boot" })).toBe("boot");
    expect(rangeOf({ source: "system", since: "previous" })).toBe("previous");
  });
});

describe("what the system's journal is asked", () => {
  it("turns the pickers into the fields of the query, before what was typed", () => {
    expect(effectiveQuery({})).toBe("");
    expect(effectiveQuery({ unit: "nginx.service", level: "err", q: "timeout -debug" })).toBe("@unit:nginx.service @level:err timeout -debug");
    expect(effectiveQuery({ unit: "kernel", q: "oom" })).toBe("oom");
    expect(effectiveQuery({ unit: "odd unit" })).toBe('@unit:"odd unit"');
  });

  it("asks for the last hour by default, for a window by its relative time, and for a boot by index", () => {
    expect(journalFilters({ source: "system" }, 300, 5000)).toEqual({ query: "", lines: 300, scan: 5000, since: "-1h" });
    expect(journalFilters({ source: "system", since: "6h" }, 300, 5000)).toEqual({ query: "", lines: 300, scan: 5000, since: "-6h" });
    expect(journalFilters({ source: "system", since: "live" }, 300, 5000)).toEqual({ query: "", lines: 300, scan: 5000, since: "-15min" });
    expect(journalFilters({ source: "system", unit: "kernel", since: "previous" }, 300, 5000)).toEqual({ query: "", lines: 300, scan: 5000, kernel: true, boot: -1 });
    expect(journalFilters({ source: "system", since: "boot" }, 300, 5000)).toEqual({ query: "", lines: 300, scan: 5000, boot: 0 });
    expect(journalFilters({ source: "system", unit: "nginx.service", level: "err", since: "24h", q: "x" }, 1000, 20000)).toEqual({
      query: "@unit:nginx.service @level:err x",
      lines: 1000,
      scan: 20000,
      since: "-24h",
    });
  });

  it("asks for a stretch picked elsewhere by its seconds, whatever the range says", () => {
    expect(journalFilters({ source: "system", since: "7d", from: 1790000000, to: 1790000600 }, 300, 5000)).toEqual({
      query: "",
      lines: 300,
      scan: 5000,
      since: "@1790000000",
      until: "@1790000600",
    });
  });

  it("knows the stretch it is looking at, when there is one", () => {
    expect(stretchOf({ from: 100, to: 200 }, 5000)).toEqual([100, 200]);
    expect(stretchOf({}, 10_000)).toEqual([6_400, 10_000]);
    expect(stretchOf({ since: "24h" }, 100_000)).toEqual([13_600, 100_000]);
    expect(stretchOf({ source: "system", since: "boot" }, 100_000)).toBeNull();
  });

  it("says the command that reads the same thing", () => {
    expect(searchCommand({ source: "system" })).toBe("noust server logs --since -1h");
    expect(searchCommand({ source: "system", unit: "nginx.service", level: "err", since: "boot", q: "it's" })).toBe(
      "noust server logs -Q '@unit:nginx.service @level:err it'\\''s' -b 0",
    );
    expect(searchCommand({ source: "system", unit: "kernel", from: 100, to: 200 })).toBe("noust server logs -k --since @100 --until @200");
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
