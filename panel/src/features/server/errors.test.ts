import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import { bindT } from "../../i18n/useT";
import { HOST_ACCESS_COMMAND, explainServerError, isHostAccessRefusal, refusalList } from "./errors";

const t = bindT("en");

const HOST_ACCESS = new ApiError(403, "permission_denied", "This needs the 'server.host_access' permission, which admin does not hold", "Ask a security officer for an account with a role that holds it.");

describe("the Server area's refusals", () => {
  it("tells a node's host-access refusal from any other permission refusal", () => {
    expect(isHostAccessRefusal(HOST_ACCESS, "web-2")).toBe(true);
    // On this server it is an ordinary permission the operator's role lacks.
    expect(isHostAccessRefusal(HOST_ACCESS, null)).toBe(false);
    expect(isHostAccessRefusal(new ApiError(403, "permission_denied", "This needs the 'server.manage' permission"), "web-2")).toBe(false);
  });

  it("gives a host-access refusal the node's own command as its fix, keeping the node's words", () => {
    const explained = explainServerError(t, HOST_ACCESS, "web-2") as ApiError;
    expect(explained.hint).toBe(`On web-2, run ${HOST_ACCESS_COMMAND} to let this console change how it is reached.`);
    expect(explained.detail).toBe(HOST_ACCESS.detail);
  });

  it("puts a refusal's list under its detail, verbatim", () => {
    const busy = new ApiError(409, "preflight_failed", "An update cannot start now", "Wait for what is running to finish, or free some space.", null, null, null, null, {
      blockers: ["Deploying shop.example.com", "Only 1.2 GB free on /"],
    });
    expect(refusalList(busy)).toEqual(["Deploying shop.example.com", "Only 1.2 GB free on /"]);
    const explained = explainServerError(t, busy, null) as ApiError;
    expect(explained.detail).toBe("An update cannot start now\n- Deploying shop.example.com\n- Only 1.2 GB free on /");
    expect(explained.hint).toBe(busy.hint);
    const removals = new ApiError(409, "confirmation_required", "This update would remove 1 package(s)", null, null, null, null, null, { required: { removals: ["libfoo1"] } });
    expect(refusalList(removals)).toEqual(["libfoo1"]);
  });

  it("leaves anything else as it is", () => {
    const other = new ApiError(500, "internal", "boom");
    expect(explainServerError(t, other, null)).toBe(other);
  });
});
