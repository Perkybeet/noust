/**
 * An operator's own picture on the sign-in screen (4.2, item 129), against the real backend, in both themes, with the
 * strict policy, axe and the real flow: choose a file in Settings, see it on the screen as it will be seen, save it in
 * sudo mode, open the screen as a stranger does and find it there, with the rest of the slab where it was, change how
 * it fills the space, and take it off again.
 *
 * The pictures are drawn by the browser itself (a logo with a transparent ground, a photograph-like landscape), so
 * what is served and shown is a real PNG and a real JPEG, not bytes that merely have the right header. A picture of
 * each screen is written to $NOUST_IMAGE_SHOTS when it is set, for a review by eye.
 */

import { request as playwrightRequest } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";
import path from "node:path";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test, totpCode } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const SHOTS = process.env.NOUST_IMAGE_SHOTS;

/** A file as Playwright's chooser takes it. */
interface Upload {
  name: string;
  mimeType: string;
  buffer: Buffer;
}

type Drawing = "logo" | "photo" | "wide";

/** What the browser draws and encodes: a PNG logo, a JPEG landscape, or a PNG wider than the limit allows. */
async function drawn(page: Page, what: Drawing): Promise<Upload> {
  const encoded = await page.evaluate((kind) => {
    const canvas = document.createElement("canvas");
    const pen = canvas.getContext("2d");
    if (pen === null) throw new Error("no canvas");
    if (kind === "wide") {
      canvas.width = 5000;
      canvas.height = 10;
      pen.fillStyle = "#336699";
      pen.fillRect(0, 0, 5000, 10);
      return { data: canvas.toDataURL("image/png"), name: "wide.png", mimeType: "image/png" };
    }
    if (kind === "logo") {
      canvas.width = 760;
      canvas.height = 260;
      // A mark and a name on a transparent ground, in a tone that reads on both themes: it is the company's own.
      pen.fillStyle = "#1f8a70";
      pen.beginPath();
      pen.roundRect(40, 40, 180, 180, 40);
      pen.fill();
      pen.fillStyle = "#ffffff";
      pen.beginPath();
      pen.moveTo(130, 70);
      pen.lineTo(190, 190);
      pen.lineTo(70, 190);
      pen.closePath();
      pen.fill();
      pen.fillStyle = "#1f8a70";
      pen.font = "700 112px sans-serif";
      pen.textBaseline = "middle";
      pen.fillText("Acme", 260, 118);
      pen.font = "500 40px sans-serif";
      pen.fillText("Corp", 262, 200);
      return { data: canvas.toDataURL("image/png"), name: "acme-logo.png", mimeType: "image/png" };
    }
    canvas.width = 1280;
    canvas.height = 720;
    const sky = pen.createLinearGradient(0, 0, 0, 460);
    sky.addColorStop(0, "#5b7fb5");
    sky.addColorStop(0.6, "#e9b383");
    sky.addColorStop(1, "#f6d9a8");
    pen.fillStyle = sky;
    pen.fillRect(0, 0, 1280, 720);
    pen.fillStyle = "#fff4d6";
    pen.beginPath();
    pen.arc(930, 300, 70, 0, Math.PI * 2);
    pen.fill();
    const ridge = (base: number, colour: string, seed: number): void => {
      pen.fillStyle = colour;
      pen.beginPath();
      pen.moveTo(0, 720);
      for (let x = 0; x <= 1280; x += 40) pen.lineTo(x, base - 60 * Math.abs(Math.sin(x / 190 + seed)) - 30 * Math.sin(x / 61 + seed * 2));
      pen.lineTo(1280, 720);
      pen.closePath();
      pen.fill();
    };
    ridge(470, "#6c7fa3", 1);
    ridge(540, "#47597c", 2.2);
    ridge(620, "#2b3a55", 3.4);
    // Grain, so it compresses like a photograph and not like a drawing.
    for (let index = 0; index < 6000; index += 1) {
      pen.fillStyle = `rgba(${String(index % 255)}, ${String((index * 7) % 255)}, ${String((index * 13) % 255)}, 0.05)`;
      pen.fillRect((index * 97) % 1280, (index * 53) % 720, 3, 3);
    }
    return { data: canvas.toDataURL("image/jpeg", 0.82), name: "lake.jpg", mimeType: "image/jpeg" };
  }, what);
  return { name: encoded.name, mimeType: encoded.mimeType, buffer: Buffer.from(encoded.data.split(",")[1] ?? "", "base64") };
}

