import { describe, expect, it } from "vitest";

import { deployKeysPage, sshRepository } from "./sshSource";

describe("an SSH source", () => {
  it("is read in both spellings, without .git and without a slash at either end", () => {
    expect(sshRepository("git@github.com:acme/api.git")).toEqual({ host: "github.com", path: "acme/api" });
    expect(sshRepository("  git@GitHub.com:acme/api  ")).toEqual({ host: "github.com", path: "acme/api" });
    expect(sshRepository("ssh://git@github.com/acme/api.git")).toEqual({ host: "github.com", path: "acme/api" });
    expect(sshRepository("ssh://git@git.example.org:2222/team/sub/api.git/")).toEqual({ host: "git.example.org", path: "team/sub/api" });
  });

  it("is not read from what is not an SSH address", () => {
    for (const value of ["", "https://github.com/acme/api.git", "github:acme/api", "/srv/app", "git@github.com:", "git@github.com:/abs/path", "acme/api"]) {
      expect(sshRepository(value), value).toBeNull();
    }
  });

  it("finds the deploy keys of GitHub and GitLab, and nobody else's", () => {
    expect(deployKeysPage({ host: "github.com", path: "acme/api" })).toEqual({ host: "GitHub", href: "https://github.com/acme/api/settings/keys/new" });
    expect(deployKeysPage({ host: "gitlab.com", path: "group/sub/api" })).toEqual({ host: "GitLab", href: "https://gitlab.com/group/sub/api/-/settings/repository" });
    expect(deployKeysPage({ host: "git.example.org", path: "team/api" })).toBeNull();
    // GitHub repositories are owner/name: anything deeper is not one.
    expect(deployKeysPage({ host: "github.com", path: "acme/api/extra" })).toBeNull();
  });
});
