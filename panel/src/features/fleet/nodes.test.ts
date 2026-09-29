import { describe, expect, it } from "vitest";

import { NODE_NAME, centralNameFrom, nodeStatus, sshAddress, testResultOf, tunnelOf } from "./nodes";

describe("a node as the central reports it", () => {
  it("reads the tunnel's open dictionary field by field", () => {
    expect(tunnelOf({ tunnel: { open: true, local_port: 40123, since: "2026-09-29T08:00:00Z", last_error: null, failures: 0 } })).toEqual({
      open: true,
      localPort: 40123,
      since: "2026-09-29T08:00:00Z",
      lastError: null,
      failures: 0,
    });
    expect(tunnelOf({})).toEqual({ open: false, localPort: null, since: null, lastError: null, failures: 0 });
    expect(tunnelOf({ tunnel: { last_error: "ssh: connect to host web2 port 22: Connection refused\n", failures: 3 } }).lastError).toBe(
      "ssh: connect to host web2 port 22: Connection refused\n",
    );
  });

  it("knows the four statuses and nothing else", () => {
    expect(nodeStatus({ status: "reachable" })).toBe("reachable");
    expect(nodeStatus({ status: "refused" })).toBe("refused");
    expect(nodeStatus({ status: "on fire" })).toBe("unknown");
  });

  it("writes the SSH address the way it is typed, leaving out the default port", () => {
    expect(sshAddress({ ssh_user: "root", ssh_host: "web2.example.com", ssh_port: 22 })).toBe("root@web2.example.com");
    expect(sshAddress({ ssh_user: "deploy", ssh_host: "203.0.113.7", ssh_port: 2222 })).toBe("deploy@203.0.113.7:2222");
    expect(sshAddress({ ssh_user: "root", ssh_host: "2001:db8::7", ssh_port: 2222 })).toBe("root@[2001:db8::7]:2222");
  });

  it("reads a test's result, keeping ssh's words verbatim", () => {
    expect(
      testResultOf({ reachable: false, status: "unreachable", version: "3.0.0", latency_ms: null, error: "The tunnel did not open", details: "Permission denied (publickey)." }),
    ).toEqual({
      reachable: false,
      status: "unreachable",
      version: "3.0.0",
      latencyMs: null,
      error: "The tunnel did not open",
      details: "Permission denied (publickey).",
    });
  });

  it("accepts the names the central accepts", () => {
    expect(NODE_NAME.test("web-2")).toBe(true);
    expect(NODE_NAME.test("a")).toBe(true);
    expect(NODE_NAME.test("Web-2")).toBe(false);
    expect(NODE_NAME.test("-web")).toBe(false);
    expect(NODE_NAME.test("x".repeat(33))).toBe(false);
  });

  it("finds the name the central gives itself in the command that authorizes it", () => {
    expect(centralNameFrom("noust fleet authorize --central-key 'ssh-ed25519 AAAA noust-central@nas' --name nas")).toBe("nas");
    expect(centralNameFrom("something else")).toBeNull();
  });
});
