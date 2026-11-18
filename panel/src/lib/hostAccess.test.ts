import { describe, expect, it } from "vitest";

import { ApiError } from "../api/errors";
import { hostAccessOf } from "./hostAccess";

const REFUSAL = new ApiError(409, "adoptionrefusederror", "Adopting shop puts under Noust a stack whose containers have what is on this server", "hint", null, null, null, null, {
  host_access: ["service app mounts the Docker socket", "service app is privileged"],
  coded: { code: "compose_guard.adopt_host_access", params: { domain: "shop", lines: "- a", command: "noust app adopt shop" } },
});

describe("the lines a refusal lists", () => {
  it("reads them from the field of the 409, with the sentence the server said of them", () => {
    const found = hostAccessOf(REFUSAL);
    expect(found?.lines).toEqual(["service app mounts the Docker socket", "service app is privileged"]);
    expect(found?.coded?.code).toBe("compose_guard.adopt_host_access");
    expect(found?.detail).toContain("Adopting shop");
  });

  it("is not any other failure: another status, no field, an empty list or lines that are not text", () => {
    expect(hostAccessOf(new ApiError(500, "internal", "x", null, null, null, null, null, { host_access: ["a"] }))).toBeNull();
    expect(hostAccessOf(new ApiError(409, "conflict", "x"))).toBeNull();
    expect(hostAccessOf(new ApiError(409, "conflict", "x", null, null, null, null, null, { host_access: [] }))).toBeNull();
    expect(hostAccessOf(new ApiError(409, "conflict", "x", null, null, null, null, null, { host_access: [3, " "] }))).toBeNull();
    expect(hostAccessOf(new Error("service app is privileged"))).toBeNull();
  });
});
