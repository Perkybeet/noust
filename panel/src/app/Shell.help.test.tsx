/**
 * The documentation for the page on screen (checklist N-18): the "?" of the top bar and the command
 * palette both lead to the page of the documentation that explains where the operator is, and the
 * link follows the operator from page to page. The session's panel does not say it again: each entry is
 * in one place on a desktop, and a phone, which has no "?", keeps them in its session sheet.
 */

import { act, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../test/axe";
import { renderConsole } from "../test/console";
import { fakeBackend, json, signedInRoutes } from "../test/fakes";
import { setLocale } from "./locale";

async function open(path: string, width = 1024) {
  Object.defineProperty(window, "innerWidth", { value: width, configurable: true });
  fakeBackend({
    ...signedInRoutes(),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
  });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  return harness;
}

type User = Awaited<ReturnType<typeof open>>["user"];

async function helpMenu(user: User) {
  await user.click(await screen.findByRole("button", { name: "Help" }));
  return screen.findByRole("menu");
}

async function sessionMenu(user: User) {
  await user.click(await screen.findByRole("button", { name: "Session and preferences" }));
  return screen.findByRole("dialog", { name: "Session" });
}

afterEach(() => {
  Object.defineProperty(window, "innerWidth", { value: 1024, configurable: true });
  vi.restoreAllMocks();
});

describe("documentation for this page", () => {
  it("is in the help menu, and leads to the heading that explains the page", async () => {
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    const { user } = await open("/settings/security");

    const menu = await helpMenu(user);
    await user.click(within(menu).getByRole("menuitem", { name: /Documentation for this page/ }));

    expect(opened).toHaveBeenCalledWith("https://github.com/Perkybeet/noust/blob/main/docs/security-sign-in.md#authentication", "_blank", "noopener,noreferrer");
  });

  it("follows the operator to another page", async () => {
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    const { user, router } = await open("/settings/about");
    await router.navigate({ to: "/cron" });
    await screen.findByRole("heading", { level: 1, name: /Scheduled tasks/i });

    const menu = await helpMenu(user);
    await user.click(within(menu).getByRole("menuitem", { name: /Documentation for this page/ }));

    expect(opened).toHaveBeenCalledWith("https://github.com/Perkybeet/noust/blob/main/docs/runtime.md#scheduled-tasks-what-ran-and-what-did-not", "_blank", "noopener,noreferrer");
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
    const menu = await helpMenu(user);

    await act(() => setLocale("es"));

    expect(await within(menu).findByRole("menuitem", { name: /Documentación de esta página/ })).toBeInTheDocument();
  });

  it("is said once on a desktop: the session's panel leaves it, and the shortcuts, to the help menu", async () => {
    const { user } = await open("/");

    const menu = await sessionMenu(user);

    expect(within(menu).queryByRole("link", { name: /Documentation for this page/ })).toBeNull();
    expect(within(menu).queryByRole("button", { name: /Keyboard shortcuts/ })).toBeNull();
    // What the panel is for is still there.
    expect(within(menu).getByRole("link", { name: "Account" })).toBeInTheDocument();
    expect(within(menu).getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("is in the session sheet of a phone, which has no help menu to put it in", async () => {
    const { user } = await open("/settings/security", 390);
    expect(screen.queryByRole("button", { name: "Help" })).toBeNull();

    await user.click(within(screen.getByRole("navigation", { name: "Areas" })).getByRole("button", { name: "More" }));
    const sheet = await screen.findByRole("dialog", { name: "All areas" });
    await user.click(within(sheet).getByRole("button", { name: "Your account" }));
    const session = await screen.findByRole("dialog", { name: "Session" });

    expect(within(session).getByRole("link", { name: /Documentation for this page/ })).toHaveAttribute(
      "href",
      "https://github.com/Perkybeet/noust/blob/main/docs/security-sign-in.md#authentication",
    );
    expect(within(session).getByRole("button", { name: /Keyboard shortcuts/ })).toBeInTheDocument();
  });

  it("leaves the menu free of accessibility violations", async () => {
    const { user, container } = await open("/");

    await sessionMenu(user);

    await expectNoAxeViolations(container, { page: true });
  });
});
