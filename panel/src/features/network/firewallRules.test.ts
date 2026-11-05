import { describe, expect, it } from "vitest";

import type { FirewallRule, PortClaim } from "../server/queries";
import { portRows } from "../server/network/data";
import { isProtected, ruleMaker, rulePorts, ruleSubject } from "./firewallRules";

const rule = (over: Partial<FirewallRule>): FirewallRule => ({ id: "1", backend: "ufw", comment: "", known: true, noust: false, ports: [[22, 22]], proto: "tcp", service: null, source: "any", spec: "", action: "allow", ...over });
const claim = (over: Partial<PortClaim> & Pick<PortClaim, "port" | "protocol" | "kind" | "owner">): PortClaim => ({ address: null, detail: "", processes: [], ...over });

const ROWS = portRows(
  [
    claim({ port: 5432, protocol: "tcp", kind: "database", owner: "postgresql" }),
    claim({ port: 2222, protocol: "tcp", kind: "stack", owner: "git.example.com" }),
    claim({ port: 25565, protocol: "tcp", kind: "process", owner: "java", address: "0.0.0.0", processes: ["java"] }),
  ],
  [],
);

describe("the ports of a rule", () => {
  it("are written one by one and as ranges, and a rule with no port is every port", () => {
    expect(rulePorts(rule({ ports: [[80, 80], [443, 443]] }))).toBe("80, 443");
    expect(rulePorts(rule({ ports: [[8000, 8100]] }))).toBe("8000:8100");
    expect(rulePorts(rule({ ports: [] }))).toBe("*");
  });
});

describe("what a rule is for", () => {
  it("is the service the firewall names, then what holds the port in Noust, then nothing", () => {
    expect(ruleSubject(rule({ service: "OpenSSH", ports: [[22, 22]] }), ROWS)).toEqual({ kind: "service", name: "OpenSSH" });
    expect(ruleSubject(rule({ ports: [[5432, 5432]] }), ROWS)).toMatchObject({ kind: "holder", holder: { kind: "database", owner: "postgresql" } });
    expect(ruleSubject(rule({ ports: [[25565, 25565]] }), ROWS)).toMatchObject({ kind: "holder", holder: { kind: "process", owner: "java" } });
    expect(ruleSubject(rule({ ports: [[9999, 9999]] }), ROWS)).toEqual({ kind: "none" });
  });
});

describe("who made a rule", () => {
  it("is a published port when Noust made it for one, with the port's name", () => {
    expect(ruleMaker(rule({ noust: true, comment: "noust: noust stream minecraft" }))).toEqual({ kind: "stream", name: "minecraft" });
  });

  it("is a database's Connect section when Noust made it to open the database", () => {
    expect(ruleMaker(rule({ noust: true, comment: "noust: db postgresql tienda_prod 5432" }))).toEqual({ kind: "database", engine: "postgresql", name: "tienda_prod" });
  });

  it("is Noust DNS when it opened the name server", () => {
    expect(ruleMaker(rule({ noust: true, comment: "noust: noust dns" }))).toEqual({ kind: "dns" });
  });

  it("is Noust, with what it wrote, for any other rule of its own", () => {
    expect(ruleMaker(rule({ noust: true, comment: "noust: opened by hand" }))).toEqual({ kind: "noust", note: "opened by hand" });
    expect(ruleMaker(rule({ noust: true, comment: "noust:" }))).toEqual({ kind: "noust", note: "" });
  });

  it("is somebody else for a rule Noust did not make, with their note when it has one", () => {
    expect(ruleMaker(rule({ comment: "office vpn" }))).toEqual({ kind: "other", note: "office vpn" });
    expect(ruleMaker(rule({}))).toEqual({ kind: "other", note: "" });
  });
});

describe("a port Noust keeps open", () => {
  it("is protected when one of the guard's ports is in the rule", () => {
    const guard = [{ port: 22, reason: "SSH" }];

    expect(isProtected(rule({ ports: [[22, 22]] }), guard)?.reason).toBe("SSH");
    expect(isProtected(rule({ ports: [[20, 30]] }), guard)?.reason).toBe("SSH");
    expect(isProtected(rule({ ports: [[80, 80]] }), guard)).toBeNull();
  });
});
