/**
 * The console of a machine that only has Docker, the container of the owner's central on a NAS, walked from the outside with a real
 * browser on the real backend (items 187 and 188). The owner's words are the criteria: "if something cannot be done, do not give the
 * option", and a NAS is a server too once it is told to deploy on its Docker.
 *
 * It runs on the two states of that machine, which only its compose file decides: a hub that was not told to deploy
 * (`--central-role hub --host-profile container`) and one that was (`--deploy-here containers`, with the NAS's Docker modelled by
 * `scripts/console_server/container_host.py`). Every entry of the bar and of every column is pressed, and the page it opens must
 * answer: any 4xx or 5xx (a console error of the page, which the fixtures fail on) and any text of a refusal fails the test. Both themes, with
 * axe on every page it looks at.
 */

import type { Locator, Page } from "@playwright/test";
import path from "node:path";

import { areaOf, paletteEntries } from "../src/app/nav";
import { isCentralOnlyPath } from "../src/app/nodeRoute";
import { offers } from "../src/features/central/offers";
import type { PlainKey } from "../src/i18n";
import { english } from "./catalog";
import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test as base } from "./fixtures";
import type { PageProblems, RunningServer } from "./fixtures";
import { continueTo, typedSource } from "./wizard-sources";

/** A test with the hub of a NAS, booted once for the worker that needs it: the two states are two fixtures of two tests. */
function nas(deployHere: boolean) {
  return base.extend<object, { nas: RunningServer }>({
    nas: [
      // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
      async ({}, use) => {
        const server = await startConsoleServer(["--central-role", "hub", "--host-profile", "container", ...(deployHere ? ["--deploy-here", "containers"] : [])]);
        try {
          await use(server);
        } finally {
          await server.stop();
        }
      },
      { scope: "worker", timeout: 120_000 },
    ],
    baseURL: async ({ nas: server }, use) => {
      await use(server.url);
    },
  });
}

const on = nas(true);
const off = nas(false);
for (const each of [on, off]) each.use({ viewport: { width: 1440, height: 900 } });

/** What a refusal of the server says, wherever it is drawn: nothing the console offers may end in one. */
const REFUSAL = /not available on (?:this central|a hub)|hub_role|which deploys containers and nothing else|containers_only/i;

const bar = (page: Page): Locator => page.getByRole("navigation", { name: "Main" });

/** The second column of the frame: the only list that keeps the focus on its links. */
const column = (page: Page): Locator => page.getByRole("complementary").locator("nav[data-keep-focus]").first();

/** The links of a list of the frame, as name and address: read before pressing any, because pressing one redraws the list. */
async function linksOf(list: Locator): Promise<{ name: string; href: string }[]> {
  return list.getByRole("link").evaluateAll((all) =>
    all.map((link) => ({ name: link.textContent.replace(/\s+/g, " ").replace(/\d+$/, "").trim(), href: link.getAttribute("href") ?? "" })),
  );
}

/** Presses a link and waits until the page it opens is drawn: its heading is there, and no refusal is. */
async function open(page: Page, link: { name: string; href: string }, within: Locator): Promise<void> {
  await within.locator(`a[href="${link.href}"]`).first().click();
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  await settle(page);
  // The page of the engines says, in the server's own sentence, why the machine has none of the system's own: it is what that page is for,
  // and the one place a refusal's words are an explanation and not what a press ended in.
  if (link.href.split("?")[0] === "/databases/engines") return;
  await expect(page.getByText(REFUSAL), `${link.name} (${link.href}) ends in a refusal`).toHaveCount(0);
}

/** Opens an area from the bar and waits until the second column is that area's: the column slides, and a list read in between is the previous one's. */
async function openArea(page: Page, area: { name: string; href: string }): Promise<void> {
  await open(page, area, bar(page));
  const kind = areaOf(area.href.split("?")[0] ?? "")?.id;
  await expect(page.locator('aside[data-frame="column"]'), `the column of ${area.name} did not arrive`).toHaveAttribute("data-column", kind ?? "");
}

/** A text of the English catalog by its dotted key, as the palette says it. */
function textOf(key: PlainKey): string {
  let node: unknown = english;
  for (const part of key.split(".")) node = (node as Record<string, unknown> | undefined)?.[part];
  if (typeof node !== "string") throw new Error(`the catalog has no text for "${key}"`);
  return node;
}

const DEPLOYED = "vault.casa.example.org";

/** The absolute path of one of the projects the console server keeps for this machine (`container_host.seed_sources`). */
async function source(page: Page, name: "container-api" | "compose-wiki" | "node-shop"): Promise<string> {
  const response = await page.request.get("/api/config/apps-directory");
  if (!response.ok()) throw new Error(`GET /api/config/apps-directory answered ${String(response.status())}`);
  const { apps_directory: apps } = (await response.json()) as { apps_directory: string };
  return path.posix.join(path.posix.dirname(apps), "src", name);
}