async function capture(page: Page, project: string, name: string): Promise<void> {
  if (SHOTS === undefined) return;
  await settle(page);
  await page.screenshot({ path: path.join(SHOTS, project, `${name}.png`) });
}

/** Where the slab and its picture slot are, as a person sees them. */
interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

async function slabBoxes(page: Page): Promise<{ slab: Box; identity: Box; art: Box }> {
  return page.evaluate(() => {
    const box = (selector: string) => {
      const element = document.querySelector(selector);
      if (element === null) throw new Error(`nothing matches ${selector}`);
      const { x, y, width, height } = element.getBoundingClientRect();
      return { x, y, width, height };
    };
    return { slab: box("[data-template='auth']"), identity: box("[data-template='auth'] [data-slot='identity']"), art: box("[data-template='auth'] [data-slot='art']") };
  });
}

/** Leaves the console and opens the sign-in screen as a stranger does: no session, nothing remembered. */
async function asAStranger(page: Page): Promise<void> {
  await page.context().clearCookies();
  await page.goto("/login");
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  await settle(page);
}

/** Best effort, for a test that failed halfway: the server outlives the test, and the next one measures the drawing. */
async function putTheDrawingBack(page: Page, server: ConsoleServer): Promise<void> {
  try {
    await signIn(page, server);
    await page.evaluate(async () => {
      const csrf = /noust_csrf=([^;]+)/.exec(document.cookie)?.[1] ?? "";
      await fetch("/api/auth/sign-in/appearance", { method: "DELETE", headers: { "X-Noust-CSRF": csrf } });
    });
  } catch {
    // The drawing is back or it was never gone: what matters is that this does not hide the failure above it.
  }
}

const preview = (page: Page) => page.locator("[data-drop-zone]");

/** Signs in and opens the section: after a visit as a stranger the page is already the sign-in's, so `next` would be ignored. */
async function openSettings(page: Page, server: ConsoleServer): Promise<void> {
  await signIn(page, server);
  await page.goto("/settings/sign-in-image");
  await expect(page.getByRole("heading", { level: 1, name: "Sign-in image" })).toBeVisible();
}

/** What this spec asks of the server and is answered no, which Chromium writes to the console as it writes every refusal. */
const REFUSED_ON_PURPOSE = /status of (403|400) .*\/api\/auth\/sign-in\/appearance$/;

