/**
 * How an application runs, against the real backend (item 189): the new-application wizard reads a directory with the
 * real `POST /api/apps/inspect`, which now says how it can run on this server, and the application's page says how it
 * runs from the real `GET /api/apps/{domain}/runs`. The machine the console server models has no Docker, so what is
 * seen here is a server where a container is not possible, said with the doctor's own words; the two cards, which
 * need Docker, are the component tests' and the captures of the lane's. Both themes, with the CSP and console gates.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";
import { continueTo, inspectSource, wizardSource } from "./wizard-sources";

test("a lone Dockerfile says it runs in a container and that this server has no Docker to run it, in the server's words", async ({ page, consoleServer, problems }) => {
  test.setTimeout(120_000);
  await signIn(page, consoleServer, "/apps/new");
  await inspectSource(page, consoleServer, problems, await wizardSource(page, "container-api"));
  await page.getByLabel("Domain", { exact: true }).fill("caja-como.example.net");
  await continueTo(page, "Configuration");

  const how = page.getByRole("group", { name: "How it runs" });
  // Nothing in a Dockerfile runs directly, and the machine cannot run a container: neither way, and what is in the way.
  await expect(how.getByText("Neither way is possible on this machine now; what is in the way is said below.")).toBeVisible();
  await expect(how.getByText("In a container it is not possible yet")).toBeVisible();
  // There is nothing to choose, so the type is where it always was.
  await expect(page.getByRole("combobox", { name: "App type" })).toHaveText(/Container/);
  await expect(how.getByRole("radio")).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "how a lone Dockerfile runs on a server without Docker");
});

test("a project with one way says how it will run on the last step, and the page of the application says how it runs", async ({ page, consoleServer, problems }) => {
  test.setTimeout(120_000);
  await signIn(page, consoleServer, "/apps/new");
  await inspectSource(page, consoleServer, problems, await wizardSource(page, "landing"));
  await page.getByLabel("Domain", { exact: true }).fill("paisaje.example.net");
  await continueTo(page, "Deploy");

  // One type and nothing to choose: Configuration was passed over, and the last step says how it will run.
  const summary = page.getByRole("main");
  await expect(summary.getByText("Directly on the server")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the summary that says how it will run");

  // The page of an application that is deployed says it with the service that runs it.
  await page.goto("/apps/tienda.example.org");
  const section = page.getByRole("region", { name: "How it is running" });
  await expect(section.getByText("Directly on the server, as a system service.")).toBeVisible();
  await expect(section.getByRole("link", { name: "See the service" })).toBeVisible();
  await expect(section.getByText(/It is not converted on the fly/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the application's page with how it runs");
});

test("the real inspection says Next.js runs directly, as it always did, and why a container cannot be", async ({ page, consoleServer, problems }) => {
  test.setTimeout(120_000);
  await signIn(page, consoleServer, "/apps/new");
  const source = await wizardSource(page, "storefront");
  const read = page.waitForResponse((response) => response.url().endsWith("/api/apps/inspect") && response.ok());
  await inspectSource(page, consoleServer, problems, source);
  const body = (await (await read).json()) as { ways?: { choice: boolean; proposed: string; server: { status: string; app_type: string }; container: { status: string; reason: { code: string } | null } } };
  // What Noust always proposed is still what it proposes; a container is said not to be possible, with its reason.
  expect(body.ways).toMatchObject({ choice: false, proposed: "server", server: { status: "yes", app_type: "nextjs" }, container: { status: "no" } });
});
