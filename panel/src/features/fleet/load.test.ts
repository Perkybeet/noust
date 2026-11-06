import { describe, expect, it } from "vitest";

import { loadOf, noustOf, osUpdatesOf, systemNameOf } from "./data";

/** A row of the summary view: what a server says of itself, with its machine snapshot and its server summary. */
const SUMMARY_ROW = {
  node: "web-2",
  noust: { current_version: "4.1.0", announced_version: "4.2.0", update_state: "update_available" },
  machine: { cpu_percent: 12, memory: { percent: 35.5 }, disk: { percent: 81 } },
  server: { os: { name: "Ubuntu", version: "22.04" }, updates: { pending: 5, security: 2 }, reboot: { required: true } },
};

describe("a server of the list, read from the summary view", () => {
  it("reads how loaded it is from its machine snapshot", () => {
    expect(loadOf(SUMMARY_ROW)).toEqual({ cpu: 12, memory: 35.5, disk: 81 });
  });

  it("says nothing of a server that gave no snapshot, and what is missing from one it gave", () => {
    expect(loadOf({ node: "old-1" })).toBeNull();
    expect(loadOf({ machine: { cpu_percent: 3 } })).toEqual({ cpu: 3, memory: null, disk: null });
  });

  it("names the system once, with its version unless the name already holds it", () => {
    expect(systemNameOf(SUMMARY_ROW)).toBe("Ubuntu 22.04");
    expect(systemNameOf({ server: { os: { name: "Debian 12", version: "12" } } })).toBe("Debian 12");
    expect(systemNameOf({ node: "x" })).toBeNull();
    // The updates view says it under `os.os`: the same name.
    expect(systemNameOf({ os: { os: { name: "Debian", version: "12" } } })).toBe("Debian 12");
  });

  it("reads the updates of its system from the summary's server part as it does from the updates view's os part", () => {
    expect(osUpdatesOf(SUMMARY_ROW)).toMatchObject({ pending: 5, security: 2, reboot: true });
    expect(osUpdatesOf({ os: { updates: { pending: 1 }, reboot: { required: false } } })).toMatchObject({ pending: 1, reboot: false });
    expect(osUpdatesOf({ node: "old-1" })).toBeNull();
  });

  it("takes the version it is told to update to from the server's own answer", () => {
    expect(noustOf(SUMMARY_ROW)).toMatchObject({ current: "4.1.0", latest: "4.2.0", state: "update_available" });
  });
});
