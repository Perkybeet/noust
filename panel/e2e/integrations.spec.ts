/**
 * Settings > Integrations and GitHub's callback against the real backend. The seeded machine
 * has its own GitHub App, installed on an organization and a personal account, with its hooks
 * exposed and the App's webhook receiving events; scripts/console_server.py answers GitHub's
 * API from fixed data, so nothing leaves the machine. The other states of the page (no App
 * yet, no installation, no public hooks address, a webhook still inactive on GitHub) are the
 * same machine at other moments, drawn from the real status answer with one field changed.
 *
 * Creating the App posts its manifest to github.com as a real form: the request is caught at
 * the browser's edge and never sent, and the Content Security Policy must let it go
 * (form-action), which the problems fixture fails on otherwise.
 */

import type { Page, Request } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, test, toasts } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const APP = "wasm-acme";
const HOOKS = "https://hooks.example.com/hooks/github";
/** The manifest code GitHub's fake no longer honours (scripts/console_server.py GITHUB_SPENT_CODE). */
const SPENT_CODE = "spent-manifest-code";

interface GitHubStatus {
  configured: boolean;
  installations: unknown[];
  hooks_url: string | null;
  hooks_active: boolean;
  [key: string]: unknown;
}

/** Answers the status with the real one, changed by `change`: another moment of this machine. */
async function statusAs(page: Page, change: (status: GitHubStatus) => GitHubStatus): Promise<void> {
  await page.route("**/api/integrations/github", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    await route.fulfill({ response, json: change((await response.json()) as GitHubStatus) });
  });
}

/** The CSRF header every write through `page.request` carries, mirrored from its cookie. */
async function csrf(page: Page): Promise<Record<string, string>> {
  const cookie = (await page.context().cookies()).find((entry) => entry.name === "wasm_csrf");
  return cookie ? { "X-WASM-CSRF": cookie.value } : {};
}

/**
 * Catches the manifest's post to github.com and stops it there: the browser leaves for GitHub
 * only in production. Resolves with the request as the browser would have sent it.
 */
function catchGitHub(page: Page): Promise<Request> {
  return new Promise((resolve) => {
    void page.route("https://github.com/**", async (route) => {
      resolve(route.request());
      await route.abort("aborted");
    });
  });
}

/** Starts creating the App from the page, to the point the browser posts to GitHub. */
async function createTheApp(page: Page, server: ConsoleServer): Promise<{ postUrl: string; state: string; posted: Request }> {
  const posted = catchGitHub(page);
  const started = page.waitForResponse((response) => response.url().endsWith("/api/integrations/github/manifest") && response.ok());
  await page.getByRole("button", { name: "Create GitHub App" }).click();
  // Asked unless the session is still in sudo mode from an earlier confirmation.
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  await expect(confirm.or(page.getByRole("status").filter({ hasText: "Opening GitHub" }))).toBeVisible();
  if (await confirm.isVisible()) await confirmItsYou(page, server);
  const manifest = (await (await started).json()) as { post_url: string; state: string; manifest: Record<string, unknown> };
  const request = await posted;
  return { postUrl: manifest.post_url, state: manifest.state, posted: request };
}

test("a connected App: what it is, where it is installed and that GitHub delivers its events", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings/integrations");
  await expect(page).toHaveTitle(/^Integrations/);
  const github = page.getByRole("region", { name: "GitHub" });
  await expect(github.getByText(APP, { exact: true })).toBeVisible();
  await expect(github.getByText("1043871")).toBeVisible();
  await expect(github.getByRole("link", { name: /github\.com\/apps\/wasm-acme/ })).toHaveAttribute("href", "https://github.com/apps/wasm-acme");

  const installations = page.getByRole("table", { name: "GitHub App installations" });
  const rows = installations.getByRole("row");
  await expect(rows).toHaveCount(3);
  await expect(rows.filter({ hasText: "acme" })).toContainText("Organization");
  await expect(rows.filter({ hasText: "acme" })).toContainText("All repositories");
  await expect(rows.filter({ hasText: "yago-lopez" })).toContainText("Personal account");
  await expect(rows.filter({ hasText: "yago-lopez" })).toContainText("Selected repositories");
  await expect(page.getByRole("link", { name: "Manage yago-lopez on GitHub" })).toHaveAttribute(
    "href",
    "https://github.com/settings/installations/61000002",
  );

  const events = page.getByRole("region", { name: "Push and pull request events" });
  await expect(events.getByText("Receiving events")).toBeVisible();
  await expect(events.getByText(HOOKS)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a connected GitHub App");

  // Syncing asks GitHub (the fake) for the App's installations: the same two.
  await page.getByRole("button", { name: "Sync installations" }).click();
  await expect(toasts(page).getByText("Synced 2 installations from GitHub")).toBeVisible();
  await expect(rows).toHaveCount(3);
});

