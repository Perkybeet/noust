/**
 * Reviewing a declaration before it is applied, against the real backend (checklist M-35): a draft
 * can start from what runs now, the blocks that differ are shown line by line, one block can be
 * put back without touching the others, and checking changes nothing. Runs in both themes, with the
 * CSP and console gates of the `problems` fixture.
 */

import type { Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const APP = "lanzamiento.example.org";

const DRAFT = "app:\n  health:\n    path: /e2e-review\nenv:\n  E2E_REVIEW_NOTE: hello\n";

/** Sudo mode, through the API, so the dialog's calls are not interrupted by the confirmation. */
async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const response = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(response.ok(), await response.text()).toBe(true);
}

test("a draft starts from what runs, shows the lines that change, and puts one block back", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/settings/export`);
  await elevate(page, consoleServer);
  await settle(page);

  const running = (await (await page.request.get(`/api/apps/${APP}/declaration`)).json()) as { declaration: string };

  await page.getByRole("button", { name: "Apply a declaration…" }).click();
  const dialog = page.getByRole("dialog", { name: `Apply a declaration to ${APP}` });
  const editor = dialog.getByLabel("Declaration (YAML)");
  await expect(dialog.getByText("What this set covers")).toBeVisible();

  // What runs now is the first thing a draft can be: the very text the server exports.
  await dialog.getByRole("button", { name: "Start from what runs now" }).click();
  await expect(editor).toHaveValue(running.declaration);
  await expect(dialog.getByRole("list", { name: "Blocks of the declaration that change" })).toHaveCount(0);

  await editor.fill(DRAFT);
  await dialog.getByRole("button", { name: "Check what it changes" }).click();
  await expect(dialog.getByRole("heading", { name: "What it changes" })).toBeVisible();
  const blocks = dialog.getByRole("list", { name: "Blocks of the declaration that change" });
  await expect(blocks).toBeVisible();
  await expect(blocks.getByRole("group", { name: /Lines of How it runs/ })).toContainText("/e2e-review");
  await expect(blocks.getByRole("group", { name: /Lines of Variables/ })).toContainText("E2E_REVIEW_NOTE");
  await stillness(page);
  await expectNoA11yViolations(page, "the declaration dialog with its blocks reviewed");

  // One block back to what runs: the other stays, and the plan is of the text that is left.
  await dialog.getByRole("button", { name: /^Put How it runs .* back$/ }).click();
  await expect(editor).not.toHaveValue(/e2e-review/);
  await expect(editor).toHaveValue(/E2E_REVIEW_NOTE: hello/);
  await expect(blocks.getByRole("group", { name: /Lines of How it runs/ })).toHaveCount(0);
  await expect(blocks.getByRole("group", { name: /Lines of Variables/ })).toBeVisible();

  // Going back and checking applied nothing.
  const environment = await (await page.request.get(`/api/apps/${APP}/env`)).text();
  expect(environment).not.toContain("E2E_REVIEW_NOTE");
  await stillness(page);
  await expectNoA11yViolations(page, "the declaration dialog after a block is put back");
});
