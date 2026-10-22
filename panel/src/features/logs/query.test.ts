import { describe, expect, it } from "vitest";

import { QueryError, composeQuery, fieldsOf, keeps, parseQuery, priorityOfViewerLevel, readQuery, renderQuery } from "./query";

/** The text the server writes back for the same query (noust.managers.server.logquery.render). */
const understood = (text: string): string => renderQuery(parseQuery(text));

describe("reading a query", () => {
  it("reads the examples of the grammar the way the server writes them back", () => {
    expect(understood("")).toBe("");
    expect(understood("timeout")).toBe("timeout");
    expect(understood('"connection refused"')).toBe('"connection refused"');
    expect(understood("@unit:nginx error -timeout")).toBe("@unit:nginx AND error AND NOT timeout");
    expect(understood("error OR fatal")).toBe("error OR fatal");
    expect(understood("(a OR b) c")).toBe("(a OR b) AND c");
    expect(understood("NOT (a b)")).toBe("NOT (a AND b)");
    expect(understood('@app:"shop example"')).toBe('@app:"shop example"');
  });

  it("takes service for unit and priority for level, and keeps a lower-case or as a word", () => {
    expect(understood("@service:nginx")).toBe("@unit:nginx");
    expect(understood("@priority:err")).toBe("@level:err");
    expect(understood("this or that")).toBe("this AND or AND that");
  });

  it("does not take a dash in the middle of a word for a negation", () => {
    expect(understood("health-check")).toBe("health-check");
    expect(understood("a - b")).toBe("a AND - AND b");
  });

  it.each([
    ['"open', "A quote is not closed (at character 1)"],
    ["(nginx", "A parenthesis is not closed (at character 1)"],
    ["nginx)", "Unexpected closing parenthesis (at character 6)"],
    ["a OR", "The query ends where a word, a field or a group was expected"],
    ["OR a", "'OR' needs something on both sides (at character 1)"],
    ["@nope:x", "There is no field @nope: use @unit, @app, @level, @pid (at character 1)"],
    ["@unit:", "@unit: needs a value (at character 1)"],
    ["@level:loud", "@level:loud is not a level: use emerg, alert, crit, critical, err, error, warning, warn, notice, info, debug or 0 to 7 (at character 1)"],
    ["@level:9", "@level:9 is not a level: use emerg, alert, crit, critical, err, error, warning, warn, notice, info, debug or 0 to 7 (at character 1)"],
    ["@pid:abc", "@pid:abc is not a process number (at character 1)"],
    ["@unit:a*b*c*d*e*f", "@unit:a*b*c*d*e*f has more than 4 wildcards (at character 1)"],
    ["@", "A field is written @name:value, such as @unit:nginx (at character 1)"],
  ])("refuses %j in the server's own words", (text, message) => {
    expect(() => parseQuery(text)).toThrow(message);
    expect(() => parseQuery(text)).toThrow(QueryError);
  });

  it("says where a query cannot be read", () => {
    const read = readQuery("error (timeout");
    expect(read.ok).toBe(false);
    if (!read.ok) {
      expect(read.error.at).toBe(6);
      expect(read.error.message).toBe("A parenthesis is not closed (at character 7)");
    }
  });

  it("refuses a query longer than the server accepts, and one nested too deep", () => {
    expect(() => parseQuery("a".repeat(501))).toThrow("The query is longer than 500 characters");
    expect(() => parseQuery(`${"(".repeat(10)}a${")".repeat(10)}`)).toThrow("nested more than 8 deep");
    expect(() => parseQuery(Array.from({ length: 41 }, (_, i) => `w${String(i)}`).join(" OR "))).toThrow("more than 40 parts");
  });

  it("reads a half-typed query as a value", () => {
    expect(readQuery("error").ok).toBe(true);
    expect(readQuery("").ok).toBe(true);
    expect(readQuery("(").ok).toBe(false);
  });
});

describe("the fields a query names", () => {
  it("finds them anywhere, under a negation too", () => {
    expect([...fieldsOf(parseQuery("a (@unit:x OR -@level:err) @app:shop"))].sort()).toEqual(["app", "level", "unit"]);
    expect([...fieldsOf(parseQuery("plain words"))]).toEqual([]);
  });
});

