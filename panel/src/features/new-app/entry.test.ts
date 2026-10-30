import { describe, expect, it } from "vitest";

import { classifyEntry, withScheme } from "./wizard";

describe("what the one box holds (item 99)", () => {
  it("says nothing for what is empty or blank", () => {
    expect(classifyEntry("")).toBe("empty");
    expect(classifyEntry("   ")).toBe("empty");
  });

  it.each([
    "https://github.com/you/app.git",
    "https://git.example.com/group/project",
    "git@github.com:you/app.git",
    "ssh://deploy@git.example.com:2222/group/project.git",
    "github:you/app",
    "/srv/app",
    "./app",
    "~/code/app",
    "https://example.com/release.tar.gz",
    "github.com/you/app",
    "gitlab.com/group/app",
  ])("reads %s as code to fetch", (value) => {
    expect(classifyEntry(value)).toBe("source");
  });

  it.each(["ghcr.io/owner/app:1.4", "nginx:1.27", "owner/app", "registry.example.com:5000/team/app:2", "docker.io/library/redis@sha256:" + "a".repeat(64)])(
    "reads %s as an image of a registry",
    (value) => {
      expect(classifyEntry(value)).toBe("image");
    },
  );

  it.each(["redis", "ghost", "uptime kuma", "a blog", "wordpress"])("reads %s as words to look for in the catalog", (value) => {
    expect(classifyEntry(value)).toBe("search");
  });

  it("does not take a sentence with a slash in it for an image", () => {
    expect(classifyEntry("a/b testing tool")).toBe("search");
  });

  it("does not take upper case for a registry's name: a registry never has it", () => {
    expect(classifyEntry("Owner/App")).toBe("search");
  });

  it("ignores the spaces around what was pasted", () => {
    expect(classifyEntry("  https://github.com/you/app.git \n")).toBe("source");
  });
});

describe("a source typed without its scheme", () => {
  it("is given https, for the hosts people copy it from", () => {
    expect(withScheme("github.com/you/app")).toBe("https://github.com/you/app");
    expect(withScheme(" www.github.com/you/app ")).toBe("https://www.github.com/you/app");
  });

  it("leaves anything else as it is", () => {
    expect(withScheme("https://github.com/you/app")).toBe("https://github.com/you/app");
    expect(withScheme("/srv/app")).toBe("/srv/app");
    expect(withScheme("ghcr.io/owner/app:1")).toBe("ghcr.io/owner/app:1");
  });
});
