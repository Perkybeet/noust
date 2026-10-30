import { describe, expect, it } from "vitest";

import { forServer, leaseFor, repositoryOf } from "./sourceLease";

describe("the repository of a source the list gave", () => {
  it("is owner/repo of github:owner/repo, and nothing for any other source", () => {
    expect(repositoryOf("github:acme/shop")).toBe("acme/shop");
    expect(repositoryOf(" github:acme/shop.api ")).toBe("acme/shop.api");
    expect(repositoryOf("https://github.com/acme/shop.git")).toBeNull();
    expect(repositoryOf("/srv/shop")).toBeNull();
    expect(repositoryOf("github:acme")).toBeNull();
  });
});

describe("leasing before the first application of a repository on a server of the fleet", () => {
  it("asks the central to lease this server this repository, and nothing else", () => {
    expect(leaseFor("web-2", "github:acme/shop", true)).toEqual({ force: false, nodes: ["web-2"], repositories: ["acme/shop"] });
  });

  it("asks nothing for the central's own server, which clones with its App directly", () => {
    expect(leaseFor(null, "github:acme/shop", true)).toBeNull();
  });

  it("asks nothing for a source that the App did not list: a typed address is cloned with what the operator gave", () => {
    expect(leaseFor("web-2", "github:acme/shop", false)).toBeNull();
    expect(leaseFor("web-2", "https://git.example.com/acme/shop.git", true)).toBeNull();
  });
});

describe("a request for a server of the fleet", () => {
  const body = { source: "github:acme/shop", github_installation_id: 77 };

  it("loses the central's installation number: the server clones with its lease, by repository", () => {
    expect(forServer(body, "web-2", true)).toEqual({ source: "github:acme/shop" });
  });

  it("keeps it for the central's own server and for a repository that was not listed", () => {
    expect(forServer(body, null, true)).toBe(body);
    expect(forServer(body, "web-2", false)).toBe(body);
  });

  it("leaves a request without one as it is", () => {
    const plain = { source: "https://git.example.com/acme/shop.git" };
    expect(forServer(plain, "web-2", true)).toBe(plain);
  });
});
