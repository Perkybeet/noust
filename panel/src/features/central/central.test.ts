import { describe, expect, it } from "vitest";

import { SESSION } from "../../test/fakes";
import type { SessionInfo } from "../../api/queries/auth";
import { ApiError, ElevationCancelledError } from "../../api/client";
import { PLAIN_SERVER, centralOf, hubAreaOf, hubRedirect, isHubRoleError, isLocalOnlyPath, isWrongPassphrase, parseHubArea } from "./central";

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

describe("a hub's pages", () => {
  it("knows which pages are about this machine's own deployments", () => {
    for (const path of ["/apps", "/apps/new", "/apps/shop.example.com/logs", "/databases", "/services/x", "/cron", "/domains", "/backups"]) {
      expect(isLocalOnlyPath(path)).toBe(true);
    }
    for (const path of ["/", "/fleet", "/settings/servers", "/activity", "/server", "/applications"]) {
      expect(isLocalOnlyPath(path)).toBe(false);
    }
    expect(hubAreaOf("/apps/shop.example.com")).toBe("apps");
    expect(hubAreaOf("/settings")).toBeNull();
  });

  it("sends a hub's overview and deployment pages to the fleet, and nothing on a server or a node", () => {
    const hub = withCentral({ role: "hub", sealed: false, locked: false });
    expect(hubRedirect(hub, "/", null)).toEqual({ hub: "overview" });
    expect(hubRedirect(hub, "/apps/new", null)).toEqual({ hub: "apps" });
    expect(hubRedirect(hub, "/settings/security", null)).toBeNull();
    // `/n/web-2/apps` is web-2's page: the router reads it as /apps with node web-2.
    expect(hubRedirect(hub, "/apps", "web-2")).toBeNull();
    expect(hubRedirect(SESSION, "/apps", null)).toBeNull();
  });

  it("reads the fleet's `hub` search back, ignoring anything else", () => {
    expect(parseHubArea("databases")).toBe("databases");
    expect(parseHubArea("overview")).toBe("overview");
    expect(parseHubArea("settings")).toBeUndefined();
    expect(parseHubArea(3)).toBeUndefined();
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