describe("judging a line", () => {
  const line = (message: string, extra: Partial<{ priority: number; unit: string; app: string; pid: number }> = {}) => ({ message, ...extra });

  it("looks for words ignoring case, and for a phrase as a whole", () => {
    expect(keeps(parseQuery("TIMEOUT"), line("upstream timed out: timeout"))).toBe(true);
    expect(keeps(parseQuery('"timed out"'), line("upstream timed out"))).toBe(true);
    expect(keeps(parseQuery('"out timed"'), line("upstream timed out"))).toBe(false);
  });

  it("combines with AND, OR, NOT and groups by their precedence", () => {
    const query = parseQuery("(error OR fatal) -debug");
    expect(keeps(query, line("fatal: out of memory"))).toBe(true);
    expect(keeps(query, line("error but debug"))).toBe(false);
    expect(keeps(query, line("all fine"))).toBe(false);
    // NOT binds tighter than AND, AND tighter than OR.
    const loose = parseQuery("a b OR c");
    expect(keeps(loose, line("c only"))).toBe(true);
    expect(keeps(loose, line("a only"))).toBe(false);
  });

  it("matches a unit without its suffix, in any case, with wildcards", () => {
    expect(keeps(parseQuery("@unit:nginx"), line("x", { unit: "Nginx.service" }))).toBe(true);
    expect(keeps(parseQuery("@unit:noust-*"), line("x", { unit: "noust-web.service" }))).toBe(true);
    expect(keeps(parseQuery("@unit:noust-*"), line("x", { unit: "nginx.service" }))).toBe(false);
    expect(keeps(parseQuery("@unit:n?inx"), line("x", { unit: "nginx" }))).toBe(true);
    expect(keeps(parseQuery("@unit:nginx"), line("x", { unit: "nginx-extra.service" }))).toBe(false);
  });

  it("keeps a level or worse", () => {
    expect(keeps(parseQuery("@level:warning"), line("x", { priority: 3 }))).toBe(true);
    expect(keeps(parseQuery("@level:warning"), line("x", { priority: 4 }))).toBe(true);
    expect(keeps(parseQuery("@level:warning"), line("x", { priority: 6 }))).toBe(false);
    expect(keeps(parseQuery("@level:3"), line("x", { priority: 4 }))).toBe(false);
  });

  it("matches an application and a process", () => {
    expect(keeps(parseQuery("@app:shop.example.com"), line("x", { app: "Shop.Example.com" }))).toBe(true);
    expect(keeps(parseQuery("@app:shop.example.com"), line("x", { app: "blog.example.com" }))).toBe(false);
    expect(keeps(parseQuery("@pid:41"), line("x", { pid: 41 }))).toBe(true);
    expect(keeps(parseQuery("@pid:41"), line("x", { pid: 42 }))).toBe(false);
  });

  it("keeps a line that does not carry what a clause asks about, instead of hiding it by a guess", () => {
    // A followed stream has the message and, sometimes, a level; never a unit.
    expect(keeps(parseQuery("@unit:nginx error"), line("an error"))).toBe(true);
    expect(keeps(parseQuery("@unit:nginx error"), line("all fine"))).toBe(false);
    expect(keeps(parseQuery("-@unit:nginx"), line("anything"))).toBe(true);
    expect(keeps(parseQuery("@level:err"), line("no level here"))).toBe(true);
    expect(keeps(parseQuery("@unit:nginx OR error"), line("fine"))).toBe(true);
    expect(keeps(parseQuery("@unit:nginx OR error"), line("an error"))).toBe(true);
  });

  it("keeps everything for an empty query", () => {
    expect(keeps(parseQuery(""), line("anything"))).toBe(true);
  });
});

describe("the viewer's levels as priorities", () => {
  it("maps what the viewer draws to the journal's numbers", () => {
    expect(priorityOfViewerLevel("error")).toBe(3);
    expect(priorityOfViewerLevel("warn")).toBe(4);
    expect(priorityOfViewerLevel("info")).toBe(6);
    expect(priorityOfViewerLevel("debug")).toBe(7);
    expect(priorityOfViewerLevel(undefined)).toBeUndefined();
  });
});

describe("composing the question of the pickers and the box", () => {
  it("writes the pickers as fields before what was typed", () => {
    expect(composeQuery({ unit: "nginx.service", level: "err" }, "connect -timeout")).toBe("@unit:nginx.service @level:err connect -timeout");
    expect(composeQuery({ app: "shop.example.com" }, "  ")).toBe("@app:shop.example.com");
    expect(composeQuery({}, "")).toBe("");
  });

  it("quotes a value with a space or a quote in it", () => {
    expect(composeQuery({ unit: 'a "b" c' }, "")).toBe('@unit:"a b c"');
  });
});