test("a company's logo and then a photograph take the space of the drawing, and only that space", async ({ page, consoleServer, problems }, testInfo) => {
  // Sudo mode is asked for once, and answered with a 403 before it is given.
  problems.expect(REFUSED_ON_PURPOSE);
  const project = testInfo.project.name;
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  try {
    await test.step("the screen as a stranger sees it before anything is set: the drawing", async () => {
      await asAStranger(page);
      await expect(page.locator("[data-slot='art'] svg[viewBox='0 0 400 230']")).toBeVisible();
    });
    const withDrawing = await slabBoxes(page);

    await test.step("an operator chooses a logo and sees the screen as it will be seen, then saves it in sudo mode", async () => {
      await openSettings(page, consoleServer);
      await expect(page.getByText("It is seen before signing in: anyone who opens the console sees it.")).toBeVisible();
      await expect(preview(page).locator("svg[viewBox='0 0 400 230']")).toBeVisible();

      const logo = await drawn(page, "logo");
      await page.getByLabel("Choose an image").setInputFiles(logo);
      await expect(page.getByText("acme-logo.png, not saved yet")).toBeVisible();
      const shown = preview(page).locator("img");
      await expect(shown).toHaveAttribute("src", /^data:image\/png;base64,/);
      await expect(shown).toHaveJSProperty("complete", true);
      // A logo is shown whole, on the surface of the space.
      await expect(page.getByRole("radio", { name: "Fit it whole" })).toBeChecked();
      await expect(shown).toHaveCSS("object-fit", "contain");
      await page.getByLabel(/^Description/).fill("Acme Corp");
      await expect(page.getByText("2 unsaved changes")).toBeVisible();
      await capture(page, project, "settings-logo-1440");
      await expectNoA11yViolations(page, "Settings > Sign-in image with a logo chosen");

      await page.getByRole("button", { name: "Save" }).click();
      await confirmItsYou(page, consoleServer);
      await expect(page.getByText("The picture that is set now")).toBeVisible();
      await expect(page.getByText("No unsaved changes")).toBeVisible();
    });

    await test.step("a stranger finds the logo in the space of the drawing, and the rest of the slab where it was", async () => {
      const served = page.waitForResponse((response) => response.url().includes("/api/auth/sign-in/image"));
      await asAStranger(page);
      const response = await served;
      // Under its own digest it is public, cached for a year, of the type its bytes are, and cannot be sniffed into another.
      expect(response.status()).toBe(200);
      expect(response.headers()["content-type"]).toBe("image/png");
      expect(response.headers()["cache-control"]).toBe("public, max-age=31536000, immutable");
      expect(response.headers()["x-content-type-options"]).toBe("nosniff");
      expect(response.url()).toMatch(/\?v=[0-9a-f]{64}$/);

      const art = page.locator("[data-template='auth'] [data-slot='art']");
      const logo = art.getByRole("img", { name: "Acme Corp" });
      await expect(logo).toBeVisible();
      await expect(logo).toHaveJSProperty("complete", true);
      expect(await logo.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(760);
      await expect(art.locator("svg[viewBox='0 0 400 230']")).toHaveCount(0);
      await expect(logo).toHaveCSS("object-fit", "contain");
      // Noust's logo, its sentence, the licence notice and the form are all there: only the space of the drawing changed.
      await expect(page.locator("[data-slot='identity']").getByRole("img", { name: "Noust" })).toBeVisible();
      await expect(page.getByText("Your apps and servers, kept safe.")).toBeVisible();
      await expect(page.getByRole("contentinfo")).toContainText("Yago López Prado");
      await expect(page.getByLabel("Username or email")).toBeVisible();
      // And the slab is the size it was: the picture has the room the drawing had.
      const withLogo = await slabBoxes(page);
      expect(Math.abs(withLogo.slab.height - withDrawing.slab.height)).toBeLessThanOrEqual(1);
      expect(Math.abs(withLogo.identity.height - withDrawing.identity.height)).toBeLessThanOrEqual(1);
      expect(Math.abs(withLogo.art.height - withDrawing.art.height)).toBeLessThanOrEqual(1);

      await capture(page, project, "login-logo-1440");
      await expectNoA11yViolations(page, "the sign-in with a logo");
      await page.setViewportSize({ width: 390, height: 844 });
      await settle(page);
      await expect(logo).toBeVisible();
      const phone = await slabBoxes(page);
      // On a phone the picture is where the drawing is: a band across the top of the slab, the whole width of the hollow.
      expect(Math.abs(phone.art.width - (phone.identity.width - 32))).toBeLessThanOrEqual(1);
      const form = await page.locator("[data-slot='form']").boundingBox();
      expect(phone.identity.y + phone.identity.height).toBeLessThanOrEqual((form?.y ?? 0) + 1);
      await capture(page, project, "login-logo-390");
      await expectNoA11yViolations(page, "the sign-in with a logo on a phone");
      await page.setViewportSize({ width: 1440, height: 900 });
    });

    await test.step("the operator changes it for a photograph that fills the space", async () => {
      await openSettings(page, consoleServer);
      await expect(page.getByText("The picture that is set now")).toBeVisible();
      const photo = await drawn(page, "photo");
      await page.getByLabel("Change the image").setInputFiles(photo);
      await expect(page.getByText("lake.jpg, not saved yet")).toBeVisible();
      await page.getByRole("radio", { name: "Fill the space" }).click();
      await page.getByLabel(/^Description/).fill("A lake at dusk");
      const shown = preview(page).locator("img");
      await expect(shown).toHaveCSS("object-fit", "cover");
      await capture(page, project, "settings-photo-1440");
      await page.getByRole("button", { name: "Save" }).click();
      // A new sign-in is a new session, and sudo mode belongs to the session.
      await confirmItsYou(page, consoleServer);
      await expect(page.getByText("No unsaved changes")).toBeVisible();
      await expect(page.getByText("The picture that is set now")).toBeVisible();
    });

    await test.step("a stranger finds the photograph filling the space, the whole box", async () => {
      await asAStranger(page);
      const art = page.locator("[data-template='auth'] [data-slot='art']");
      const photo = art.getByRole("img", { name: "A lake at dusk" });
      await expect(photo).toBeVisible();
      await expect(photo).toHaveJSProperty("complete", true);
      await expect(photo).toHaveCSS("object-fit", "cover");
      // The box the picture is in is the box the drawing had (the slot's own padding is not part of it).
      const [box, shown] = await Promise.all([photo.locator("xpath=..").boundingBox(), photo.boundingBox()]);
      // It fills that box, edge to edge, and the slab is still the size it was.
      expect(Math.abs((shown?.width ?? 0) - (box?.width ?? 0))).toBeLessThanOrEqual(1);
      expect(Math.abs((shown?.height ?? 0) - (box?.height ?? 0))).toBeLessThanOrEqual(1);
      const withPhoto = await slabBoxes(page);
      expect(Math.abs(withPhoto.slab.height - withDrawing.slab.height)).toBeLessThanOrEqual(1);
      await capture(page, project, "login-photo-1440");
      await expectNoA11yViolations(page, "the sign-in with a photograph");
      await page.setViewportSize({ width: 390, height: 844 });
      await settle(page);
      await expect(photo).toBeVisible();
      await capture(page, project, "login-photo-390");
      await expectNoA11yViolations(page, "the sign-in with a photograph on a phone");
      await page.setViewportSize({ width: 1440, height: 900 });
    });

    await test.step("removing it asks once and brings the drawing back, and the old address is gone", async () => {
      await openSettings(page, consoleServer);
      const address = await preview(page).locator("img").getAttribute("src");
      expect(address).toMatch(/^\/api\/auth\/sign-in\/image\?v=[0-9a-f]{64}$/);
      await page.getByRole("button", { name: "Remove the image" }).click();
      const dialog = page.getByRole("alertdialog", { name: "Remove the sign-in image?" });
      await expect(dialog).toBeVisible();
      await dialog.getByRole("button", { name: "Remove the image" }).click();
      await confirmItsYou(page, consoleServer);
      await expect(preview(page).locator("svg[viewBox='0 0 400 230']")).toBeVisible();
      await expect(page.getByText("Noust's own drawing, as it is now")).toBeVisible();

      const gone = await page.request.get(address ?? "/");
      expect(gone.status()).toBe(404);
      await asAStranger(page);
      await expect(page.locator("[data-slot='art'] svg[viewBox='0 0 400 230']")).toBeVisible();
      await expect(page.locator("[data-slot='art'] img")).toHaveCount(0);
    });
  } finally {
    await putTheDrawingBack(page, consoleServer);
  }
});

test("a picture the server refuses is refused in its own words, and what was there stays", async ({ page, consoleServer, problems }, testInfo) => {
  problems.expect(REFUSED_ON_PURPOSE);
  const project = testInfo.project.name;
  await page.setViewportSize({ width: 1440, height: 900 });
  try {
    await openSettings(page, consoleServer);

    // A PNG the browser draws without complaint and the server will not take: 5000 pixels is past the limit.
    await page.getByLabel("Choose an image").setInputFiles(await drawn(page, "wide"));
    await expect(page.getByText("wide.png, not saved yet")).toBeVisible();
    await page.getByRole("button", { name: "Save" }).click();
    await confirmItsYou(page, consoleServer);

    await expect(page.getByText("Could not save the sign-in image")).toBeVisible();
    await expect(page.getByText("The image is 5000 by 10 pixels, and the most is 4096 on each side")).toBeVisible();
    await expect(page.getByText("Scale it down and send it again.")).toBeVisible();
    await capture(page, project, "settings-refused-1440");
    await expectNoA11yViolations(page, "Settings > Sign-in image after a refusal");
    // Nothing was kept: a stranger still sees the drawing.
    await asAStranger(page);
    await expect(page.locator("[data-slot='art'] svg[viewBox='0 0 400 230']")).toBeVisible();
  } finally {
    await putTheDrawingBack(page, consoleServer);
  }
});

test("a file that is not an image of the three types is refused before it is sent", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openSettings(page, consoleServer);
  const sent: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "PUT") sent.push(request.url());
  });

  await page.getByLabel("Choose an image").setInputFiles({ name: "logo.svg", mimeType: "image/svg+xml", buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>') });

  await expect(page.getByText(/An SVG is not accepted: it is code/)).toBeVisible();
  await expect(page.getByText(/not saved yet/)).toHaveCount(0);
  expect(sent).toEqual([]);
  await expectNoA11yViolations(page, "Settings > Sign-in image after an SVG");
});

