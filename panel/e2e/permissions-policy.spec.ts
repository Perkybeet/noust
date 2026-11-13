/**
 * The `Permissions-Policy` the server sends, as the browser applies it.
 *
 * The header is pinned in text by the server's tests; what only a browser can say is whether
 * what the console does use is still open to it: the report of a problem asks to share the tab
 * (`display-capture`), the copy buttons write to the clipboard, the log goes full screen and the
 * sign-in with a key asks for a credential. A feature closed by mistake fails there, in front of
 * a person, with a message about the browser and not about the header.
 */

import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

/** What the browser says of a feature in the page it loaded. */
async function allowed(page: Page, features: readonly string[]): Promise<Record<string, boolean>> {
  return page.evaluate((names) => {
    const doc = document as Document & {
      permissionsPolicy?: { allowsFeature: (name: string) => boolean };
      featurePolicy?: { allowsFeature: (name: string) => boolean };
    };
    const policy = doc.permissionsPolicy ?? doc.featurePolicy;
    if (policy === undefined) throw new Error("This browser does not report its permissions policy");
    return Object.fromEntries(names.map((name) => [name, policy.allowsFeature(name)]));
  }, features);
}

test("what the console uses is open to it and what it never uses is closed", async ({ page }) => {
  await page.goto("/login?with=token");
  await expect(page.getByLabel("Access token").or(page.getByRole("button", { name: "Access token" }))).toBeVisible();

  const used = ["display-capture", "clipboard-write", "fullscreen", "publickey-credentials-get", "publickey-credentials-create"];
  const never = ["camera", "microphone", "geolocation", "payment", "usb", "serial", "hid", "bluetooth"];
  const verdicts = await allowed(page, [...used, ...never]);

  expect(Object.fromEntries(used.map((name) => [name, verdicts[name]]))).toEqual(
    Object.fromEntries(used.map((name) => [name, true])),
  );
  expect(Object.fromEntries(never.map((name) => [name, verdicts[name]]))).toEqual(
    Object.fromEntries(never.map((name) => [name, false])),
  );
});

test("the answer to a page that does not exist carries the same policy", async ({ request }) => {
  const missing = await request.get("/assets/missing.js");

  expect(missing.status()).toBe(404);
  expect(missing.headers()["permissions-policy"]).toContain("display-capture=(self)");
  expect(missing.headers()["permissions-policy"]).toContain("camera=()");
});
