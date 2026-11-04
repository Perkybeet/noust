import { describe, expect, it } from "vitest";

import type { Column } from "../../components/ui/DataTable";
import { inRows } from "./ownership";

interface Database {
  name: string;
  tags: string[];
}

interface Listed {
  database: Database;
}

describe("inRows", () => {
  const column: Column<Database> = {
    id: "name",
    header: "Database",
    cell: (database) => database.name,
    sortValue: (database) => database.name,
    stacked: (database) => database.tags.length > 0,
  };
  const picked = inRows<Database, Listed>(column, (listed) => listed.database);

  it("reads a cell, a sort value and whether the cell stacks lines from the row inside the one the table holds", () => {
    const tagged: Listed = { database: { name: "shop", tags: ["production"] } };
    const plain: Listed = { database: { name: "blog", tags: [] } };
    expect(picked.cell(tagged)).toBe("shop");
    expect(picked.sortValue?.(plain)).toBe("blog");
    expect(picked.stacked?.(tagged)).toBe(true);
    expect(picked.stacked?.(plain)).toBe(false);
  });

  it("leaves out what the column did not have, so a column that does not stack does not start to", () => {
    const bare = inRows<Database, Listed>({ id: "name", header: "Database", cell: (database) => database.name }, (listed) => listed.database);
    expect(bare).not.toHaveProperty("stacked");
    expect(bare).not.toHaveProperty("sortValue");
  });
});
