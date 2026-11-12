import { describe, expect, it } from "vitest";

import { SECRET_VARIABLE_CASES } from "../api/secretShapes.gen";
import { looksSecret, nameLooksSecret, valueLooksSecret } from "./looksSecret";

describe("looksSecret", () => {
  // The one table both sides are held to: the server's `classify` is run through it in Python
  // (tests/test_secret_shape_table.py) and this is the console's reading of the same rules.
  it.each(SECRET_VARIABLE_CASES.map((entry) => [`${entry.name}=${entry.value}`, entry] as const))("judges %s as the server does", (_label, entry) => {
    expect(looksSecret(entry.name, entry.value)).toBe(entry.secret);
  });

  it("has the verdicts of both kinds in its table, so no rule is only ever tested one way", () => {
    expect(SECRET_VARIABLE_CASES.filter((entry) => entry.secret).length).toBeGreaterThanOrEqual(15);
    expect(SECRET_VARIABLE_CASES.filter((entry) => !entry.secret).length).toBeGreaterThanOrEqual(15);
  });
});

describe("nameLooksSecret", () => {
  it("reads a name as the server does: by substring and by whole words, whatever its case", () => {
    expect(nameLooksSecret("STRIPE_SECRET_KEY")).toBe(true);
    expect(nameLooksSecret("stripeSecretKey")).toBe(true);
    expect(nameLooksSecret("db.password")).toBe(true);
    expect(nameLooksSecret("keyboard_layout")).toBe(false);
    expect(nameLooksSecret("")).toBe(false);
    expect(nameLooksSecret("   ")).toBe(false);
  });
});

describe("valueLooksSecret", () => {
  it("knows a credential on its own, and leaves a hash, an id, a path and an address alone", () => {
    expect(valueLooksSecret("sk_live_4eC39HqLyjWDarjtT1zdp7dc")).toBe(true);
    expect(valueLooksSecret("postgres://app:hunter22@db.internal:5432/shop")).toBe(true);
    expect(valueLooksSecret("3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39")).toBe(false);
    expect(valueLooksSecret("550e8400-e29b-41d4-a716-446655440000")).toBe(false);
    expect(valueLooksSecret("/var/www/apps/shop/current")).toBe(false);
    expect(valueLooksSecret("https://shop.example.com/Mixed1Case2Path3")).toBe(false);
  });

  it("does not take the words around a value for the value: a sentence that says password is not a password", () => {
    expect(valueLooksSecret("password=hunter2222")).toBe(false);
    expect(valueLooksSecret("Bearer abcdef")).toBe(false);
  });

  it("is not fooled by a very long value of no one kind of character", () => {
    expect(valueLooksSecret("a".repeat(80))).toBe(false);
    expect(valueLooksSecret("0123456789".repeat(8))).toBe(false);
  });
});
