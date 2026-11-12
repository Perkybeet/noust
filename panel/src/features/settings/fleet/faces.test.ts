import { describe, expect, it } from "vitest";

import type { Cell, StatusServer, StatusSetting } from "./data";
import { conditionsOf, faceOf, followWords, isWaiting, marksOf, returnPlan, savePlan, serverSummary } from "./faces";

const cell = (over: Partial<Cell>): Cell => ({ node: "web-01", origin: "fleet", sync: "in_sync", wanted_version: 3, applied_version: 3, ...over });

const setting = (name: string, servers: Cell[], over: Partial<StatusSetting> = {}): StatusSetting => ({
  resource: "channel",
  name,
  version: 3,
  change: "c1",
  changed_by: "yago",
  changed_at: "2026-11-10T10:00:00Z",
  exceptions: [],
  servers,
  ...over,
});

describe("the face of a server's cell: one word at a time", () => {
  it("follows when the server holds what the fleet says", () => {
    expect(faceOf(cell({}))).toBe("follows");
  });

  it("is an exception when the central gave it a value of its own", () => {
    expect(faceOf(cell({ origin: "central_exception" }))).toBe("exception");
  });

  it("is an exception even while it has not arrived: it is its own, and saving the fleet's value leaves it alone", () => {
    expect(faceOf(cell({ origin: "central_exception", sync: "pending", applied_version: 2 }))).toBe("exception");
  });

  it("is changed on the server when its operator moved off the fleet's value", () => {
    expect(faceOf(cell({ origin: "local_exception", sync: "diverged" }))).toBe("changedHere");
    expect(faceOf(cell({ origin: "local_exception", sync: "in_sync", restore_requested: true }))).toBe("changedHere");
  });

  it("is pending when the server answers and the change is not there yet", () => {
    expect(faceOf(cell({ sync: "pending", applied_version: 2 }))).toBe("pending");
    expect(faceOf(cell({ sync: "release_pending" }))).toBe("pending");
  });

  it("is off when the server does not answer, and keeps what is waiting for it apart", () => {
    const silent = cell({ sync: "unreachable", applied_version: 2 });
    expect(faceOf(silent)).toBe("off");
    expect(isWaiting(silent)).toBe(true);
    expect(isWaiting(cell({ sync: "unreachable" }))).toBe(false);
  });

  it("takes the server's own word for waiting when it sends one", () => {
    expect(isWaiting(cell({ sync: "unreachable", waiting: false, applied_version: 1 }))).toBe(false);
    expect(isWaiting(cell({ sync: "unreachable", waiting: true }))).toBe(true);
  });

  it("is independent before anything else is asked of the server: it is a decision, not a failure", () => {
    expect(faceOf(cell({ origin: "independent", sync: "unsupported" }))).toBe("independent");
    expect(faceOf(cell({ origin: "independent", sync: "not_applicable", wanted_version: null }))).toBe("independent");
  });

  it("is refused by the ceiling or unsupported by the version", () => {
    expect(faceOf(cell({ sync: "refused" }))).toBe("refused");
    expect(faceOf(cell({ sync: "unsupported" }))).toBe("unsupported");
  });

  it("is not sent where a channel's route cannot match, and none where the fleet has nothing for it", () => {
    expect(faceOf(cell({ reach: { reaches: false, why: "server" } }))).toBe("notSent");
    expect(faceOf(cell({ origin: "none", sync: "not_applicable" }))).toBe("none");
  });

  it("is unknown when the server could not be read for another reason", () => {
    expect(faceOf(cell({ sync: "unknown" }))).toBe("unknown");
  });
});

describe("the line under a setting", () => {
  it("says how many follow it out of how many, as the server counts them", () => {
    expect(followWords(2, 4)).toEqual({ kind: "some", following: 2, of: 4 });
    expect(followWords(4, 4)).toEqual({ kind: "all", of: 4 });
    expect(followWords(0, 4)).toEqual({ kind: "none" });
    expect(followWords(1, 4)).toEqual({ kind: "one", of: 4 });
    expect(followWords(1, 1)).toEqual({ kind: "single" });
    expect(followWords(0, 1)).toEqual({ kind: "none" });
  });

  it("keeps for the row only what is true of this setting on a server", () => {
    const row = setting("slack", [
      cell({ node: "web-01" }),
      cell({ node: "web-02", sync: "unreachable", applied_version: 2 }),
      cell({ node: "db-02", sync: "refused" }),
      cell({ node: "vps-03", origin: "independent", sync: "not_applicable" }),
      cell({ node: "web-04", origin: "central_exception" }),
    ]);
    expect(marksOf(row).map((mark) => [mark.node, mark.face])).toEqual([
      ["web-02", "off"],
      ["web-04", "exception"],
    ]);
  });
});

