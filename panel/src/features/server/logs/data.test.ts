import { describe, expect, it } from "vitest";

import { effectiveQuery, journalLines, logsCommand, searchFilters, stretchOf, validateLogsSearch } from "./data";

describe("the Logs tab's data", () => {
  it("reads the filters from the URL and drops what is not one", () => {
    expect(validateLogsSearch({ unit: " nginx.service ", priority: "err", range: "boot", q: "denied" })).toEqual({
      unit: "nginx.service",
      priority: "err",
      range: "boot",
      q: "denied",
    });
    expect(validateLogsSearch({ priority: "loud", range: "1h", unit: "" })).toEqual({});
  });

  it("reads a stretch picked elsewhere only when it has both ends and runs forward", () => {
    expect(validateLogsSearch({ from: "1790000000", to: "1790000600" })).toEqual({ from: 1790000000, to: 1790000600 });
    expect(validateLogsSearch({ from: 1790000000, to: 1790000600 })).toEqual({ from: 1790000000, to: 1790000600 });
    expect(validateLogsSearch({ from: "1790000000" })).toEqual({});
    expect(validateLogsSearch({ from: "1790000600", to: "1790000000" })).toEqual({});
    expect(validateLogsSearch({ from: "yesterday", to: "now" })).toEqual({});
  });

  it("turns the pickers into the fields of the query, before what was typed", () => {
    expect(effectiveQuery({})).toBe("");
    expect(effectiveQuery({ unit: "nginx.service", priority: "err", q: "timeout -debug" })).toBe("@unit:nginx.service @level:err timeout -debug");
    expect(effectiveQuery({ unit: "kernel", q: "oom" })).toBe("oom");
    expect(effectiveQuery({ unit: "odd unit" })).toBe('@unit:"odd unit"');
  });

  it("asks the API for the last hour by default, and for a boot by index", () => {
    expect(searchFilters({}, 300, 5000)).toEqual({ query: "", lines: 300, scan: 5000, since: "-1h" });
    expect(searchFilters({ unit: "kernel", range: "previous" }, 300, 5000)).toEqual({ query: "", lines: 300, scan: 5000, kernel: true, boot: -1 });
    expect(searchFilters({ unit: "nginx.service", priority: "err", range: "24h", q: "x" }, 1000, 20000)).toEqual({
      query: "@unit:nginx.service @level:err x",
      lines: 1000,
      scan: 20000,
      since: "-24h",
    });
  });

  it("asks for a stretch picked elsewhere by its seconds, whatever the range says", () => {
    expect(searchFilters({ range: "7d", from: 1790000000, to: 1790000600 }, 300, 5000)).toEqual({
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
    expect(stretchOf({ range: "24h" }, 100_000)).toEqual([13_600, 100_000]);
    expect(stretchOf({ range: "boot" }, 100_000)).toBeNull();
  });

  it("says the command that reads the same thing", () => {
    expect(logsCommand({})).toBe("noust server logs --since -1h");
    expect(logsCommand({ unit: "nginx.service", priority: "err", range: "boot", q: "it's" })).toBe("noust server logs -Q '@unit:nginx.service @level:err it'\\''s' -b 0");
    expect(logsCommand({ unit: "kernel", from: 100, to: 200 })).toBe("noust server logs -k --since @100 --until @200");
  });

  it("prints an entry the way journalctl does, the message verbatim, with its level and where to find it again", () => {
    const lines = journalLines({
      entries: [
        { timestamp: "2026-09-29T10:00:00.123456+00:00", priority: 3, unit: "nginx.service", message: "bind() to 0.0.0.0:80 failed", pid: 812, cursor: "c1" },
        { timestamp: "2026-09-29T10:00:01+00:00", priority: 6, unit: "sshd.service", message: "ok", pid: null, cursor: "c2" },
        { timestamp: "2026-09-29T10:00:02+00:00", priority: 4, unit: "cron.service", message: "late", cursor: "c3" },
      ],
    });
    expect(lines).toEqual([
      { id: 0, text: "2026-09-29T10:00:00+00:00 nginx.service[812]: bind() to 0.0.0.0:80 failed", ref: "c1", level: "error" },
      { id: 1, text: "2026-09-29T10:00:01+00:00 sshd.service: ok", ref: "c2" },
      { id: 2, text: "2026-09-29T10:00:02+00:00 cron.service: late", ref: "c3", level: "warn" },
    ]);
  });
});
