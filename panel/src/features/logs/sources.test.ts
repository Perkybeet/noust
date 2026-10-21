import { describe, expect, it } from "vitest";

import { bindT } from "../../i18n";
import { patched, rangeChoices, sourceChoices, sourceOf, switchedTo } from "./sources";

const t = bindT("en");

describe("changing the question", () => {
  it("writes a change back and leaves out what is empty or is the default", () => {
    expect(patched({ q: "a" }, { q: "a b", since: "24h" })).toEqual({ q: "a b", since: "24h" });
    expect(patched({ q: "a", since: "24h" }, { q: undefined, since: "1h" })).toEqual({});
    expect(patched({}, { app: "shop.example.com", scope: "fleet" })).toEqual({ app: "shop.example.com", scope: "fleet" });
  });

  it("keeps a space the operator typed at the end of the words", () => {
    expect(patched({}, { q: "error " })).toEqual({ q: "error " });
  });
});

describe("the two sources", () => {
  it("are the applications unless the system is asked for", () => {
    expect(sourceOf({})).toBe("apps");
    expect(sourceOf({ source: "system" })).toBe("system");
  });

  it("take the words, the level and the range along to the system, and leave an application behind", () => {
    expect(switchedTo({ q: "timeout", level: "err", since: "24h", app: "shop.example.com", scope: "fleet" }, "system")).toEqual({
      source: "system",
      q: "timeout",
      level: "err",
      since: "24h",
    });
  });

  it("take the words along to the applications, and leave a service, a stretch, a boot and a line behind", () => {
    expect(switchedTo({ source: "system", q: "timeout", unit: "nginx.service", since: "boot", from: 1, to: 2, line: "c1" }, "apps")).toEqual({ q: "timeout" });
    expect(switchedTo({ source: "system", since: "7d", unit: "nginx.service" }, "apps")).toEqual({ since: "7d" });
  });
});

describe("the choices of the toolbar", () => {
  it("offer Live first and the two boots only to the system", () => {
    expect(rangeChoices(t, "apps").map((choice) => choice.value)).toEqual(["live", "15m", "1h", "6h", "24h", "7d"]);
    expect(rangeChoices(t, "system").map((choice) => choice.value)).toEqual(["live", "15m", "1h", "6h", "24h", "7d", "boot", "previous"]);
    expect(rangeChoices(t, "system").map((choice) => choice.label)).toEqual(["Live", "15 minutes", "1 hour", "6 hours", "24 hours", "7 days", "This boot", "Previous boot"]);
  });

  it("switch between Applications and System", () => {
    expect(sourceChoices(t)).toEqual([
      { value: "apps", label: "Applications" },
      { value: "system", label: "System" },
    ]);
  });
});
