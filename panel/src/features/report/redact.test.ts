import { describe, expect, it } from "vitest";

import { SECRET_MASK, SECRET_SHAPES, SECRET_SHAPE_CASES } from "../../api/secretShapes.gen";
import { pieces, redact } from "./redact";

describe("masking the text of the browser", () => {
  // The table Python runs through mask_secret_shapes: a shape the two read differently fails here or there.
  it.each(SECRET_SHAPE_CASES.map((entry) => [entry.name, entry] as const))("%s", (_name, entry) => {
    const result = redact(entry.text);

    expect(result.text).toBe(entry.masked);
    expect(result.holes > 0).toBe(entry.masked !== entry.text);
  });

  it("walks every shape the server walks, in the order the server does", () => {
    const names = SECRET_SHAPES.map((shape) => shape.name);

    expect(names.indexOf("assigned secret")).toBeLessThan(names.indexOf("bearer"));
    expect(names.indexOf("private key block")).toBeLessThan(names.indexOf("private key"));
    expect(names.at(-1)).toBe("url credentials");
  });

  it("counts each secret it hides", () => {
    const result = redact("DB_PASSWORD=hunter2222 and postgres://a:b1b1b1b1@h/db with ghp_" + "a".repeat(36));

    expect(result.holes).toBe(3);
    expect(result.text).toBe(`DB_PASSWORD=${SECRET_MASK} and postgres://a:${SECRET_MASK}@h/db with ${SECRET_MASK}`);
  });

  it("changes nothing the second time", () => {
    const once = redact("token=abcdefghijkl and ghp_" + "a".repeat(36) + " in postgres://a:b1b1b1b1@h/db").text;

    expect(redact(once)).toEqual({ text: once, holes: 0 });
  });

  it("is the same when asked again: the patterns carry no state from one text to the next", () => {
    const text = "key ghp_" + "a".repeat(36);

    expect(redact(text)).toEqual(redact(text));
  });

  it("hides the token in the address of the page and keeps the rest of it", () => {
    expect(redact("/apps/shop?token=0123456789abcdef&tab=logs").text).toBe(`/apps/shop?token=${SECRET_MASK}&tab=logs`);
  });

  it("does not take long on a hostile line", () => {
    const started = performance.now();
    redact("password".repeat(50_000));
    redact("token=" + "a".repeat(500_000));
    redact("-----BEGIN RSA PRIVATE KEY-----" + "A".repeat(500_000));
    redact("Bearer " + "x".repeat(500_000));
    redact("a:".repeat(100_000) + "@");
    redact("://".repeat(100_000));

    expect(performance.now() - started).toBeLessThan(5_000);
  });
});

describe("the pieces of a text, for a preview", () => {
  it("marks each mask as hidden and leaves the words around it", () => {
    expect(pieces(`password=${SECRET_MASK} and ${SECRET_MASK}`)).toEqual([
      { hidden: false, text: "password=" },
      { hidden: true, text: SECRET_MASK },
      { hidden: false, text: " and " },
      { hidden: true, text: SECRET_MASK },
    ]);
  });

  it("is one visible piece for a text with nothing hidden, and none for an empty one", () => {
    expect(pieces("hello")).toEqual([{ hidden: false, text: "hello" }]);
    expect(pieces("")).toEqual([]);
  });
});
