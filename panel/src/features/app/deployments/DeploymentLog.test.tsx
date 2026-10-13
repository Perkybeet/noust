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
  const region = await screen.findByRole("region", { name: `Build log of deployment 30 of ${TAB_DOMAIN}` });
  await within(region).findByText("npm install");
  return { ...harness, backend, region };
}

describe("reading a deploy's log", { timeout: 20_000 }, () => {
  it("shows every line first, and only warnings and errors when asked", async () => {
    const { region } = await open();
    for (const line of ["[1/9] Fetching source...", "npm install", "done"]) {
      expect(await within(region).findByText(line)).toBeInTheDocument();
    }

    await userEvent.click(screen.getByRole("radio", { name: "Warnings and errors" }));

    await waitFor(
      () => {
        expect(within(region).queryByText("npm install")).not.toBeInTheDocument();
      },
      { timeout: 5_000 },
    );
    expect(within(region).getByText("npm WARN deprecated left-pad@1.0.0")).toBeInTheDocument();
    expect(within(region).getByText("Error: Cannot find module 'express'")).toBeInTheDocument();
    expect(screen.getByText("Showing 2 of 6 lines.")).toBeInTheDocument();
  });

  it("shows only errors at the errors level, and says when nothing reads that way", async () => {
    const { region } = await open();

    await userEvent.click(screen.getByRole("radio", { name: "Errors" }));

    await waitFor(
      () => {
        expect(within(region).queryByText("npm WARN deprecated left-pad@1.0.0")).not.toBeInTheDocument();
      },
      { timeout: 5_000 },
    );
    expect(within(region).getByText("Error: Cannot find module 'express'")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 6 lines.")).toBeInTheDocument();
  });

  it("leaves the times out and puts them back", async () => {
    const { region } = await open();
    expect(within(region).getAllByText("18:00:03")).not.toHaveLength(0);

    await userEvent.click(screen.getByRole("checkbox", { name: "Show times" }));
    await waitFor(
      () => {
        expect(within(region).queryByText("18:00:03")).not.toBeInTheDocument();
      },
      { timeout: 5_000 },
    );
    expect(within(region).getByText("npm install")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "Show times" }));
    expect(await within(region).findAllByText("18:00:03")).not.toHaveLength(0);
  });

  it("copies the log to share as the server prepares it, at the level and with the times shown", async () => {
    const { backend } = await open();
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    await userEvent.click(screen.getByRole("radio", { name: "Warnings and errors" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Show times" }));

    await userEvent.click(screen.getByRole("button", { name: "Copy to share" }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    const copied = String((writeText.mock.calls[0] as unknown[] | undefined)?.[0]);
    expect(copied).not.toContain("hunter2-password");
    expect(copied).toContain("postgres://app:***@db:5432/app");
    const asked = backend.callsTo("GET /api/deployments/30/log").at(-1);
    expect(asked?.search.get("sanitize")).toBe("true");
    expect(asked?.search.get("level")).toBe("warn");
    expect(asked?.search.get("timestamps")).toBe("false");
    expect(await screen.findByText("Copied the log, ready to share")).toBeInTheDocument();
  });

  it("has no accessibility violations with the controls on the page", async () => {
    const { container } = await open();
    await expectNoAxeViolations(container);
  });

  it("speaks Spanish", async () => {
    await open();
    await act(() => setLocale("es"));
    expect(await screen.findByRole("radio", { name: "Avisos y errores" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copiar para compartir" })).toBeInTheDocument();
  });
});
