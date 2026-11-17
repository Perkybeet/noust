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

describe("a function that is any of several", () => {
  it("is offered when one of them is available, and not when none is: a list means any of them", () => {
    const both = session(without("databases", "database_instances"));
    expect(offers(both, ["databases", "database_instances"])).toBe(false);
    // A machine that only has Docker has no engine of its own and does have the engines Noust runs in containers.
    expect(offers(session(without("databases")), ["databases", "database_instances"])).toBe(true);
    expect(offers(session(without("database_instances")), ["databases", "database_instances"])).toBe(true);
    expect(offers(session(without()), ["databases", "database_instances"])).toBe(true);
  });

  it("asks for nothing when the list is empty, and hides nothing where the session says nothing of functions", () => {
    expect(offers(session(without("databases")), [])).toBe(true);
    expect(offers(SESSION, ["databases", "database_instances"])).toBe(true);
    expect(offers(session(null), ["databases", "database_instances"])).toBe(true);
    expect(offers(undefined, ["databases", "database_instances"])).toBe(true);
  });

  it("is one answer with a list of one and with the key alone", () => {
    const hub = session(without("databases"));
    expect(offers(hub, ["databases"])).toBe(offers(hub, "databases"));
    expect(offers(hub, ["firewall"])).toBe(offers(hub, "firewall"));
  });

  it("gives the reason of the first function that has one, for the page that says why not", () => {
    const hub = session(without("databases", "database_instances"));
    expect(featureAnswer(hub, ["databases", "database_instances"])?.reason?.code).toBe("features.hub_role");
    expect(featureAnswer(session(without("databases")), ["databases", "database_instances"])?.available).toBe(true);
  });
});

describe("the page that turns on the machine's Docker", () => {
  it("is a function of the table like any other: offered where the table says it is available, never worked out from the role", () => {
    expect(offers(session(without()), "deploy_here")).toBe(true);
    expect(offers(session(without("deploy_here")), "deploy_here")).toBe(false);
    // The table says it for a hub that was not told to deploy as much as for one that was.
    expect(offers(session(without(), ""), "deploy_here")).toBe(true);
    expect(offers(session(without(), "containers"), "deploy_here")).toBe(true);
  });

  it("says the sentence the server says for a server, which has nothing to turn on", () => {
    const refusal = { code: "features.hub_only", params: { feature: "Deploying on this machine's Docker" }, text: "Deploying on this machine's Docker: only a central that is a hub has this, and this Noust is a server" };
    const table = new Proxy({}, { get: (_, key: string) => (key === "deploy_here" ? { available: false, reason: refusal } : { available: true, reason: null }) }) as never;
    expect(offers(session(table), "deploy_here")).toBe(false);
    expect(featureAnswer(session(table), "deploy_here")?.reason?.code).toBe("features.hub_only");
  });

  it("hides nothing before the session is read, nor where it says nothing of functions", () => {
    expect(offers(undefined, "deploy_here")).toBe(true);
    expect(offers(session(null), "deploy_here")).toBe(true);
  });

  it("says a central deploys on its machine's Docker only when the session says it was told to", () => {
    expect(isContainerHost(session(without(), "containers"))).toBe(true);
    expect(isContainerHost(session(without(), ""))).toBe(false);
    expect(isContainerHost(session(without()))).toBe(false);
    expect(isContainerHost(undefined)).toBe(false);
  });
});
