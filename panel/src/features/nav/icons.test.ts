import { describe, expect, it } from "vitest";

import tableSource from "./icons.ts?raw";

/**
 * The icons of the lists on the side are drawn by no other file of the console. An icon that two
 * files import is a file of its own in the build, and every route that reaches it lists it to
 * preload: forty of them were a kilobyte and a half on the first page, where the routes of Settings
 * are named. One nobody else imports stays in the table's file. So when a page wants one of these,
 * it is the page that takes another icon (or the table does), not both.
 */
const SOURCES = import.meta.glob<string>(["../../**/*.{ts,tsx}", "!../../**/*.test.{ts,tsx}", "!../../test/**", "!./icons.ts"], {
  eager: true,
  query: "?raw",
  import: "default",
});

function lucideNames(source: string): string[] {
  return [...source.matchAll(/import\s*\{([^}]*)\}\s*from\s*"lucide-react"/g)].flatMap((match) =>
    (match[1] ?? "")
      .split(",")
      .map((name) => name.trim().split(" as ")[0]?.trim() ?? "")
      .filter((name) => name !== ""),
  );
}

describe("the icons of the lists on the side", () => {
  const mine = new Set(lucideNames(tableSource));

  it("are the table's and nobody else's", () => {
    const shared = Object.entries(SOURCES).flatMap(([file, source]) => lucideNames(source).filter((name) => mine.has(name)).map((name) => `${name} in ${file}`));
    expect(shared, "an icon of features/nav/icons.ts that another file draws too: take another one in one of the two").toEqual([]);
  });

  it("are lucide's, a lot of them", () => {
    expect(mine.size).toBeGreaterThan(30);
  });
});
