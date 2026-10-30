import { describe, expect, it } from "vitest";

import type { ListeningPort, PortClaim } from "../queries";
import { askedPort, isExposed, isFree, matchRows, portRows, validateNetworkSearch } from "./data";

const claim = (over: Partial<PortClaim> & Pick<PortClaim, "port" | "protocol" | "kind" | "owner">): PortClaim => ({ address: null, detail: "", processes: [], ...over });

const CLAIMS: PortClaim[] = [
  claim({ port: 443, protocol: "tcp", kind: "system", owner: "web" }),
  claim({ port: 443, protocol: "udp", kind: "system", owner: "web" }),
  claim({ port: 22, protocol: "tcp", kind: "system", owner: "ssh" }),
  claim({ port: 3000, protocol: "tcp", kind: "app", owner: "shop.example.net" }),
  // The kernel sees the same app as a process: one row says what Noust knows, not two.
  claim({ port: 3000, protocol: "tcp", kind: "process", owner: "node", address: "127.0.0.1", processes: ["node"] }),
  claim({ port: 9090, protocol: "tcp", kind: "process", owner: "prometheus", address: "0.0.0.0", processes: ["prometheus"] }),
  claim({ port: 25565, protocol: "udp", kind: "stream", owner: "game" }),
];

const BYPASS: ListeningPort = { proto: "tcp", port: 9090, address: "0.0.0.0", process: "prometheus", verdict: "docker_bypass", sources: [], risky: "", baseline: false, reachable: true };
const SSH: ListeningPort = { proto: "tcp", port: 22, address: "0.0.0.0", process: "sshd", verdict: "open", sources: [], risky: "", baseline: true, reachable: true };
const LISTENING: ListeningPort[] = [BYPASS, SSH];

/** The row of a port, or a failure that says which one the fixture lacks. */
function rowOf(rows: ReturnType<typeof portRows>, key: string) {
  const row = rows.find((entry) => entry.key === key);
  if (row === undefined) throw new Error(`no row for ${key}`);
  return row;
}

describe("one row per port", () => {
  it("names what Noust knows of a port before the process the kernel reports, and shows one holder for it", () => {
    const row = portRows(CLAIMS, []).find((entry) => entry.port === 3000);

    expect(row?.holders).toHaveLength(1);
    expect(row?.holders[0]).toMatchObject({ kind: "app", owner: "shop.example.net" });
  });

  it("shows the process for a port nothing in Noust accounts for", () => {
    const row = portRows(CLAIMS, []).find((entry) => entry.port === 9090);

    expect(row?.holders).toEqual([expect.objectContaining({ kind: "process", owner: "prometheus" })]);
  });

  it("keeps TCP and UDP apart, and a published port on UDP is its own row", () => {
    const keys = portRows(CLAIMS, []).map((entry) => entry.key);

    expect(keys).toContain("udp:25565");
    expect(keys).toContain("tcp:443");
  });

  it("leaves out the machine's own UDP reservations until something listens on them", () => {
    const keys = portRows(CLAIMS, []).map((entry) => entry.key);

    expect(keys).not.toContain("udp:443");
    expect(portRows(CLAIMS, [{ ...BYPASS, proto: "udp", port: 443 }]).map((entry) => entry.key)).toContain("udp:443");
  });

  it("sorts by port, then protocol", () => {
    const ports = portRows(CLAIMS, []).map((entry) => entry.port);

    expect(ports).toEqual([...ports].sort((a, b) => a - b));
  });

  it("joins what the firewall says of a listening port, and only of that one", () => {
    const rows = portRows(CLAIMS, LISTENING);

    expect(rows.find((entry) => entry.key === "tcp:22")?.firewall?.verdict).toBe("open");
    expect(rows.find((entry) => entry.key === "tcp:3000")?.firewall).toBeNull();
  });

  it("says a port is open by accident when Docker publishes it around the firewall", () => {
    const rows = portRows(CLAIMS, LISTENING);

    expect(isExposed(rowOf(rows, "tcp:9090"))).toBe(true);
    expect(isExposed(rowOf(rows, "tcp:22"))).toBe(false);
  });

  it("ignores a protocol it does not know instead of inventing a row", () => {
    expect(portRows([claim({ port: 1, protocol: "sctp", kind: "process", owner: "x" })], [])).toEqual([]);
  });
});

describe("who uses port N", () => {
  const rows = portRows(CLAIMS, LISTENING);

  it("takes a number as a port, or the start of one", () => {
    expect(matchRows(rows, "443").map((entry) => entry.port)).toEqual([443]);
    expect(matchRows(rows, "30").map((entry) => entry.port)).toEqual([3000]);
  });

  it("takes words as what holds the port: an application, a process, a kind", () => {
    expect(matchRows(rows, "shop").map((entry) => entry.port)).toEqual([3000]);
    expect(matchRows(rows, "prometheus").map((entry) => entry.port)).toEqual([9090]);
    expect(matchRows(rows, "ssh").map((entry) => entry.port)).toEqual([22]);
  });

  it("keeps everything for an empty search", () => {
    expect(matchRows(rows, undefined)).toHaveLength(rows.length);
    expect(matchRows(rows, "  ")).toHaveLength(rows.length);
  });

  it("knows a port is free only when it is a whole port number that nothing holds", () => {
    expect(askedPort("5432")).toBe(5432);
    expect(askedPort("0")).toBeNull();
    expect(askedPort("65536")).toBeNull();
    expect(askedPort("5432x")).toBeNull();
    expect(askedPort(undefined)).toBeNull();
    expect(isFree(rows, 5432)).toBe(true);
    expect(isFree(rows, 3000)).toBe(false);
  });
});

describe("the address", () => {
  it("keeps a view and a search, drops anything else", () => {
    expect(validateNetworkSearch({ view: "firewall", q: " 5432 " })).toEqual({ view: "firewall", q: "5432" });
    expect(validateNetworkSearch({ view: "ports" })).toEqual({});
    expect(validateNetworkSearch({ view: "nonsense", q: 4 })).toEqual({});
  });

  it("does not take a search of unreasonable length whole", () => {
    expect(validateNetworkSearch({ q: "x".repeat(500) }).q).toHaveLength(60);
  });
});