const STATES: readonly { name: string; change: (status: GitHubStatus) => GitHubStatus; expect: (page: Page) => Promise<void> }[] = [
  {
    name: "installed nowhere yet",
    change: (status) => ({ ...status, installations: [] }),
    expect: async (page) => {
      await expect(page.getByText("Next: install the App")).toBeVisible();
      await expect(page.getByRole("link", { name: /Install on GitHub/ })).toHaveAttribute(
        "href",
        "https://github.com/apps/wasm-acme/installations/new",
      );
      await expect(page.getByRole("table", { name: "GitHub App installations" })).toHaveCount(0);
    },
  },
  {
    name: "hooks not exposed",
    change: (status) => ({ ...status, hooks_url: null, hooks_active: false }),
    expect: async (page) => {
      const events = page.getByRole("region", { name: "Push and pull request events" });
      await expect(events.getByText("Not reachable from GitHub")).toBeVisible();
      await expect(events.getByText("wasm web expose-hooks hooks.example.com")).toBeVisible();
    },
  },
  {
    name: "webhook inactive on GitHub",
    change: (status) => ({ ...status, hooks_active: false }),
    expect: async (page) => {
      const events = page.getByRole("region", { name: "Push and pull request events" });
      await expect(events.getByText("Inactive on GitHub")).toBeVisible();
      await expect(events.getByText(HOOKS)).toBeVisible();
      await expect(events.getByRole("link", { name: /Open the App's settings on GitHub/ })).toHaveAttribute(
        "href",
        "https://github.com/organizations/acme/settings/apps/wasm-acme",
      );
    },
  },
  {
    name: "no App yet, hooks exposed",
    change: (status) => ({ configured: false, installations: [], hooks_url: status.hooks_url, hooks_active: false }),
    expect: async (page) => {
      await expect(page.getByText("Connect GitHub with an App of your own")).toBeVisible();
      await expect(page.getByRole("button", { name: "Create GitHub App" })).toBeVisible();
      const events = page.getByRole("region", { name: "Push and pull request events" });
      await expect(events.getByText("Ready for the App")).toBeVisible();
      await expect(page.getByRole("button", { name: "Remove GitHub App" })).toHaveCount(0);
    },
  },
  {
    name: "no App yet, hooks not exposed",
    change: () => ({ configured: false, installations: [], hooks_url: null, hooks_active: false }),
    expect: async (page) => {
      await expect(page.getByRole("button", { name: "Create GitHub App" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Push and pull request events" }).getByText("Not reachable from GitHub")).toBeVisible();
    },
  },
];

for (const state of STATES) {
  test(`the integration ${state.name}`, async ({ page, consoleServer }) => {
    await statusAs(page, state.change);
    await signIn(page, consoleServer, "/settings/integrations");
    await state.expect(page);
    await settle(page);
    await expectNoA11yViolations(page, `the GitHub integration, ${state.name}`);
  });
}

test("creating the App posts its manifest to GitHub, and GitHub's code finishes it here", async ({ page, consoleServer, problems }) => {
  // The manifest is sudo mode: the first attempt is refused until "Confirm it's you".
  problems.expect(/status of 403 .* \/api\/integrations\/github\/manifest$/);
  await statusAs(page, (status) => ({ configured: false, installations: [], hooks_url: status.hooks_url, hooks_active: false }));
  await signIn(page, consoleServer, "/settings/integrations");
  // An organization's name is checked before anything is asked of the server.
  await page.getByLabel(/^Organization/).fill("not an org!");
  await page.getByRole("button", { name: "Create GitHub App" }).click();
  await expect(page.getByText(/An organization's name on GitHub uses letters, digits and single hyphens/)).toBeVisible();
  await page.getByLabel(/^Organization/).fill("");

  const { postUrl, state, posted } = await createTheApp(page, consoleServer);
  expect(postUrl).toBe(`https://github.com/settings/apps/new?state=${state}`);
  // The form the console built, and posted: to the address the server named, the manifest in it.
  await expect(page.locator("form[hidden]")).toHaveAttribute("action", postUrl);
  await expect(page.getByRole("status").filter({ hasText: "Opening GitHub" })).toBeVisible();
  expect(posted.method()).toBe("POST");
  expect(posted.url()).toBe(postUrl);
  const form = new URLSearchParams(posted.postData() ?? "");
  const manifest = JSON.parse(form.get("manifest") ?? "{}") as Record<string, unknown>;
  const origin = new URL(page.url()).origin;
  expect(manifest).toMatchObject({
    url: origin,
    redirect_url: `${origin}/integrations/github/callback`,
    setup_url: `${origin}/integrations/github/callback`,
    public: false,
    default_events: ["push", "pull_request"],
    hook_attributes: { url: HOOKS, active: true },
  });

  // GitHub sends the browser back with a one-time code and the state: the App is kept here.
  await page.unroute("**/api/integrations/github");
  await page.goto(`/integrations/github/callback?code=console-code-1&state=${state}`);
  await expect(toasts(page).getByText(`Created the GitHub App ${APP}`)).toBeVisible();
  await expect(page).toHaveURL(/\/settings\/integrations$/);
  await expect(page.getByRole("table", { name: "GitHub App installations" }).getByRole("row")).toHaveCount(3);
});

test("a callback this server did not start, or a code GitHub no longer honours, is refused in the server's words", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/integrations\/github\/manifest\/conversions$/);
  problems.expect(/status of 400 .* \/api\/integrations\/github\/manifest\/conversions$/);
  problems.expect(/status of 502 .* \/api\/integrations\/github\/manifest\/conversions$/);
  await signIn(page, consoleServer);
  await page.goto("/integrations/github/callback?code=console-code-2&state=forged-state");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Connecting GitHub");
  await confirmItsYou(page, consoleServer);
  const refused = page.getByRole("alert").filter({ hasText: "The GitHub App was not created on this server" });
  await expect(refused).toBeVisible();
  await expect(refused.getByText("This GitHub callback does not belong to an App creation started here")).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to integrations" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a refused callback");

  // A state this server issued, with a code GitHub refuses: GitHub's own answer, verbatim.
  const started = await page.request.post("/api/integrations/github/manifest", { data: { origin: new URL(page.url()).origin }, headers: await csrf(page) });
  expect(started.ok(), await started.text()).toBe(true);
  const { state } = (await started.json()) as { state: string };
  await page.goto(`/integrations/github/callback?code=${SPENT_CODE}&state=${state}`);
  const spent = page.getByRole("alert").filter({ hasText: "The GitHub App was not created on this server" });
  await expect(spent.getByText(`GitHub refused POST /app-manifests/${SPENT_CODE}/conversions (404)`)).toBeVisible();
  await expect(spent.getByText("Not Found")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a code GitHub refused");
});

test("GitHub's setup callback records the installation, after asking GitHub it is the App's", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/integrations\/github\/installations$/);
  problems.expect(/status of 502 .* \/api\/integrations\/github\/installations$/);
  await signIn(page, consoleServer);
  await page.goto("/integrations/github/callback?installation_id=61000002&setup_action=install");
  await confirmItsYou(page, consoleServer);
  await expect(toasts(page).getByText("Installed the GitHub App on yago-lopez")).toBeVisible();
  await expect(page).toHaveURL(/\/settings\/integrations$/);

  // An installation GitHub does not know for this App is not believed.
  await page.goto("/integrations/github/callback?installation_id=99&setup_action=install");
  const refused = page.getByRole("alert").filter({ hasText: "The installation was not recorded" });
  await expect(refused).toBeVisible();
  await expect(refused.getByText("GitHub refused GET /app/installations/99 (404)")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an installation GitHub does not know");
});

test("the callback without anything to finish, and an installation an owner must approve", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer);
  await page.goto("/integrations/github/callback?setup_action=request");
  await expect(page.getByText("Waiting for an organization owner")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an installation waiting for approval");

  await page.goto("/integrations/github/callback");
  await expect(page.getByText("Nothing to finish here")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a callback with nothing to finish");
  await page.getByRole("link", { name: "Back to integrations" }).click();
  await expect(page).toHaveURL(/\/settings\/integrations$/);
});

test("removing the App forgets it here and says where to delete it on GitHub; creating it again restores it", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .* \/api\/integrations\/github$/);
  problems.expect(/status of 403 .* \/api\/integrations\/github\/manifest$/);
  await signIn(page, consoleServer, "/settings/integrations");
  await page.getByRole("button", { name: "Remove GitHub App" }).click();
  const dialog = page.getByRole("alertdialog", { name: `Remove ${APP}` });
  await expect(dialog).toBeVisible();
  const confirm = dialog.getByRole("button", { name: "Remove GitHub App" });
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("textbox").fill(APP);
  await expect(confirm).toBeEnabled();
  await settle(page);
  await expectNoA11yViolations(page, "removing the GitHub App");
  await confirm.click();
  await confirmItsYou(page, consoleServer);
  await expect(toasts(page).getByText(`Removed ${APP} from this server`)).toBeVisible();
  const removed = page.getByRole("status").filter({ hasText: "The App was removed from this server" });
  await expect(removed).toBeVisible();
  await expect(removed.getByRole("link", { name: /Delete the App on GitHub/ })).toHaveAttribute(
    "href",
    "https://github.com/organizations/acme/settings/apps/wasm-acme",
  );
  await expect(page.getByRole("button", { name: "Create GitHub App" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the App removed");

  // Put the machine back as seeded for the rest of this worker: the App again, then its
  // installations, which removing it forgot.
  const { state } = await createTheApp(page, consoleServer);
  await page.goto(`/integrations/github/callback?code=console-code-3&state=${state}`);
  await expect(page).toHaveURL(/\/settings\/integrations$/);
  await expect(page.getByText("Next: install the App")).toBeVisible();
  await page.getByRole("button", { name: "Sync installations" }).click();
  await expect(toasts(page).getByText("Synced 2 installations from GitHub")).toBeVisible();
  await expect(page.getByRole("table", { name: "GitHub App installations" }).getByRole("row")).toHaveCount(3);
  await expect(page.getByRole("region", { name: "Push and pull request events" }).getByText("Receiving events")).toBeVisible();
});
