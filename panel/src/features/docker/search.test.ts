import { describe, expect, it } from "vitest";

import { validateDockerSearch } from "./search";
import { DOCKER_CONTAINERS_PATH, dockerContainersHref } from "../app/howItRuns";

describe("what a list of the Docker zone keeps in its address", () => {
  it("reads the filters a list sets and leaves out what it does not know", () => {
    expect(validateDockerSearch({ q: " web ", owner: "app", state: "problem", app: "shop.example.com", project: "shop-example-com", news: true })).toEqual({
      q: "web",
      owner: "app",
      state: "problem",
      app: "shop.example.com",
      project: "shop-example-com",
      news: true,
    });
    expect(validateDockerSearch({ owner: "robot", state: "sleeping", q: "", app: 3, news: "no" })).toEqual({});
  });

  it("reads the link an application's page draws, so the page lands on its containers", () => {
    const href = dockerContainersHref("shop.example.com");
    expect(href.startsWith(DOCKER_CONTAINERS_PATH)).toBe(true);
    const search = Object.fromEntries(new URLSearchParams(href.slice(href.indexOf("?") + 1)));
    expect(validateDockerSearch(search)).toEqual({ app: "shop.example.com" });
  });

  it("takes the shared form of a flag the address carries as text", () => {
    expect(validateDockerSearch({ news: "true" })).toEqual({ news: true });
  });
});
