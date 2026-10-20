import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import { TAB_DOMAIN, appRoutes, screenWidth } from "../testRoutes";

const DEPLOYMENT = {
  id: 30,
  domain: TAB_DOMAIN,
  status: "success",
  triggered_by: "cli",
  git_commit: "a94c0e2",
  git_branch: "main",
  started_at: "2026-09-25T18:00:00",
  finished_at: "2026-09-25T18:00:40",
  duration_s: 40,
  error: null,
  has_log: true,
};

const LOG = [
  "[2026-09-25 18:00:00] [1/9] Fetching source...",
  "[2026-09-25 18:00:03] npm install",
  "[2026-09-25 18:00:05] npm WARN deprecated left-pad@1.0.0",
  "[2026-09-25 18:00:07] connecting to postgres://app:hunter2-password@db:5432/app",
  "[2026-09-25 18:00:09] Error: Cannot find module 'express'",
  "[2026-09-25 18:00:11] done",
  "",
].join("\n");

afterEach(() => {
  vi.unstubAllGlobals();
});

async function open() {
  screenWidth(1440);
  // The virtualizer sizes its window from offsetHeight, which jsdom reports as 0.
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
  const backend = fakeBackend(
    appRoutes(
      {},
      {
        "GET /api/deployments": () => json(200, { items: [DEPLOYMENT], total: 1, next_before_id: null }),
        "GET /api/deployments/30": () => json(200, DEPLOYMENT),
        "GET /api/deployments/30/log": ({ search }) => {
          // What the server would do for a copy to share: the same log with the password masked.
          const sanitized = search.get("sanitize") === "true";
          const content = sanitized ? LOG.replace("hunter2-password", "***") : LOG;
          return json(200, { content, truncated: false, missing_reason: null });
        },
        "GET /api/jobs": () => json(200, { jobs: [], total: 0, active: 0 }),
      },
    ),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}/deployments/30`);
  // The first test of the file also loads the page's code: a slow machine takes more than a second.
  const region = await screen.findByRole("region", { name: `Build log of deployment 30 of ${TAB_DOMAIN}` }, { timeout: 10_000 });
  await within(region).findByText("npm install", undefined, { timeout: 10_000 });
  return { ...harness, backend, region };
}

describe("reading a deploy's log", { timeout: 20_000 }, () => {
  it("shows every line first, and the viewer's chips narrow it to a level", async () => {
    const { region } = await open();
    for (const line of ["[1/9] Fetching source...", "npm install", "done"]) {
      expect(await within(region).findByText(line)).toBeInTheDocument();
    }

    await userEvent.click(screen.getByRole("button", { name: "Warning 1" }));

    await waitFor(
      () => {
        expect(within(region).queryByText("npm install")).not.toBeInTheDocument();
      },
      { timeout: 5_000 },
    );
    expect(within(region).getByText("npm WARN deprecated left-pad@1.0.0")).toBeInTheDocument();
    expect(within(region).queryByText("Error: Cannot find module 'express'")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Error 1" }));
    expect(await within(region).findByText("Error: Cannot find module 'express'")).toBeInTheDocument();
  });

  it("leaves the times out and puts them back, with the viewer's own toggle", async () => {
    const { region } = await open();
    expect(within(region).getAllByText("18:00:03")).not.toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Show times" }));
    await waitFor(
      () => {
        expect(within(region).queryByText("18:00:03")).not.toBeInTheDocument();
      },
      { timeout: 5_000 },
    );
    expect(within(region).getByText("npm install")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show times" }));
    expect(await within(region).findAllByText("18:00:03")).not.toHaveLength(0);
  });

  it("holds the share button's place while the log is read, so it does not push the viewer down when it comes", async () => {
    screenWidth(1440);
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
    let answer: (response: Response) => void = () => undefined;
    const slow = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    fakeBackend(
      appRoutes(
        {},
        {
          "GET /api/deployments": () => json(200, { items: [DEPLOYMENT], total: 1, next_before_id: null }),
          "GET /api/deployments/30": () => json(200, DEPLOYMENT),
          "GET /api/deployments/30/log": () => slow,
          "GET /api/jobs": () => json(200, { jobs: [], total: 0, active: 0 }),
        },
      ),
    );
    renderConsole(`/apps/${TAB_DOMAIN}/deployments/30`);

    const waiting = await screen.findByText("Loading the build log", undefined, { timeout: 10_000 });
    const section = waiting.closest("section");
    if (section === null) throw new Error("the build log has no section");
    // A box as tall as the button, before the log is there; no button yet.
    expect(section.querySelector('div[aria-hidden="true"] .h-control-sm')).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Copy to share" })).not.toBeInTheDocument();

    answer(json(200, { content: LOG, truncated: false, missing_reason: null }));
    expect(await screen.findByRole("button", { name: "Copy to share" })).toBeInTheDocument();
    expect(section.querySelector('div[aria-hidden="true"] .h-control-sm')).toBeNull();
  });

  it("has one control of each, the viewer's: the page adds no level or times control of its own", async () => {
    await open();

    expect(screen.queryByRole("radio", { name: "Warnings and errors" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Show times" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: "Filter by level" })).toHaveLength(1);
  });

  it("copies the whole log to share as the server prepares it, with the credentials masked", async () => {
    const { backend } = await open();
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });

    await userEvent.click(screen.getByRole("button", { name: "Copy to share" }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    const copied = String((writeText.mock.calls[0] as unknown[] | undefined)?.[0]);
    expect(copied).not.toContain("hunter2-password");
    expect(copied).toContain("postgres://app:***@db:5432/app");
    const asked = backend.callsTo("GET /api/deployments/30/log").at(-1);
    expect(asked?.search.get("sanitize")).toBe("true");
    expect(asked?.search.get("level")).toBe("all");
    expect(asked?.search.get("timestamps")).toBe("true");
    expect(await screen.findByText("Copied the log, ready to share")).toBeInTheDocument();
  });

  it("has no accessibility violations with the controls on the page", async () => {
    const { container } = await open();
    await expectNoAxeViolations(container);
  });

  it("speaks Spanish", async () => {
    await open();
    await act(() => setLocale("es"));
    expect(await screen.findByRole("button", { name: "Aviso 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copiar para compartir" })).toBeInTheDocument();
  });
});
