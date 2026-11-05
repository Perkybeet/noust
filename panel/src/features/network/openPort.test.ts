import { describe, expect, it } from "vitest";

import type { ListeningPort, PortClaim } from "../server/queries";
import { portRows } from "../server/network/data";
import { OPEN_PORT_DEFAULTS, factsOf, parsePort, planOf, ruleBody, streamBody } from "./openPort";
import type { OpenForm } from "./openPort";

const claim = (over: Partial<PortClaim> & Pick<PortClaim, "port" | "protocol" | "kind" | "owner">): PortClaim => ({ address: null, detail: "", processes: [], ...over });
const listening = (over: Partial<ListeningPort> & Pick<ListeningPort, "port" | "proto" | "address" | "verdict">): ListeningPort => ({ process: null, sources: [], risky: "", baseline: false, reachable: true, ...over });

const CLAIMS: PortClaim[] = [
  claim({ port: 25565, protocol: "tcp", kind: "process", owner: "java", address: "0.0.0.0", processes: ["java"] }),
  claim({ port: 6379, protocol: "tcp", kind: "database", owner: "redis@main", address: "127.0.0.1", processes: ["redis-server"] }),
  claim({ port: 5432, protocol: "tcp", kind: "database", owner: "postgresql", address: "0.0.0.0", processes: ["postgres"] }),
];
const FIREWALL: ListeningPort[] = [
  listening({ port: 25565, proto: "tcp", address: "0.0.0.0", verdict: "blocked", process: "java", reachable: false }),
  listening({ port: 6379, proto: "tcp", address: "127.0.0.1", verdict: "local", process: "redis-server", reachable: false }),
  listening({ port: 5432, proto: "tcp", address: "0.0.0.0", verdict: "open_to", process: "postgres", sources: ["10.0.0.0/24"] }),
];
const ROWS = portRows(CLAIMS, FIREWALL);

const form = (over: Partial<OpenForm>): OpenForm => ({ ...OPEN_PORT_DEFAULTS, ...over });
const ACTIVE = { active: true, installed: true };

describe("the port a person typed", () => {
  it("is a whole number from 1 to 65535 and nothing else", () => {
    expect(parsePort("25565")).toBe(25565);
    expect(parsePort(" 80 ")).toBe(80);
    expect(parsePort("65535")).toBe(65535);
    for (const bad of ["", "0", "65536", "-1", "80.5", "8o", "0x50", "99999999999"]) expect(parsePort(bad), bad).toBeNull();
  });
});

describe("who listens on the port", () => {
  it("is free when nothing holds it, and the port is opened all the same", () => {
    const fact = factsOf(ROWS, 8080, "tcp");

    expect(fact).toMatchObject({ reach: "free", process: null, holder: null });
  });

  it("is open to the network when the program listens on every address", () => {
    const fact = factsOf(ROWS, 25565, "tcp");

    expect(fact.reach).toBe("outside");
    expect(fact.process).toBe("java");
  });

  it("is inside only when the program listens on 127.0.0.1: the firewall alone does not reach it", () => {
    const fact = factsOf(ROWS, 6379, "tcp");

    expect(fact.reach).toBe("inside");
    expect(fact.process).toBe("redis-server");
    expect(fact.holder?.kind).toBe("database");
  });

  it("says a port that already answers whoever asks is open, and one open to a network is not open to everyone", () => {
    const open = portRows(CLAIMS, [listening({ port: 25565, proto: "tcp", address: "0.0.0.0", verdict: "open", process: "java" })]);

    expect(factsOf(open, 25565, "tcp").openToAll).toBe(true);
    expect(factsOf(ROWS, 5432, "tcp").openToAll).toBe(false);
  });

  it("reads the protocol the person chose, and either one for 'both'", () => {
    const udp = portRows([claim({ port: 51820, protocol: "udp", kind: "process", owner: "wg", address: "0.0.0.0" })], []);

    expect(factsOf(udp, 51820, "tcp").reach).toBe("free");
    expect(factsOf(udp, 51820, "udp").reach).not.toBe("free");
    expect(factsOf(udp, 51820, "any").reach).not.toBe("free");
  });
});

