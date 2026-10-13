/**
 * What an operator is typing is never lost to what the server says meanwhile (checklist M-30).
 * Coolify's forms dropped focus and what was typed every ten seconds as its page refreshed
 * (issues 9695 and 8647). Here a field is typed into one character at a time, slowly enough that
 * the console's own event stream delivers `metrics` (every two seconds) and `machine` (every five)
 * in the middle of it, in a form, in a dialog and in a search box over a list the events update,
 * and each keeps its focus and every character.
 */

import type { Locator, Page } from "@playwright/test";

import { expect, signIn, test } from "./fixtures";

const IN_PLACE_APP = "pedidos.example.org";

/** One character every 450 ms: ten of them take four and a half seconds, two metrics events at least. */
const SLOWLY = 450;
const TYPED = "1234567890";

/** Counts the events of the stream the page holds, to prove some arrived while the field was typed into. */
async function countEvents(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const seen = { count: 0 };
    (window as unknown as { __events: { count: number } }).__events = seen;
    const Native = window.EventSource;
    window.EventSource = class extends Native {
      constructor(url: string | URL, init?: EventSourceInit) {
        super(url, init);
        for (const name of ["machine", "metrics", "app", "job", "notice", "state"]) {
          this.addEventListener(name, () => {
            seen.count += 1;
          });
        }
      }
    };
  });
}

function eventsSeen(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as { __events: { count: number } }).__events.count);
}

/** Types `text` into `field` a character at a time, and says how many events arrived meanwhile. */
async function typeSlowly(page: Page, field: Locator, text: string): Promise<number> {
  await field.click();
  const before = await eventsSeen(page);
  await field.pressSequentially(text, { delay: SLOWLY });
  return (await eventsSeen(page)) - before;
}

test("a limit typed into the resources form keeps its focus and every character while events arrive", async ({ page, consoleServer }) => {
  await countEvents(page);
  await signIn(page, consoleServer, `/apps/${IN_PLACE_APP}/settings/resources`);
  const memory = page.getByRole("textbox", { name: "Memory" });
  await expect(memory).toBeVisible();
  await memory.fill("");

  const arrived = await typeSlowly(page, memory, TYPED);

  expect(arrived, "the stream said nothing while the field was typed into: the test proved nothing").toBeGreaterThanOrEqual(2);
  await expect(memory).toBeFocused();
  await expect(memory).toHaveValue(TYPED);
});

test("a dialog's text area keeps its focus and what was pasted or typed while events arrive", async ({ page, consoleServer }) => {
  await countEvents(page);
  await signIn(page, consoleServer, `/apps/${IN_PLACE_APP}/environment`);
  await page.getByRole("button", { name: "Paste .env" }).click();
  const contents = page.getByRole("dialog", { name: "Paste a .env file" }).getByLabel(".env contents");

  const arrived = await typeSlowly(page, contents, "ABC=1234567890");

  expect(arrived).toBeGreaterThanOrEqual(2);
  await expect(contents).toBeFocused();
  await expect(contents).toHaveValue("ABC=1234567890");
});

test("a search over a list the events keep updating keeps its focus and its text", async ({ page, consoleServer }) => {
  await countEvents(page);
  await signIn(page, consoleServer, "/apps");
  const search = page.getByRole("searchbox", { name: "Search applications" });
  await expect(search).toBeVisible();

  const arrived = await typeSlowly(page, search, "pedidos");

  expect(arrived).toBeGreaterThanOrEqual(1);
  await expect(search).toBeFocused();
  await expect(search).toHaveValue("pedidos");
});
