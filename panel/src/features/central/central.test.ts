import { describe, expect, it } from "vitest";

import { SESSION } from "../../test/fakes";
import type { SessionInfo } from "../../api/queries/auth";
import { ApiError, ElevationCancelledError } from "../../api/client";
import { PLAIN_SERVER, centralOf, isHubRoleError, isWrongPassphrase, startsOnFleet } from "./central";

function withCentral(central: NonNullable<SessionInfo["central"]> | null): SessionInfo {
  return { ...SESSION, central };
}

describe("centralOf", () => {
  it("reads the role and the seal from the session", () => {
    expect(centralOf(withCentral({ role: "hub", sealed: true, locked: true }))).toEqual({ role: "hub", sealed: true, locked: true });
  });

  it("is a plain server when the backend says nothing, or something it does not know", () => {
    expect(centralOf(SESSION)).toEqual(PLAIN_SERVER);
    expect(centralOf(undefined)).toEqual(PLAIN_SERVER);
    expect(centralOf(withCentral(null))).toEqual(PLAIN_SERVER);
    expect(centralOf(withCentral({ role: "galaxy", sealed: false, locked: false }))).toEqual(PLAIN_SERVER);
  });
});

describe("where a hub starts", () => {
  const hub = withCentral({ role: "hub", sealed: false, locked: false });

  it("starts a hub on All servers when nothing has been chosen in this tab: it has no server of its own to begin on", () => {
    expect(startsOnFleet(hub, "/", null, false)).toBe(true);
  });

  it("leaves everything else where it is: a hub whose operator chose something, a page that is not the start, a node, and any other server", () => {
    // The hub itself chosen in the selector is a choice: its home is its home.
    expect(startsOnFleet(hub, "/", null, true)).toBe(false);
    // Nothing a hub does not have is sent to the fleet any more: the page says why it is not there (FeatureGate).
    for (const path of ["/apps", "/databases", "/cron", "/domains", "/backups", "/apps/new", "/settings/security", "/catalog"]) {
      expect(startsOnFleet(hub, path, null, false)).toBe(false);
    }
    expect(startsOnFleet(hub, "/", "web-2", false)).toBe(false);
    expect(startsOnFleet(SESSION, "/", null, false)).toBe(false);
  });
});

describe("the central's own refusals", () => {
  it("tells a wrong passphrase from sudo mode asked for or declined", () => {
    expect(isWrongPassphrase(new ApiError(403, "wrong_passphrase", "The passphrase does not open the sealed secrets"))).toBe(true);
    expect(isWrongPassphrase(new ApiError(403, "elevation_required", "Confirm it's you"))).toBe(false);
    expect(isWrongPassphrase(new ElevationCancelledError())).toBe(false);
    expect(isWrongPassphrase(new ApiError(409, "sealerror", "Not sealed"))).toBe(false);
  });

  it("knows a hub's refusal", () => {
    expect(isHubRoleError(new ApiError(409, "hub_role", "Not available on this central, which is a hub"))).toBe(true);
    expect(isHubRoleError(new ApiError(409, "conflict", "Busy"))).toBe(false);
  });
});
