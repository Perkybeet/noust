import { describe, expect, it } from "vitest";

import type { SessionInfo } from "../../api/queries/auth";
import { SESSION } from "../../test/fakes";
import { featureAnswer, isContainerHost, offers } from "./features";

const REASON = { code: "features.hub_role", params: { feature: "Databases", command: "noust config set central.role server" }, text: "Databases: not available on this central, which is a hub" };

function session(features: NonNullable<NonNullable<SessionInfo["central"]>["features"]> | null | undefined, deployHere?: string): SessionInfo {
  return { ...SESSION, central: { role: "hub", sealed: false, locked: false, ...(deployHere !== undefined ? { deploy_here: deployHere } : {}), ...(features !== undefined ? { features } : {}) } };
}

/** A table where every function but the ones named works. */
function without(...missing: string[]): NonNullable<NonNullable<SessionInfo["central"]>["features"]> {
  const everything = new Proxy({}, {
    get: (_, key: string) => (missing.includes(key) ? { available: false, reason: REASON } : { available: true, reason: null }),
  });
  return everything as never;
}

describe("offers", () => {
  it("offers what asks for nothing, and what a server that publishes no table did always", () => {
    expect(offers(session(without("databases")), undefined)).toBe(true);
    expect(offers(SESSION, "databases")).toBe(true);
    expect(offers(session(null), "databases")).toBe(true);
    expect(offers(session(undefined), "databases")).toBe(true);
    // Before the session is read nothing is hidden: the pages behind sign-in only draw after it.
    expect(offers(undefined, "databases")).toBe(true);
  });

  it("does not offer what the table says is not available, and offers the rest", () => {
    const hub = session(without("databases", "scheduled_jobs"));
    expect(offers(hub, "databases")).toBe(false);
    expect(offers(hub, "scheduled_jobs")).toBe(false);
    expect(offers(hub, "firewall")).toBe(true);
  });

  it("is not a reason to hide anything that a key of the table does not know", () => {
    const states = { applications: { available: true, reason: null } } as never;
    expect(offers(session(states), "databases")).toBe(true);
  });
});

describe("featureAnswer", () => {
  it("gives the sentence the server says for why not", () => {
    const hub = session(without("databases"));
    expect(featureAnswer(hub, "databases")?.reason?.code).toBe("features.hub_role");
    expect(featureAnswer(hub, "firewall")?.reason).toBeNull();
    expect(featureAnswer(SESSION, "databases")).toBeNull();
  });
});

describe("the page that turns on the machine's Docker", () => {
  const hubReason = { ...REASON, code: "features.hub_role" };
  const refused = (code: string): NonNullable<NonNullable<SessionInfo["central"]>["features"]> =>
    new Proxy({}, { get: (_, key: string) => (key === "applications" ? { available: false, reason: { ...hubReason, code } } : { available: true, reason: null }) }) as never;

  it("is offered to a hub that does not deploy yet, because the table says the one thing in the way is that it is a hub", () => {
    expect(offers(session(refused("features.hub_role")), "deploy_here")).toBe(true);
  });

  it("is offered to a central that deploys already: it is where the machine's Docker is checked again", () => {
    expect(offers(session(without(), "containers"), "deploy_here")).toBe(true);
  });

  it("is not offered to a server, which deploys without being asked", () => {
    expect(offers(session(without()), "deploy_here")).toBe(false);
    expect(offers(SESSION, "deploy_here")).toBe(false);
  });

  it("is not offered where applications are refused for another reason than being a hub, which turning on Docker would not change", () => {
    expect(offers(session(refused("features.needs_systemd")), "deploy_here")).toBe(false);
  });

  it("is not offered by a server that publishes no table, and hides nothing before the session is read", () => {
    expect(offers(session(null), "deploy_here")).toBe(false);
    expect(offers(session(undefined), "deploy_here")).toBe(false);
    expect(offers(undefined, "deploy_here")).toBe(true);
  });

  it("says a central deploys on its machine's Docker only when the session says it was told to", () => {
    expect(isContainerHost(session(without(), "containers"))).toBe(true);
    expect(isContainerHost(session(without(), ""))).toBe(false);
    expect(isContainerHost(session(without()))).toBe(false);
    expect(isContainerHost(undefined)).toBe(false);
  });
});
