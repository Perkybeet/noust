/**
 * Every language contributed as data, held to English on every `npm test` (checklist M-63): a
 * file that breaks a placeholder, names a key English does not have or is registered and missing
 * fails here, with the key and what to do, before it can reach a release. Today the registry is
 * empty and this proves the harness runs; the first contributed language is held by it from its
 * first commit.
 */

import { describe, expect, it, vi } from "vitest";

import { communityLoader } from "./catalogs";
import { auditRegistry, expand, flattenEnglish } from "./community";
import type { CommunityFile } from "./community";
import { COMMUNITY_LOCALES } from "./communityLocales";
import * as en from "./en";

const FILES = import.meta.glob<CommunityFile>("./community/*.json", { eager: true, import: "default" });
const ENGLISH = new Map(flattenEnglish({ ...en }));

describe("the contributed languages", () => {
  it("are registered, have their file, and hold everything English holds them to", () => {
    const problems = auditRegistry(COMMUNITY_LOCALES, FILES, ENGLISH);
    expect(problems.map((problem) => `${problem.key}: ${problem.message}`)).toEqual([]);
  });

  it("are held to the real catalogs, which have thousands of messages (the standard is not an empty one)", () => {
    expect(ENGLISH.size).toBeGreaterThan(2000);
    expect(ENGLISH.get("nav.apps.label")).toBe("Applications");
    expect(ENGLISH.get("time.duration.hoursMinutes")).toBe("{hours}h {minutes}m");
  });
});

describe("loading a language that is only a file", () => {
  const file = { "nav.apps.label": "Aplicações", "nav.failed": { one: "{count} falhou", other: "{count} falharam" } };

  it("is read from the file of its code as the nested catalog, nothing else", async () => {
    const load = vi.fn(() => Promise.resolve(file));
    const loader = communityLoader("pt", { "./community/pt.json": load });
    expect(loader).toBeDefined();
    expect(await loader?.()).toEqual(expand(file));
    expect(load).toHaveBeenCalledOnce();
  });

  it("has no loader when the registry names a language whose file is not there", () => {
    expect(communityLoader("fr", { "./community/pt.json": () => Promise.resolve(file) })).toBeUndefined();
  });
});
