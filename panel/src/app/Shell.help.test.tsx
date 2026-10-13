/**
 * The documentation for the page on screen (checklist N-18): the session menu and the command
 * palette both lead to the page of the documentation that explains where the operator is, and the
 * link follows the operator from page to page.
 */

import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../test/axe";
import { renderConsole } from "../test/console";
import { fakeBackend, json, signedInRoutes } from "../test/fakes";
import { setLocale } from "./locale";

async function open(path: string) {
  fakeBackend({
    ...signedInRoutes(),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
  });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  return harness;
}

async function sessionMenu(user: Awaited<ReturnType<typeof open>>["user"]) {
  await user.click(await screen.findByRole("button", { name: "Session and preferences" }));
  return screen.findByRole("dialog", { name: "Session" });
}

describe("documentation for this page", () => {
  it("is in the session menu, and leads to the heading that explains the page", async () => {
    const { user } = await open("/settings/security");

    const menu = await sessionMenu(user);
    const link = within(menu).getByRole("link", { name: /Documentation for this page/ });

    expect(link).toHaveAttribute("href", "https://github.com/Perkybeet/noust/blob/main/docs/security.md#authentication");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("follows the operator to another page", async () => {
    const { user, router } = await open("/settings/about");
    await router.navigate({ to: "/cron" });
    await screen.findByRole("heading", { level: 1, name: /Cron/i });

    const menu = await sessionMenu(user);

    expect(within(menu).getByRole("link", { name: /Documentation for this page/ })).toHaveAttribute(
      "href",
      "https://github.com/Perkybeet/noust/blob/main/README.md#cron",
    );
  });

  it("is a command of the palette too", async () => {
    const { user } = await open("/");

    await user.keyboard("{Control>}k{/Control}");
    const input = await screen.findByRole("combobox", { name: /Search/ });
    await user.type(input, "documentation");

    expect(await screen.findByRole("option", { name: /Open the documentation for this page/ })).toBeInTheDocument();
  });

  it("is in Spanish when the console is", async () => {
    const { user } = await open("/");
    const menu = await sessionMenu(user);

    await act(() => setLocale("es"));

    expect(await within(menu).findByRole("link", { name: /Documentación de esta página/ })).toBeInTheDocument();
  });

  it("leaves the menu free of accessibility violations", async () => {
    const { user, container } = await open("/");

    await sessionMenu(user);

    await expectNoAxeViolations(container, { page: true });
  });
});
