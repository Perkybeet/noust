import { describe, expect, it } from "vitest";

import { formatFilter, parseFilter, parseSort, validateDataSearch } from "./search";

describe("the data browser's place in the URL", () => {
  it("reads a filter the API's way, a value holding colons included", () => {
    expect(parseFilter("created_at:gte:2026-09-01 10:00")).toEqual({ column: "created_at", op: "gte", value: "2026-09-01 10:00" });
    expect(parseFilter("notes:null")).toEqual({ column: "notes", op: "null", value: "" });
    expect(parseFilter("status:contains:x")).toBeNull();
    expect(formatFilter({ column: "notes", op: "notnull", value: "ignored" })).toBe("notes:notnull");
  });

  it("reads an order, ascending unless it says otherwise", () => {
    expect(parseSort("total:desc")).toEqual({ column: "total", descending: true });
    expect(parseSort("id")).toEqual({ column: "id", descending: false });
    expect(parseSort(undefined)).toBeNull();
  });

  it("drops what it cannot read instead of refusing the link", () => {
    expect(
      validateDataSearch({ schema: "public", table: "orders", view: "whatever", filter: ["status:eq:paid", "bad"], limit: "7", type: "Bad!" }),
    ).toEqual({ schema: "public", table: "orders", filter: ["status:eq:paid"] });
    expect(validateDataSearch({ view: "structure", limit: "100", filter: "id:gt:5" })).toEqual({ view: "structure", limit: 100, filter: ["id:gt:5"] });
  });
});