/** The CSRF header of a session made over the API. */
async function csrf(context: APIRequestContext): Promise<Record<string, string>> {
  const { cookies } = await context.storageState();
  const token = cookies.find((cookie) => cookie.name === "noust_csrf");
  return token ? { "X-Noust-CSRF": token.value } : {};
}

/** The access token's session over the API, in sudo mode: what an administrator does before changing a setting. */
async function administrator(server: ConsoleServer): Promise<APIRequestContext> {
  const api = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await api.post("/api/auth/login", { data: { token: server.token, totp_code: server.secondFactor() } })).ok()).toBe(true);
  expect((await api.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(api) })).ok()).toBe(true);
  return api;
}

/** A viewer with an authenticator, as the person would enrol it: what a person who may only read signs in with. */
async function aViewer(server: ConsoleServer, admin: APIRequestContext): Promise<{ username: string; password: string; codes: string[] }> {
  const username = `reader-${String(Date.now())}`;
  const password = "quiet meadow viewer lantern 7919";
  const created = await admin.post("/api/auth/accounts", { data: { username, role: "viewer", password }, headers: await csrf(admin) });
  expect(created.status(), await created.text()).toBe(201);
  const own = await playwrightRequest.newContext({ baseURL: server.url });
  expect((await own.post("/api/auth/login", { data: { username, password } })).ok()).toBe(true);
  const enrol = (await (await own.post("/api/auth/2fa/enroll", { headers: await csrf(own) })).json()) as { secret: string };
  const confirmed = await own.post("/api/auth/2fa/confirm", { data: { code: totpCode(enrol.secret) }, headers: await csrf(own) });
  expect(confirmed.ok(), await confirmed.text()).toBe(true);
  const { backup_codes: codes } = (await confirmed.json()) as { backup_codes: string[] };
  await own.dispose();
  return { username, password, codes };
}

