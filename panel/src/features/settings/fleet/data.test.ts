import { describe, expect, it } from "vitest";

import { cellView, pendingCount } from "./data";
import type { Cell, FleetStatus } from "./data";

const cell = (over: Partial<Cell>): Cell => ({ node: "fra-2", origin: "fleet", sync: "in_sync", ...over });

describe("how a server's cell reads", () => {
  it("is plain, with no colour, for a server that follows the fleet and has what it says", () => {
    expect(cellView(cell({}))).toEqual({ level: "follows", state: null, label: "fleetSettings.cell.follows", restorable: false });
  });

  it("tells a value of its own from a server that follows, and says it is the central's or the server's", () => {
    expect(cellView(cell({ origin: "central_exception" }))).toMatchObject({ level: "exception", state: null, label: "fleetSettings.cell.exception" });
    expect(cellView(cell({ origin: "local_exception", sync: "in_sync" }))).toMatchObject({ level: "exception", state: null, label: "fleetSettings.cell.ownValue", restorable: false });
  });

  it("warns, and offers to put back, where the server's operator changed what the fleet gave it", () => {
    const changed = cellView(cell({ origin: "local_exception", sync: "diverged" }));
    expect(changed).toMatchObject({ level: "exception", state: "warning", label: "fleetSettings.cell.changedHere", restorable: true });
    expect(cellView(cell({ origin: "local_exception", sync: "diverged", restore_requested: true })).label).toBe("fleetSettings.cell.restoreAsked");
  });

  it("is waiting where the fleet's value has not reached the server yet", () => {
    expect(cellView(cell({ sync: "pending" }))).toMatchObject({ level: "follows", state: "queued", label: "fleetSettings.cell.pending" });
    expect(cellView(cell({ origin: "central_exception", sync: "pending" })).level).toBe("exception");
  });

  it("puts a server that cannot be read, or that refuses, before whatever it should hold", () => {
    expect(cellView(cell({ sync: "unreachable", origin: "independent" }))).toMatchObject({ state: "failed", label: "fleetSettings.cell.unreachable" });
    expect(cellView(cell({ sync: "refused" })).state).toBe("failed");
    expect(cellView(cell({ sync: "unsupported" }))).toMatchObject({ state: "stopped", label: "fleet.outcome.unsupported" });
  });

  it("leaves an independent server alone on purpose, in grey and not as a problem", () => {
    expect(cellView(cell({ origin: "independent", sync: "not_applicable" }))).toMatchObject({ level: "independent", state: "stopped", label: "fleetSettings.cell.independent" });
  });

  it("says a channel is not sent to a server whose route cannot match there, instead of a gap", () => {
    expect(cellView(cell({ reach: { reaches: false, why: "server" } }))).toMatchObject({ state: "stopped", label: "fleetSettings.cell.notSent" });
  });

  it("says a server that differs from the fleet where nobody changed it as a difference to put back", () => {
    expect(cellView(cell({ sync: "diverged" }))).toMatchObject({ level: "follows", state: "warning", restorable: true });
  });
});

describe("what waits to be applied", () => {
  const status = (syncs: Cell["sync"][]): FleetStatus => ({
    generated_at: "2026-10-29T10:00:00Z",
    partial: false,
    servers: [],
    released: [],
    settings: [{ resource: "variable", name: "A", version: 1, change: "c1", changed_by: "marta", changed_at: "2026-10-29T10:00:00Z", exceptions: [], servers: syncs.map((sync, index) => cell({ node: `s${String(index)}`, sync })) }],
  });
  it("counts the servers that are not what the fleet says, and not the ones that are well or out of reach", () => {
    expect(pendingCount(status(["in_sync", "pending", "diverged", "unreachable", "not_applicable"]))).toBe(2);
    expect(pendingCount(undefined)).toBe(0);
  });
});
