import { describe, expect, it } from "vitest";

import { ApiError } from "../api/errors";
import { acceptedLines, hostAccessOf } from "./hostAccess";

const REFUSAL = new ApiError(409, "adoptionrefusederror", "Adopting shop puts under Noust a stack whose containers have what is on this server", "hint", null, null, null, null, {
  host_access: ["service app mounts the Docker socket", "service app is privileged"],
  host_access_digest: "9f2c",
  coded: { code: "compose_guard.adopt_host_access", params: { domain: "shop", lines: "- a", command: "noust app adopt shop" } },
});

describe("the lines a refusal lists", () => {
  it("reads them from the field of the 409, with the sentence the server said of them", () => {
    const found = hostAccessOf(REFUSAL);
    expect(found?.lines).toEqual(["service app mounts the Docker socket", "service app is privileged"]);
    expect(found?.coded?.code).toBe("compose_guard.adopt_host_access");
    expect(found?.detail).toContain("Adopting shop");
  });

  it("reads which lines they are, so that accepting says it back", () => {
    expect(hostAccessOf(REFUSAL)?.digest).toBe("9f2c");
    expect(hostAccessOf(new ApiError(409, "conflict", "x", null, null, null, null, null, { host_access: ["a"] }))?.digest).toBe("");
  });

  it("is not any other failure: another status, no field, an empty list or lines that are not text", () => {
    expect(hostAccessOf(new ApiError(500, "internal", "x", null, null, null, null, null, { host_access: ["a"] }))).toBeNull();
    expect(hostAccessOf(new ApiError(409, "conflict", "x"))).toBeNull();
    expect(hostAccessOf(new ApiError(409, "conflict", "x", null, null, null, null, null, { host_access: [] }))).toBeNull();
    expect(hostAccessOf(new ApiError(409, "conflict", "x", null, null, null, null, null, { host_access: [3, " "] }))).toBeNull();
    expect(hostAccessOf(new Error("service app is privileged"))).toBeNull();
  });
});

describe("what accepting the lines says of them", () => {
  const access = hostAccessOf(REFUSAL);

  it("is the digest of the lines on screen, once the box is ticked", () => {
    expect(acceptedLines(access, true)).toEqual({ host_access_digest: "9f2c" });
  });

  it("is the digest of nothing while the box is not ticked, or when no lines were listed", () => {
    expect(acceptedLines(access, false)).toEqual({ host_access_digest: "" });
    expect(acceptedLines(null, true)).toEqual({ host_access_digest: "" });
  });
});