test("someone who may only read the page sees the picture, with its action turned off and the reason a press away", async ({ page, problems }, testInfo) => {
  const project = testInfo.project.name;
  test.setTimeout(150_000);
  // The page asks what a viewer may not have in its shell, and each refusal is a line in the console.
  problems.expect(/status of (401|403|404) /);
  await page.setViewportSize({ width: 1440, height: 900 });
  const logo = await drawn(page, "logo");
  // A server of its own: an account changes how every other test of the worker signs in.
  const server = await startConsoleServer(["--totp", "--backup-codes", "40"]);
  const admin = await administrator(server);
  try {
    // The first account of a server is its owner, whatever it was asked to be: a viewer is the second.
    const first = await admin.post("/api/auth/accounts", {
      data: { username: "first-owner", role: "admin", password: "quiet meadow owner lantern 1111" },
      headers: await csrf(admin),
    });
    expect(first.status(), await first.text()).toBe(201);
    const set = await admin.put("/api/auth/sign-in/appearance", {
      data: { image: logo.buffer.toString("base64"), fit: "contain", alt: "Acme Corp" },
      headers: await csrf(admin),
    });
    expect(set.status(), await set.text()).toBe(200);
    const reader = await aViewer(server, admin);

    await page.goto(`${server.url}/login?next=${encodeURIComponent("/settings/sign-in-image")}`);
    await page.getByLabel("Username or email").fill(reader.username);
    await page.getByLabel("Password").fill(reader.password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByText(`Password accepted for ${reader.username}.`)).toBeVisible();
    await page.getByLabel("Two-factor code").fill(reader.codes[0] ?? "");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Sign-in image" })).toBeVisible();
    // The precondition, said by the server: this person holds the permission to read and not the one to change.
    const held = await page.evaluate(async () => ((await (await fetch("/api/auth/session")).json()) as { role: string | null; permissions: string[] }));
    expect(held.permissions, `role ${String(held.role)}`).toContain("settings.read");
    expect(held.permissions, `role ${String(held.role)}`).not.toContain("security.manage");

    // The page is read: the screen with the saved picture, said to be seen by everyone, and nothing to edit or save.
    await expect(page.getByText("It is seen before signing in: anyone who opens the console sees it.")).toBeVisible();
    await expect(page.getByText("You can see this page, not change it.")).toBeVisible();
    await expect(page.locator("[data-drop-zone]").getByRole("img", { name: "Acme Corp" })).toBeHidden(); // the preview is hidden from assistive technology
    await expect(page.locator("[data-drop-zone] img")).toHaveAttribute("src", /\/api\/auth\/sign-in\/image\?v=[0-9a-f]{64}$/);
    await expect(page.getByRole("radiogroup")).toHaveCount(0);
    await expect(page.getByRole("textbox")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Remove the image" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Save" })).toHaveCount(0);

    // The primary action stays where it is, turned off with a lock (the kit's GuardedButton), and the reason is one press away.
    await expect(page.getByRole("button", { name: "Change the image" })).toBeDisabled();
    await page.getByRole("button", { name: "Why you cannot do this" }).click();
    await expect(page.getByText(/What is seen before signing in is part of how the console is protected/)).toBeVisible();
    await expect(page.getByText("security.manage")).toBeVisible();
    await capture(page, project, "settings-readonly-1440");
    await expectNoA11yViolations(page, "Settings > Sign-in image read-only");
  } finally {
    await admin.dispose();
    // Nothing on screen may keep asking a server that is going away.
    await page.goto("about:blank");
    await server.stop();
  }
});
