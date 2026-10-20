import { describe, expect, it } from "vitest";

import { COMMIT, DOMAIN, GIT_TAG, IMAGE_TAG, lacking, parseFailures, parseSerial, rolloutBody, sources, syncView } from "./placements";
import type { PlacementServer } from "./placements";

const BASE = { what: "head", value: "", force: false, rollback: true, serial: "", failures: "", canary: "", plan: true } as const;

function server(node: string, extra: Partial<PlacementServer>): PlacementServer {
  return { node, present: true, reachable: true, sync: "current", ...extra } as PlacementServer;
}

describe("the batch size of a rollout", () => {
  it("takes a count, a percentage or nothing, and nothing else", () => {
    expect(parseSerial("")).toBeNull();
    expect(parseSerial(" 2 ")).toBe(2);
    expect(parseSerial("50%")).toBe("50%");
    expect(parseSerial("100%")).toBe("100%");
    for (const bad of ["0", "0%", "101%", "-1", "1.5", "two", "10 %", "%", "99999"]) expect(parseSerial(bad), bad).toBeUndefined();
  });

  it("takes a count of failures or nothing", () => {
    expect(parseFailures("")).toBeNull();
    expect(parseFailures("0")).toBe(0);
    expect(parseFailures("3")).toBe(3);
    for (const bad of ["-1", "1.5", "x", "12345"]) expect(parseFailures(bad), bad).toBeUndefined();
  });
});

describe("what a rollout deploys", () => {
  it("is the head of the branch with nothing named, and says so by naming nothing", () => {
    const body = rolloutBody({ ...BASE });
    expect(body).toEqual({ force: false, build_on_each: false, rollback_on_failure: true, serial: null, max_failures: null, canary: null, plan: true });
    expect(body).not.toHaveProperty("commit");
    expect(body).not.toHaveProperty("image_tag");
  });

  it("follows the application's build server unless each server is asked to build", () => {
    expect(rolloutBody({ ...BASE })).toMatchObject({ build_on_each: false });
    expect(rolloutBody({ ...BASE, buildOnEach: true })).toMatchObject({ build_on_each: true });
  });

  it("lowercases a commit and refuses what is not one", () => {
    expect(rolloutBody({ ...BASE, what: "commit", value: " ABC1234 " })).toMatchObject({ commit: "abc1234" });
    for (const bad of ["", "abc12", "zzzzzzz", "abc1234;rm", "a".repeat(41)]) expect(rolloutBody({ ...BASE, what: "commit", value: bad }), bad).toBeNull();
  });

  it("refuses a git tag that climbs out of the refs or starts like an option", () => {
    expect(rolloutBody({ ...BASE, what: "tag", value: "release/1.4" })).toMatchObject({ tag: "release/1.4" });
    for (const bad of ["", "-f", "a..b", "a b", "a;b"]) expect(rolloutBody({ ...BASE, what: "tag", value: bad }), bad).toBeNull();
  });

  it("takes an image tag and refuses a digest, a registry or a space", () => {
    expect(rolloutBody({ ...BASE, what: "image", value: "1.4.2" })).toMatchObject({ image_tag: "1.4.2" });
    for (const bad of ["", "-1", "a b", "repo/img:1", "sha256:abc"]) expect(rolloutBody({ ...BASE, what: "image", value: bad }), bad).toBeNull();
  });

  it("is null when the batch size or the failures are not valid, so nothing is sent", () => {
    expect(rolloutBody({ ...BASE, serial: "0" })).toBeNull();
    expect(rolloutBody({ ...BASE, failures: "x" })).toBeNull();
    expect(rolloutBody({ ...BASE, serial: "25%", failures: "2", canary: "web-2", force: true, rollback: false, plan: false })).toEqual({
      force: true,
      build_on_each: false,
      rollback_on_failure: false,
      serial: "25%",
      max_failures: 2,
      canary: "web-2",
      plan: false,
    });
  });
});

describe("the patterns that judge what a person types", () => {
  it("are anchored whole, so a trailing newline or a suffix does not pass", () => {
    expect(DOMAIN.test("shop.example.com")).toBe(true);
    for (const bad of ["shop.example.com\n", "Shop.example.com", "https://shop.example.com", "shop_example.com", "", "-a.com"]) expect(DOMAIN.test(bad), JSON.stringify(bad)).toBe(false);
    expect(COMMIT.test("abc1234\n")).toBe(false);
    expect(IMAGE_TAG.test("1.4\n")).toBe(false);
    expect(GIT_TAG.test("v1\n")).toBe(false);
  });
});

describe("reading the servers of a placement", () => {
  const servers = [server("web-1", {}), server("web-2", { sync: "missing", present: false }), server("web-3", { reachable: false, sync: "unreachable" })];

  it("finds where a copy goes and where it can come from", () => {
    expect(lacking(servers)).toEqual(["web-2"]);
    expect(sources(servers)).toEqual(["web-1"]);
  });

  it("reads a word it does not know as unknown, never as fine", () => {
    expect(syncView("current").state).toBe("running");
    expect(syncView("missing").state).toBe("failed");
    expect(syncView("something-new").state).toBe("unknown");
  });
});
