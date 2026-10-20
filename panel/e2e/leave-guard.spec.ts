/**
 * The question asked before a form with unsaved changes is left (docs/DESIGN.md FM-3).
 *
 * Until 4.0 a settings subsection with a save bar let go of its draft without a word: editing a
 * value in an application's Deploys and pressing "Resources" in the side list dropped the edit.
 * The save bar owns one question now ("Discard unsaved changes?", with "Keep editing" focused so
 * that Enter loses nothing), for a navigation inside the console and for the browser's own
 * "leave this page" when the tab is closed or reloaded. This walks it on three real forms of
 * different shapes, a server's General (a text field), an application's Deploys (a number) and the
 * Notifications settings (a choice of language), because a guard that works on one page and not on
 * the next is the failure this prevents.
 */

import type { Locator, Page } from "@playwright/test";

import { expect, settle, signIn, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";
import { confirmItsYou } from "./settings.helpers";

interface Form {
  id: string;
  path: string;
  /** The first control of the form, waited for before anything is edited. */
  first: (page: Page) => Locator;
  /** What it holds now, as text. */
  read: (page: Page) => Promise<string>;
  /** Changes it to something it does not hold. */
  change: (page: Page) => Promise<void>;
}

const FORMS: readonly Form[] = [
  {
    id: "settings",
    path: "/settings",
    first: (page) => page.getByLabel("Server name"),
    read: (page) => page.getByLabel("Server name").inputValue(),
    change: (page) => page.getByLabel("Server name").fill("web-leave-guard"),
  },
  {
    id: "app-settings-deploys",
    path: "/apps/tienda.example.org/settings/deploys",
    first: (page) => page.getByLabel("Versions to keep"),
    read: (page) => page.getByLabel("Versions to keep").inputValue(),
    change: (page) => page.getByLabel("Versions to keep").fill("7"),
  },
  {
    id: "settings-notifications",
    path: "/settings/notifications",
    first: (page) => page.getByRole("radiogroup", { name: "Language of notifications" }),
    read: async (page) => ((await page.getByRole("radio", { name: "Español" }).isChecked()) ? "es" : "en"),
    change: (page) => page.getByRole("radio", { name: "Español" }).click(),
  },
];

const saveBar = (page: Page): Locator => page.getByRole("region", { name: "Unsaved changes" });
const question = (page: Page): Locator => page.getByRole("alertdialog", { name: "Discard unsaved changes?" });

/** A link of the sidebar that leaves any settings page: it points at the Databases list. */
const leave = (page: Page): Locator => page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Databases", exact: true });

/** Opens the form, waits until it has stopped loading and says it holds nothing unsaved, and returns what its first control holds. */
async function open(page: Page, form: Form, server: ConsoleServer): Promise<string> {
  await signIn(page, server, form.path);
  await expect(form.first(page)).toBeVisible();
  await expect(page.locator("main [aria-busy=true]")).toHaveCount(0);
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  await settle(page);
  return form.read(page);
}

for (const form of FORMS) {
  test.describe(form.id, () => {
    test("asks once before leaving with a change, keeps the edit on 'Keep editing' and drops it on 'Discard changes'", async ({ page, consoleServer }) => {
      const original = await open(page, form, consoleServer);
      await form.change(page);
      const edited = await form.read(page);
      expect(edited).not.toBe(original);
      await expect(saveBar(page).getByText("1 unsaved change")).toBeVisible();

      await leave(page).click();
      await expect(question(page)).toBeVisible();
      await expect(question(page).getByText("Leaving this page discards 1 unsaved change.")).toBeVisible();
      // The safe answer has the focus: Enter alone loses nothing.
      await expect(question(page).getByRole("button", { name: "Keep editing" })).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(question(page)).toBeHidden();
      expect(new URL(page.url()).pathname).toBe(form.path);
      expect(await form.read(page)).toBe(edited);
      await expect(saveBar(page).getByText("1 unsaved change")).toBeVisible();

      // Asked again, and this time let go: the page leaves and the draft with it.
      await leave(page).click();
      await question(page).getByRole("button", { name: "Discard changes" }).click();
      await expect(page).toHaveURL(/\/databases/);
      await expect(question(page)).toBeHidden();
      await page.goBack();
      await expect(form.first(page)).toBeVisible();
      await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
      expect(await form.read(page)).toBe(original);
    });

    test("asks nothing when nothing changed, and nothing after the edit is discarded", async ({ page, consoleServer }) => {
      const original = await open(page, form, consoleServer);

      await leave(page).click();
      await expect(page).toHaveURL(/\/databases/);
      await expect(question(page)).toHaveCount(0);

      await page.goBack();
      await expect(form.first(page)).toBeVisible();
      await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
      await form.change(page);
      await expect(saveBar(page).getByText("1 unsaved change")).toBeVisible();
      await saveBar(page).getByRole("button", { name: "Discard" }).click();
      await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
      expect(await form.read(page)).toBe(original);
      await leave(page).click();
      await expect(page).toHaveURL(/\/databases/);
      await expect(question(page)).toHaveCount(0);
    });

    test("warns the browser before the tab is closed, only while there is something to lose", async ({ page, consoleServer }) => {
      await open(page, form, consoleServer);
      // Whether the browser then draws its own dialog is its decision (it wants a gesture on the page,
      // which an automated one does not always count as); what the page owns is cancelling the event.
      /** Whether the page asks the browser to confirm a close: it cancels the event. */
      const asks = (): Promise<boolean> =>
        page.evaluate(() => {
          const event = new Event("beforeunload", { cancelable: true });
          window.dispatchEvent(event);
          return event.defaultPrevented;
        });

      expect(await asks(), "a form with nothing unsaved lets the tab close").toBe(false);
      await form.change(page);
      await expect(saveBar(page).getByText("1 unsaved change")).toBeVisible();
      expect(await asks(), "a form with a change asks the browser to confirm").toBe(true);
      await saveBar(page).getByRole("button", { name: "Discard" }).click();
      await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
      expect(await asks(), "discarding the change lets the tab close again").toBe(false);
    });
  });
}

test("a saved change is not asked about, and the question comes back with the next edit", async ({ page, consoleServer }) => {
  const form = FORMS[0];
  if (form === undefined) throw new Error("no form");
  const name = page.getByLabel("Server name");
  const original = await open(page, form, consoleServer);

  await form.change(page);
  await expect(saveBar(page).getByText("1 unsaved change")).toBeVisible();
  // Saving configuration is sudo mode: the bar asks "Confirm it's you" before the write.
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
  await leave(page).click();
  await expect(page).toHaveURL(/\/databases/);
  await expect(question(page)).toHaveCount(0);

  // Back on the page what was saved is there; the next edit is guarded again. Then the worker's
  // server is put back as it was.
  await page.goBack();
  await expect(name).toHaveValue("web-leave-guard");
  await name.fill(original);
  await expect(saveBar(page).getByText("1 unsaved change")).toBeVisible();
  await leave(page).click();
  await expect(question(page)).toBeVisible();
  await question(page).getByRole("button", { name: "Keep editing" }).click();
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByText("No unsaved changes")).toBeVisible();
});
