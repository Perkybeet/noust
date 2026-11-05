import { describe, expect, it } from "vitest";

import { REDIRECT_ROWS } from "../../../app/redirects";
import { CONFIG_PAGES, PAGE_PARTS, SUBSECTION_PAGE, partsOf } from "./sections";
import type { ConfigPageId } from "./sections";

describe("the four pages that gather the subsections of an application's settings", () => {
  it("are the four the column lists, and each part is on exactly one of them", () => {
    expect(CONFIG_PAGES).toEqual(["code", "triggers", "resources", "access"]);
    const ids = CONFIG_PAGES.flatMap((page) => PAGE_PARTS[page].map((part) => part.id));
    expect(new Set(ids).size, "a part is on two pages").toBe(ids.length);
  });

  it("keep every subsection of 4.1: none was dropped when they were gathered", () => {
    // The seventeen of 4.1's list, General (now the first part of Resources) and Export and Delete, which are pages of their own.
    const was = ["source", "build", "container", "deploys", "automation", "compose", "deploy-on-push", "hooks", "previews", "resources", "processes", "mounts", "disk", "security", "access", "protection"];
    const now = CONFIG_PAGES.flatMap((page) => PAGE_PARTS[page].map((part) => part.id));
    for (const id of was) expect(now, id).toContain(id);
    expect(now).toContain("general");
  });

  it("leave out of a page what the application does not concern", () => {
    const app = (type: string, extra: Record<string, unknown> = {}) => ({ app_type: type, unit: "x.service", ...extra }) as never;
    const ids = (page: ConfigPageId, type: string) => partsOf(page, app(type)).map((part) => part.id);
    expect(ids("code", "nextjs")).toEqual(["source", "build"]);
    expect(ids("code", "container")).toEqual(["source", "build", "container"]);
    expect(ids("triggers", "nextjs")).not.toContain("compose");
    expect(ids("triggers", "docker-compose")).toContain("compose");
    // Nothing of what Noust builds and deploys is offered for a site it adopted.
    expect(ids("code", "adopted")).toEqual([]);
    expect(ids("access", "adopted")).toEqual([]);
  });

  it("have an address of 4.1 that goes to each, and the table of redirects says so", () => {
    for (const [old, page] of Object.entries(SUBSECTION_PAGE)) {
      // General was the list's first page, at the address of the list itself.
      const from = old === "general" ? "/apps/$domain/settings" : `/apps/$domain/settings/${old}`;
      const row = REDIRECT_ROWS.find((candidate) => candidate.from === from);
      expect(row?.to, from).toBe(`/apps/$domain/${page}`);
    }
  });
});
