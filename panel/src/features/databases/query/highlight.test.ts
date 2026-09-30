import { describe, expect, it } from "vitest";

import { tokenize } from "./highlight";

describe("the SQL editor's tokens", () => {
  it("tells keywords, identifiers, strings, numbers and comments apart", () => {
    const tokens = tokenize("SELECT \"id\", 'x''y' FROM orders -- recent\nWHERE total > 1.5");
    const kinds = tokens.filter((token) => token.text.trim() !== "").map((token) => [token.kind, token.text.trim()]);
    expect(kinds).toContainEqual(["keyword", "SELECT"]);
    expect(kinds).toContainEqual(["identifier", '"id"']);
    expect(kinds).toContainEqual(["string", "'x''y'"]);
    expect(kinds).toContainEqual(["comment", "-- recent"]);
    expect(kinds).toContainEqual(["number", "1.5"]);
    expect(kinds).toContainEqual(["keyword", "WHERE"]);
  });

  it("gives back exactly the text it was given, so the drawn copy lines up with the textarea", () => {
    const source = "select * from t where a = 'unterminated\n  and b = $$ body $$ /* open";
    expect(tokenize(source).map((token) => token.text).join("")).toBe(source);
  });

  it("reads a keyword in any case, and a word that only contains one as a word", () => {
    const kinds = tokenize("select selection").map((token) => token.kind);
    expect(kinds[0]).toBe("keyword");
    expect(tokenize("selection")[0]?.kind).toBe("text");
  });
});
