/**
 * No URL loses its screen (docs/DESIGN.md, NV-18): the tables of app/nav.ts walked in a browser
 * against the real backend, in both themes. An address that moved lands on its new place with
 * the filters it carried; one whose destination does not hold everything yet still opens its
 * own page; and a new route whose page is yet to come opens the screen that holds the same thing
 * today. routes/redirects.test.tsx walks the same tables through the router alone.
 */

import type { Page } from "@playwright/test";

import { INTERIM_ROWS as INTERIM, REDIRECT_ROWS as REDIRECTS } from "../src/app/redirects";
import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

/** The seeded machine's names for the parameters of a path. */
const SAMPLE: Readonly<Record<string, string>> = { $domain: "shop.example.net", $name: "wasm-shop-example-net" };

const address = (pattern: string): string => pattern.replace(/\$\w+/g, (param) => SAMPLE[param] ?? "sample");

function pathnameOf(page: Page): () => string {
  return () => new URL(page.url()).pathname;
}

test("an address that moved opens where it is now, with the filters it carried", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/");
  for (const row of REDIRECTS.filter((candidate) => candidate.state === "live")) {
    // A list keeps its search; the page of one thing (a service) has none.
    const search = row.from.includes("$") ? "" : "?q=zzz";
    await page.goto(`${address(row.from)}${search}`);
    await expect.poll(pathnameOf(page), { message: `${row.from} did not reach ${row.to}` }).toBe(address(row.to));
    if (search !== "") expect(new URL(page.url()).searchParams.get("q"), `${row.from} lost its search`).toBe("zzz");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  }
});

test("the page of the Teams tab is the page /teams always was", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/teams");
  await expect.poll(pathnameOf(page)).toBe("/projects/teams");
  await expect(page.getByRole("heading", { level: 1, name: "Teams" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the teams list, at its new address");
});

test("an address whose destination does not hold everything yet still opens its own page", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/");
  for (const row of REDIRECTS.flatMap((candidate) => (candidate.state === "pending" ? [candidate] : []))) {
    expect(row.after.trim(), `${row.from} is pending and says nothing it waits for`).not.toBe("");
    await page.goto(address(row.from));
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(new URL(page.url()).pathname, `${row.from} redirects although ${row.to} does not hold it all`).toBe(address(row.from));
  }
});

test("a route whose page is yet to come opens the screen that holds the same thing today", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/");
  for (const row of INTERIM) {
    await page.goto(address(row.from));
    await expect.poll(pathnameOf(page), { message: `${row.from} did not reach ${row.to}` }).toBe(address(row.to));
    await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
  }
  await settle(page);
  await expectNoA11yViolations(page, "the screen an account route opens");
});
