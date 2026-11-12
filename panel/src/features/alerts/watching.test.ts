import { describe, expect, it } from "vitest";

import type { Watch, Watching } from "../../api/queries/watching";
import { bindT, loadCatalog } from "../../i18n";
import { KNOWN_WATCHES, isOffered, limitedChannels, reachCell, reachMissing, reachOf, splitWatching, watchWords } from "./watching";

const en = bindT("en");

function watch(id: string, overrides: Partial<Watch> = {}): Watch {
  return { id, group: "server", state: "on", events: [], params: {}, reach: [{ name: "telegram", type: "telegram", limited: false }], offer: null, asks_outside: false, ...overrides };
}

function data(overrides: Partial<Watching> = {}): Watching {
  return {
    watcher: { state: "running", fix: null },
    notifications: { enabled: true, paused_until: null, channels: [{ name: "telegram", type: "telegram" }] },
    watches: [],
    ...overrides,
  };
}

describe("watchWords", () => {
  it("names every watch the server has a table for and says what it looks at, in both languages", async () => {
    const sentences = new Set<string>();
    for (const id of KNOWN_WATCHES) {
      const words = watchWords(en, watch(id, { params: { percent: 90, hours: 24, days: 7 } }));
      expect(words.name, id).not.toBe(id);
      expect(words.sentence, id).not.toBe("");
      sentences.add(words.sentence);
    }
    expect(sentences.size).toBe(KNOWN_WATCHES.length);
    await loadCatalog("es");
    const es = bindT("es");
    for (const id of KNOWN_WATCHES) expect(watchWords(es, watch(id, { params: { percent: 90, hours: 24, days: 7 } })).name, id).not.toBe(watchWords(en, watch(id)).name);
  });

  it("quotes the numbers the server read from the configuration, in the right number", () => {
    expect(watchWords(en, watch("disk", { params: { percent: 85 } })).sentence).toBe("It passes 85% of its space.");
    expect(watchWords(en, watch("updates", { params: { hours: 1 } })).sentence).toContain("every 1 hour ");
    expect(watchWords(en, watch("updates", { params: { hours: 24 } })).sentence).toContain("every 24 hours ");
    expect(watchWords(en, watch("backups_missing", { params: { days: 2 } })).sentence).toContain("2 days");
  });

  it("lists a watch it has no words for under its id, which is a server newer than the console", () => {
    expect(watchWords(en, watch("brand_new"))).toEqual({ name: "brand_new", sentence: "" });
  });
});

describe("splitWatching", () => {
  it("puts what is off and worth turning on apart from what is watched", () => {
    const offered = watch("domain_expiry", { state: "off", offer: { key: "monitor.domain_expiry", value: true }, asks_outside: true });
    const turnedOff = watch("updates", { state: "off" });
    const split = splitWatching(data({ watches: [watch("disk"), offered, turnedOff] }));
    expect(split.rows.map((row) => row.id)).toEqual(["disk", "updates"]);
    expect(split.offers.map((row) => row.id)).toEqual(["domain_expiry"]);
    expect(isOffered(turnedOff)).toBe(false);
  });

  it("offers nothing that is on, even if the server says how it would be turned on", () => {
    expect(isOffered(watch("domain_expiry", { state: "on", offer: { key: "monitor.domain_expiry", value: true } }))).toBe(false);
  });
});

describe("where it goes", () => {
  it("says nothing leaves the server when notifications are off, whatever channels there are", () => {
    expect(reachOf(data({ notifications: { enabled: false, paused_until: null, channels: [{ name: "telegram", type: "telegram" }] } }))).toEqual({ kind: "off" });
  });

  it("says a pause, then that no channel takes it, then the channels that do", () => {
    expect(reachOf(data({ notifications: { enabled: true, paused_until: "2026-11-12T18:00:00+00:00", channels: [] } }))).toEqual({ kind: "paused", until: "2026-11-12T18:00:00+00:00" });
    expect(reachOf(data({ notifications: { enabled: true, paused_until: null, channels: [] } }))).toEqual({ kind: "nobody" });
    expect(reachOf(data())).toEqual({ kind: "channels", names: ["telegram"] });
  });

  it("names the channels that hear less than everything, once each", () => {
    const limited = { name: "pager", type: "pagerduty", limited: true };
    expect(limitedChannels(data({ watches: [watch("disk", { reach: [limited] }), watch("deploys", { reach: [limited, { name: "telegram", type: "telegram", limited: false }] })] }))).toEqual(["pager"]);
    expect(limitedChannels(data({ watches: [watch("disk")] }))).toEqual([]);
  });

  it("gives a row's channels, and none when notifications are off", () => {
    expect(reachCell(watch("disk"), data())).toEqual(["telegram"]);
    expect(reachCell(watch("disk"), data({ notifications: { enabled: false, paused_until: null, channels: [] } }))).toEqual([]);
  });

  it("says of a row only the channels that do not take it: nothing when they all do", () => {
    const two = data({ notifications: { enabled: true, paused_until: null, channels: [{ name: "telegram", type: "telegram" }, { name: "email", type: "email" }] } });
    expect(reachMissing(watch("disk", { reach: [{ name: "telegram", type: "telegram", limited: false }, { name: "email", type: "email", limited: false }] }), two)).toBeNull();
    expect(reachMissing(watch("disk", { reach: [{ name: "telegram", type: "telegram", limited: false }] }), two)).toEqual(["email"]);
    expect(reachMissing(watch("disk", { reach: [] }), two)).toEqual(["telegram", "email"]);
  });

  it("says nothing of a row when there is no channel to miss or the top of the page already says nothing leaves", () => {
    expect(reachMissing(watch("disk", { reach: [] }), data({ notifications: { enabled: true, paused_until: null, channels: [] } }))).toBeNull();
    expect(reachMissing(watch("disk", { reach: [] }), data({ notifications: { enabled: false, paused_until: null, channels: [{ name: "telegram", type: "telegram" }] } }))).toBeNull();
  });
});
