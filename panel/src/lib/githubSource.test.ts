import { describe, expect, it } from "vitest";
import { isGithubSource } from "./githubSource";

describe("isGithubSource", () => {
  it.each([
    "https://github.com/shop/storefront.git",
    "http://github.com/shop/storefront",
    "https://www.github.com/shop/storefront",
    "https://user@github.com/shop/storefront.git",
    "git@github.com:shop/storefront.git",
    "ssh://git@github.com/shop/storefront.git",
    "git://github.com/shop/storefront.git",
    "github:shop/storefront",
    "HTTPS://GITHUB.COM/shop/storefront",
  ])("says %s is a repository on GitHub", (source) => {
    expect(isGithubSource(source)).toBe(true);
  });

  it.each([
    "https://gitlab.com/shop/storefront.git",
    "https://example.com/github.com/shop/storefront",
    "https://evil.example/?next=github.com/shop",
    "https://github.com.evil.example/shop/storefront",
    "git@gitlab.com:shop/github.com/storefront.git",
    "/srv/github.com/shop",
    "",
  ])("says %s is not", (source) => {
    expect(isGithubSource(source)).toBe(false);
  });

  it("says nothing of an application with no source", () => {
    expect(isGithubSource(null)).toBe(false);
    expect(isGithubSource(undefined)).toBe(false);
  });
});