describe("what saving a value does on each server, said before it is saved", () => {
  it("applies on the ones that follow, waits for the one that is off, keeps what is the server's own, and says why it skips", () => {
    const row = setting("slack", [
      cell({ node: "web-01" }),
      cell({ node: "web-02", sync: "unreachable", applied_version: 2 }),
      cell({ node: "web-04", origin: "central_exception" }),
      cell({ node: "web-05", origin: "local_exception", sync: "diverged" }),
      cell({ node: "db-02", sync: "refused" }),
      cell({ node: "vps-03", origin: "independent", sync: "not_applicable" }),
      cell({ node: "old-01", sync: "unsupported" }),
    ]);
    const plan = savePlan(row);
    expect(plan.rows.map((entry) => [entry.node, entry.does])).toEqual([
      ["web-01", "applies"],
      ["web-02", "waits"],
      ["web-04", "keepsOwn"],
      ["web-05", "keepsOwn"],
      ["db-02", "refused"],
      ["vps-03", "skips"],
      ["old-01", "unsupported"],
    ]);
    expect(plan.now).toBe(1);
    expect(plan.later).toBe(1);
  });
});

describe("the strip of conditions over a page", () => {
  const server = (node: string, over: Partial<StatusServer> = {}): StatusServer => ({ node, mode: "managed", outcome: "ok", ...over });

  it("names only the servers that something stands in the way of, one condition each", () => {
    const found = conditionsOf([
      server("web-01"),
      server("web-02", { outcome: "unreachable" }),
      server("db-02", { outcome: "forbidden" }),
      server("vps-03", { mode: "independent" }),
      server("old-01", { outcome: "unsupported" }),
    ]);
    expect(found).toEqual([
      { node: "web-02", face: "off" },
      { node: "db-02", face: "refused" },
      { node: "vps-03", face: "independent" },
      { node: "old-01", face: "unsupported" },
    ]);
  });

  it("is empty for a lone server, which has no servers to tell apart, and names one server that is off", () => {
    expect(conditionsOf([])).toEqual([]);
    expect(conditionsOf([server("web-01", { outcome: "unreachable" })])).toEqual([{ node: "web-01", face: "off" }]);
  });
});

describe("a server's file over the settings of the fleet", () => {
  const rows = [
    setting("a", [cell({ node: "web-02" })]),
    setting("b", [cell({ node: "web-02", origin: "central_exception" })]),
    setting("c", [cell({ node: "web-02", origin: "local_exception", sync: "diverged" })]),
    setting("d", [cell({ node: "web-02", sync: "pending", applied_version: 2 })], { version: 3 }),
  ];

  it("counts what it follows, what it holds by itself and what waits to arrive", () => {
    expect(serverSummary("web-02", rows)).toEqual({ total: 4, follows: 2, exceptions: 1, changedHere: 1, waiting: 1 });
  });

  it("says what an independent server gets back when it follows again, from the versions it still reports", () => {
    const apart = [
      setting("a", [cell({ node: "vps-03", origin: "independent", sync: "not_applicable", wanted_version: null, applied_version: 3 })], { version: 3 }),
      setting("b", [cell({ node: "vps-03", origin: "independent", sync: "not_applicable", wanted_version: null, applied_version: 1 })], { version: 4 }),
      setting("c", [cell({ node: "vps-03", origin: "independent", sync: "not_applicable", wanted_version: null, applied_version: null })], { version: 2 }),
    ];
    expect(returnPlan("vps-03", apart)).toEqual({ total: 3, receives: 2, same: 1 });
  });

  it("takes the central's word for what it would send when it says it, and not the versions", () => {
    const told = [
      setting("a", [cell({ node: "vps-03", origin: "independent", sync: "not_applicable", wanted_version: null, applied_version: 1, if_followed: "in_sync" })], { version: 4 }),
      setting("b", [cell({ node: "vps-03", origin: "independent", sync: "not_applicable", wanted_version: null, applied_version: 4, if_followed: "pending" })], { version: 4 }),
    ];
    expect(returnPlan("vps-03", told)).toEqual({ total: 2, receives: 1, same: 1 });
  });
});