describe("what opening a port does", () => {
  it("is one firewall rule for a program that listens on every address", () => {
    const { plan, problems } = planOf(form({ port: "25565" }), ROWS, ACTIVE);

    expect(problems).toEqual({});
    expect(plan).toMatchObject({ mode: "rule" });
    expect(ruleBody(form({ port: "25565" }))).toEqual({ action: "allow", port: 25565, proto: "tcp", source: "any", comment: "" });
  });

  it("is that rule too for a port nothing listens on yet: it answers when the program starts", () => {
    const { plan } = planOf(form({ port: "8080" }), ROWS, ACTIVE);

    expect(plan).toMatchObject({ mode: "rule" });
  });

  it("restricts the rule to one address or network when the person asks", () => {
    const { plan, problems } = planOf(form({ port: "5432", who: "some", source: "10.0.0.0/24" }), ROWS, ACTIVE);

    expect(problems).toEqual({});
    expect(plan).toMatchObject({ mode: "rule" });
    expect(ruleBody(form({ port: "5432", who: "some", source: "10.0.0.0/24" }))).toMatchObject({ source: "10.0.0.0/24" });
  });

  it("opens both protocols with the one rule the firewall calls 'any'", () => {
    expect(ruleBody(form({ port: "51820", proto: "any" }))).toMatchObject({ proto: "any" });
  });

  it("is a relay through nginx for a program that listens only inside, on a port of its own outside", () => {
    const { plan, problems } = planOf(form({ port: "6379", outside: "16379" }), ROWS, ACTIVE);

    expect(problems).toEqual({});
    expect(plan).toMatchObject({ mode: "relay", listen: 16379, target: { host: "127.0.0.1", port: 6379 } });
    expect(streamBody(form({ port: "6379", outside: "16379" }))).toMatchObject({ listen_port: 16379, target_port: 6379, target_host: "127.0.0.1", protocol: "tcp", sources: [] });
  });

  it("needs no active firewall to be a relay, which makes its own rule", () => {
    const { plan } = planOf(form({ port: "6379", outside: "16379" }), ROWS, { active: false, installed: true });

    expect(plan).toMatchObject({ mode: "relay" });
  });

  it("does not offer a rule while the firewall is off: with none filtering, every port that listens is open already", () => {
    const { plan, problems } = planOf(form({ port: "25565" }), ROWS, { active: false, installed: true });

    expect(plan).toBeNull();
    expect(problems).toEqual({ firewall: "off" });
  });

  it("does not offer a rule on a machine with no firewall installed", () => {
    expect(planOf(form({ port: "25565" }), ROWS, { active: false, installed: false }).problems).toEqual({ firewall: "missing" });
  });
});

describe("what is wrong with the form", () => {
  it("names the port when it is not a port", () => {
    expect(planOf(form({ port: "http" }), ROWS, ACTIVE).problems).toEqual({ port: "invalid" });
    expect(planOf(form({ port: "" }), ROWS, ACTIVE).problems).toEqual({ port: "invalid" });
  });

  it("names the address when 'only these addresses' is chosen and nothing, or a typo, is typed", () => {
    expect(planOf(form({ port: "5432", who: "some", source: "" }), ROWS, ACTIVE).problems).toEqual({ source: "needed" });
    expect(planOf(form({ port: "5432", who: "some", source: "10.0.0" }), ROWS, ACTIVE).problems).toEqual({ source: "invalid" });
  });

  it("explains that a rule takes one address or network, since each is a rule of its own", () => {
    expect(planOf(form({ port: "5432", who: "some", source: "10.0.0.1, 10.0.0.2" }), ROWS, ACTIVE).problems).toEqual({ source: "one" });
  });

  it("a relay takes the addresses as a list, up to twenty, and needs a port to answer on that is not the program's", () => {
    const many = Array.from({ length: 21 }, (_, index) => `10.0.0.${String(index + 1)}`).join(", ");

    expect(planOf(form({ port: "6379", outside: "16379", who: "some", source: "10.0.0.1, 10.0.0.2" }), ROWS, ACTIVE).problems).toEqual({});
    expect(planOf(form({ port: "6379", outside: "16379", who: "some", source: many }), ROWS, ACTIVE).problems).toEqual({ source: "many" });
    expect(planOf(form({ port: "6379", outside: "" }), ROWS, ACTIVE).problems).toEqual({ outside: "invalid" });
    expect(planOf(form({ port: "6379", outside: "6379" }), ROWS, ACTIVE).problems).toEqual({ outside: "same" });
  });

  it("a relay carries one protocol at a time: nginx proxies TCP or UDP, not both", () => {
    expect(planOf(form({ port: "6379", outside: "16379", proto: "any" }), ROWS, ACTIVE).problems).toEqual({ proto: "one" });
  });
});