/**
 * Inspects a source from the wizard's first step and waits for the step it leads to: on this machine that is not Address (there is no
 * domain to ask for) but "How it opens", or Configuration where nothing in the source can run, confirming it is the operator when
 * the session is not in sudo mode yet.
 */
async function inspect(page: Page, server: RunningServer, problems: PageProblems, source: string, heading: string): Promise<void> {
  problems.expect(/status of 403 .* \/api\/apps\/inspect$/);
  await (await typedSource(page)).fill(source);
  await page.getByRole("button", { name: "Continue" }).click();
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  const arrived = page.getByRole("heading", { level: 2, name: heading });
  await expect(confirm.or(arrived)).toBeVisible();
  if (await confirm.isVisible()) await confirmItsYou(page, server);
  await expect(arrived).toBeFocused();
}

on.describe("a hub that deploys containers on its machine's Docker offers what the table says it can do, and nothing else", () => {
  on("every entry of the bar and of each column opens a page that works, with no 4xx, no 5xx and no refusal", async ({ page, nas: server }) => {
    on.setTimeout(420_000);
    await signIn(page, server, "/server");

    const areas = await linksOf(bar(page));
    const names = areas.map((area) => area.name);
    // What the machine cannot do is not in the bar: no Scheduled tasks and no Security. Logs stays, for the logs of the applications' containers.
    for (const gone of [/^Scheduled tasks/, /^Security/]) expect(names.some((name) => gone.test(name)), `the bar has ${String(gone)}`).toBe(false);
    // What it can is: the applications, the catalog, the databases in containers and the Docker of the machine.
    for (const there of [/^Applications/, /^Catalog/, /^Databases/, /^Server/]) expect(names.some((name) => there.test(name)), `the bar lacks ${String(there)}`).toBe(true);

    for (const area of areas) {
      if (area.href === "/settings") continue;
      await openArea(page, area);
      for (const section of await linksOf(column(page))) {
        if (section.href.startsWith("/fleet") || section.href.startsWith("/server/terminal")) continue;
        await open(page, section, column(page));
      }
    }
  });

  on("the Server column says what Docker is for here, and the page of Deploy on this machine checks it and promises what the table does", async ({ page, nas: server }) => {
    await signIn(page, server, "/server");
    const entry = column(page).getByRole("link", { name: "Deploy on this machine" });
    await expect(entry).toBeVisible();
    // What a machine without systemd or a web server cannot do is not in its column, nor what it would show of the container Noust runs in: the
    // processes, the updates and the files are the server's, which this Noust does not see.
    for (const gone of ["Services", "Web server", "Software", "Console", "Processes", "Updates", "Files"]) await expect(column(page).getByRole("link", { name: new RegExp(`^${gone}`) })).toHaveCount(0);

    await entry.click();
    await expect(page.getByRole("heading", { level: 1, name: "Deploy on this machine" })).toBeVisible();
    // It checks the machine once, when a person opens the page, and says what it found in the machine's words.
    await expect(page.getByText(/Docker 24\.0\.2 answers, speaking API 1\.43, with Compose 2\.20\.2/)).toBeVisible();
    await expect(page.getByText("On", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "What Noust does here, and what it does not" })).toBeVisible();
    await expect(page.getByText(REFUSAL)).toHaveCount(0);
    await settle(page);
    await expectNoA11yViolations(page, "Deploy on this machine, with every check in green");
  });

  on("the application Noust deployed says the address of its machine and the three ways to publish it outside", async ({ page, nas: server }) => {
    await signIn(page, server, `/apps/${DEPLOYED}`);
    await expect(page.getByRole("heading", { level: 1, name: DEPLOYED })).toBeVisible();
    // A port of the machine, said by the server, and not a domain that is not here.
    await expect(page.getByRole("link", { name: /http:\/\/nas-casa:8081/ }).first()).toBeVisible();
    await expect(page.getByText("Manage domains")).toHaveCount(0);

    const publish = page.getByRole("region", { name: "Publish it outside" });
    await expect(publish).toBeVisible();
    await publish.getByRole("button", { name: /With the machine's own reverse proxy/ }).click();
    await expect(publish.getByRole("group", { name: "Rule for the machine's reverse proxy" })).toContainText("proxy_pass http://127.0.0.1:8081;");
    await expect(publish.getByRole("button", { name: "Copy the rule" })).toBeVisible();
    await expect(page.getByText(REFUSAL)).toHaveCount(0);
    // Every section of the application's own column that is offered works too.
    for (const section of await linksOf(column(page))) {
      if (!section.href.startsWith(`/apps/${DEPLOYED}`) || section.href.endsWith("/terminal")) continue;
      await open(page, section, column(page));
    }
    await page.goto(`/apps/${DEPLOYED}`);
    await settle(page);
    await expectNoA11yViolations(page, "an application of a NAS, how it is reached");
  });

  on("what Noust runs by itself is where the schedules are", async ({ page, nas: server }) => {
    await signIn(page, server, "/backups/schedules");
    await expect(page.getByRole("region", { name: "What Noust runs by itself" })).toBeVisible();
    await expect(page.getByText(`Copy of ${DEPLOYED}`)).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the schedules of a NAS, with what Noust runs by itself");
  });

  on("the palette opens every page this machine can do, and never one it cannot", async ({ page, nas: server }, testInfo) => {
    on.skip(testInfo.project.name !== "light", "the palette is the same in both themes");
    on.setTimeout(600_000);
    await signIn(page, server, "/server");
    const session = (await (await page.request.get("/api/auth/session")).json()) as Parameters<typeof offers>[0];
    const entries = paletteEntries({ t: (key: PlainKey) => textOf(key), can: () => true, scope: "server", features: (key) => offers(session, key) });
    const search = page.getByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
    const missed: string[] = [];
    for (const entry of entries) {
      if (isCentralOnlyPath(entry.to) || entry.to === "/apps/new" || entry.to.startsWith("/fleet") || entry.to.includes("terminal")) continue;
      await page.goto("/server");
      // The shortcut is the console's, and it is heard once the page is drawn: pressed before, it is the browser's.
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await page.keyboard.press("Control+k");
      await search.fill(entry.text);
      await page.keyboard.press("Enter");
      await expect(page.getByRole("dialog", { name: "Search the console" })).toBeHidden();
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      if (new URL(page.url()).pathname !== entry.to.split("?")[0]) missed.push(`"${entry.text}" went to ${new URL(page.url()).pathname}, not ${entry.to} (${entry.id})`);
      // The engines say in the server's own sentence why the machine has none of the system's own: it is what that page is for.
      if (entry.to.split("?")[0] !== "/databases/engines" && (await page.getByText(REFUSAL).count()) > 0) missed.push(`"${entry.text}" ends in a refusal`);
    }
    expect(missed, missed.join("\n")).toEqual([]);
  });

  on("a new application opens on a port of the machine, asks no domain and offers no way forward for what cannot run here", async ({ page, nas: server, problems }) => {
    on.setTimeout(180_000);
    await signIn(page, server, "/apps/new");
    await inspect(page, server, problems, await source(page, "container-api"), "How it opens");
    // Not "Address" and not a domain: how it opens.
    await expect(page.getByLabel("Domain")).toHaveCount(0);
    await expect(page.getByLabel("Port of this machine")).toHaveValue("8082");
    await page.getByLabel("Name", { exact: true }).fill("wiki");
    await expect(page.getByRole("group", { name: "It will open at" })).toContainText(":8082");
    await settle(page);
    await expectNoA11yViolations(page, "how a new application opens on a NAS");
    await continueTo(page, "Configuration");
    await expect(page.getByText("There is no other way: there is nothing to choose.")).toBeVisible();
    await expect(page.getByText("Web server", { exact: true })).toHaveCount(0);

    // A project nothing in which runs here: the one fix, and no way forward.
    await page.goto("/apps/new");
    await inspect(page, server, problems, await source(page, "node-shop"), "Configuration");
    await expect(page.getByText(/Nothing in this repository runs on this machine/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Continue" })).toHaveCount(0);
    // Nothing to configure: no plan of a service of the system (install, build, start), no type to choose and nothing under "Advanced".
    for (const gone of ["Install", "Build", "App type", "Advanced"]) await expect(page.getByText(gone, { exact: true })).toHaveCount(0);
    await settle(page);
    await expectNoA11yViolations(page, "a source that nothing in runs on a NAS");
  });
});

on.describe("the databases of a machine that only has Docker are the instances Noust runs in containers", () => {
  on("lists the engines with the server's words for why the system has none, and offers to add one in a container and nothing to install", async ({ page, nas: server }) => {
    await signIn(page, server, "/databases/engines");
    await expect(page.getByRole("heading", { level: 1, name: "Engines" })).toBeVisible();
    // The sentence of the server's table, said once and calmly, with nothing to press beside it.
    await expect(page.getByText(/not available here, which deploys containers and nothing else/)).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the engines of a NAS, with why the system has none");
    await page.getByRole("button", { name: "Add an engine" }).click();
    const dialog = page.getByRole("dialog", { name: "Add an engine" });
    await expect(dialog).toBeVisible();
    // Where the engine comes from is not asked: there is a way and it is a container.
    await expect(dialog.getByText("On the system")).toHaveCount(0);
  });

  on("the list of databases is the one of every server, and says to make an engine in a container when none runs", async ({ page, nas: server }) => {
    await signIn(page, server, "/databases");
    await expect(page.getByRole("heading", { level: 1, name: "Databases" })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the databases of a NAS");
  });
});

off.describe("a hub that was not told to deploy is what it was, plus the way in to Deploy on this machine", () => {
  off("its bar has no applications, no databases and no domains, and every entry opens a page that works", async ({ page, nas: server }) => {
    off.setTimeout(300_000);
    await signIn(page, server, "/server");
    const names = (await linksOf(bar(page))).map((area) => area.name);
    for (const gone of [/^Applications/, /^Databases/, /^Scheduled tasks/, /^Domains/]) expect(names.some((name) => gone.test(name)), `the bar has ${String(gone)}`).toBe(false);
    for (const area of await linksOf(bar(page))) {
      if (area.href === "/settings") continue;
      await openArea(page, area);
      for (const section of await linksOf(column(page))) {
        if (section.href.startsWith("/fleet")) continue;
        await open(page, section, column(page));
      }
    }
  });

  off("the way in is in the Server column and in the central's role, and the page says what it means and that the console cannot turn it on", async ({ page, nas: server }) => {
    await signIn(page, server, "/settings/central");
    await expect(page.getByRole("link", { name: "Deploy on this machine" })).toBeVisible();
    await page.goto("/server");
    await column(page).getByRole("link", { name: "Deploy on this machine" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Deploy on this machine" })).toBeVisible();
    await expect(page.getByText("Off", { exact: true })).toBeVisible();
    await expect(page.getByText(/Docker access is root access on this machine/)).toBeVisible();
    await expect(page.getByText(/not from this console/)).toBeVisible();
    await expect(page.getByRole("group", { name: "Lines to add to Noust's compose.yaml" })).toContainText("NOUST_DEPLOY_HERE: containers");
    await settle(page);
    await expectNoA11yViolations(page, "Deploy on this machine, before it is turned on");

    // Asked, the machine says what stands in the way, with the change that fixes it, and what it could not check as not checked.
    await page.getByRole("button", { name: "Run again" }).click();
    await expect(page.getByText("This central is not set to deploy on this machine")).toBeVisible();
    await expect(page.getByText("With a problem")).toBeVisible();
    await expect(page.getByText("Noust does not deploy here until this is fixed")).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "Deploy on this machine, with what stands in the way");
  });

  off("an address that was typed for what a hub does not do says so once, calmly, and leads to the way in", async ({ page, nas: server }) => {
    await signIn(page, server, "/apps");
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "Deploy on this machine" })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "what a hub does not do, said calmly");
  });
});

if (process.env.NOUST_SCREENS === "1") {
  const OUT = process.env.NOUST_NAS_SCREENS ?? path.join(import.meta.dirname, "__screens__", "nas");
  const PHONE = { width: 390, height: 844 };

  /** The pages of a machine that only has Docker that this release changed, at 1440x900 and 390 wide, in the theme of the project. */
  on.describe("the NAS @screens", () => {
    const SCREENS: readonly { name: string; path: string; ready?: (page: Page, server: RunningServer, problems: PageProblems) => Promise<void> }[] = [
      {
        name: "inicio",
        path: "/",
        ready: async (page) => {
          // A hub opens on its fleet; the machine's own Home is the panel "This server".
          await page.getByRole("link", { name: /This server/ }).click();
          await expect(page.getByRole("heading", { level: 1, name: "This server" })).toBeVisible();
        },
      },
      { name: "bases-de-datos", path: "/databases" },
      { name: "bases-de-datos-motores", path: "/databases/engines" },
      { name: "servidor-estado", path: "/server" },
      { name: "servidor-docker", path: "/server/docker" },
      {
        name: "alta-sin-dockerfile",
        path: "/apps/new",
        ready: async (page, server, problems) => {
          await inspect(page, server, problems, await source(page, "node-shop"), "Configuration");
        },
      },
    ];
    for (const screen of SCREENS) {
      on(`nas ${screen.name}`, async ({ page, nas: server, problems }, testInfo) => {
        on.setTimeout(240_000);
        await page.setViewportSize({ width: 1440, height: 900 });
        await signIn(page, server, screen.path);
        await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
        await screen.ready?.(page, server, problems);
        await settle(page);
        await page.waitForTimeout(500);
        const dir = path.join(OUT, testInfo.project.name);
        await page.screenshot({ path: path.join(dir, `${screen.name}-1440.png`) });
        await page.setViewportSize(PHONE);
        await settle(page);
        await page.waitForTimeout(400);
        await page.screenshot({ path: path.join(dir, `${screen.name}-390.png`) });
      });
    }
  });
}
