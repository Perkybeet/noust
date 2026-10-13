import { describe, expect, it } from "vitest";

import type { FleetRow } from "./data";
import { fleetTarget } from "./target";

function server(node: string, current: string, state: string, latest: string | null = null): FleetRow {
  return { node, local: false, href: `/n/${node}`, noust: { current_version: current, latest_version: latest, update_state: state } };
}

describe("fleetTarget", () => {
  it("is the newest version any server's source offers", () => {
    const target = fleetTarget([
      server("a", "3.3.1", "up_to_date"),
      server("b", "3.3.0", "update_available", "3.3.1"),
      server("c", "3.3.0", "on_the_way", "3.3.1"),
    ]);

    expect(target).toEqual({ version: "3.3.1", total: 3, onTarget: 1, updatable: 1, onTheWay: 1 });
  });

  it("counts a server whose package index has not seen the release as able to update", () => {
    const target = fleetTarget([server("a", "3.3.0", "index_behind", "3.3.1"), server("b", "3.3.1", "up_to_date")]);

    expect(target).toMatchObject({ version: "3.3.1", onTarget: 1, updatable: 1, onTheWay: 0 });
  });

  it("takes a server that says it is up to date at its word, whatever the published release is", () => {
    // Mixed distributions: this one is served 3.3.0 only, so 3.3.0 is all it can reach.
    const target = fleetTarget([server("a", "3.3.0", "up_to_date", "3.3.1")]);

    expect(target).toEqual({ version: "3.3.0", total: 1, onTarget: 1, updatable: 0, onTheWay: 0 });
  });

  it("leaves out a server that was not checked", () => {
    const target = fleetTarget([server("a", "3.3.1", "up_to_date"), server("b", "3.2.0", "unknown")]);

    expect(target).toMatchObject({ total: 1, onTarget: 1 });
  });

  it("is null when no server reported a version", () => {
    expect(fleetTarget([])).toBeNull();
    expect(fleetTarget([{ node: "x", local: false, href: "/", noust: {} }])).toBeNull();
  });

  it("compares versions as numbers, not as text", () => {
    const target = fleetTarget([server("a", "3.9.0", "up_to_date"), server("b", "3.10.0", "up_to_date")]);

    expect(target?.version).toBe("3.10.0");
    expect(target).toMatchObject({ onTarget: 1 });
  });
});
