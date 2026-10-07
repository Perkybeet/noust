import { describe, expect, it } from "vitest";

import { MAX_EXCLUSIONS, coversEveryDatabase, exclusionKinds, exclusionText, leftOutLines, parseNames } from "./scope";

describe("what a policy can leave out", () => {
  it("is schemas and tables on PostgreSQL, tables on MySQL, collections on MongoDB and nothing on Redis", () => {
    expect(exclusionKinds("postgresql", "database")).toEqual(["schemas", "tables"]);
    expect(exclusionKinds("mysql", "database")).toEqual(["tables"]);
    expect(exclusionKinds("mongodb", "database")).toEqual(["tables"]);
    expect(exclusionKinds("redis", "database")).toEqual([]);
  });

  it("adds whole databases for the policy over every database", () => {
    expect(exclusionKinds("postgresql", "all")).toEqual(["databases", "schemas", "tables"]);
    expect(exclusionKinds("mysql", "all")).toEqual(["databases", "tables"]);
    expect(exclusionKinds("redis", "all")).toEqual(["databases"]);
  });

  it("reads an engine in a container as the engine it is", () => {
    expect(exclusionKinds("postgresql@proggest.postgres", "database")).toEqual(["schemas", "tables"]);
  });

  it("covers every database of an engine that has several, and not Redis", () => {
    expect(coversEveryDatabase("postgresql")).toBe(true);
    expect(coversEveryDatabase("mongodb")).toBe(true);
    expect(coversEveryDatabase("redis")).toBe(false);
  });
});

describe("the names it leaves out", () => {
  it("are read from commas, spaces and lines, once each", () => {
    expect(parseNames("scratch, cache\nscratch  logs", "databases")).toEqual({ names: ["scratch", "cache", "logs"], invalid: [], tooMany: false });
  });

  it("keep out what the server would refuse: a leading dash, a slash, an option", () => {
    expect(parseNames("--exclude ../etc a/b ok_name", "databases").invalid).toEqual(["--exclude", "../etc", "a/b"]);
    expect(parseNames("-x", "tables").invalid).toEqual(["-x"]);
    expect(parseNames("audit.logs", "schemas").invalid).toEqual(["audit.logs"]);
  });

  it("allow a table to be qualified or a pattern, and a schema only a name", () => {
    expect(parseNames("public.sessions logs_* cache?", "tables").invalid).toEqual([]);
    expect(parseNames("audit", "schemas").invalid).toEqual([]);
  });

  it("say when a list is longer than a policy may hold", () => {
    const text = Array.from({ length: MAX_EXCLUSIONS + 1 }, (_, index) => `t${String(index)}`).join(" ");
    expect(parseNames(text, "tables").tooMany).toBe(true);
    expect(parseNames("a b", "tables").tooMany).toBe(false);
  });
});

describe("what a saved policy leaves out", () => {
  const policy = { exclusions: { databases: ["scratch"], tables: ["logs", "sessions"] } };

  it("goes back into the text boxes, one per line", () => {
    expect(exclusionText(policy, "tables")).toBe("logs\nsessions");
    expect(exclusionText(policy, "schemas")).toBe("");
    expect(exclusionText(undefined, "tables")).toBe("");
  });

  it("is listed by kind, leaving out the empty ones", () => {
    expect(leftOutLines(policy)).toEqual([
      { kind: "databases", names: ["scratch"] },
      { kind: "tables", names: ["logs", "sessions"] },
    ]);
    expect(leftOutLines({ exclusions: {} })).toEqual([]);
  });
});
