import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import { TAB_DOMAIN, appRoutes, screenWidth } from "./testRoutes";

const RESTART = {
  domain: TAB_DOMAIN,
  pending: true,
  needs: "restart",
  since: "2026-10-13T10:00:00",
  deployment_id: 41,
  baseline_created: false,
  counts: { restart: 2, rebuild: 0, next_deploy: 0 },
  items: [
    { area: "limits", key: "memory_max_mb", change: "changed", before: "256", after: "512", needs: "restart" },
    { area: "environment", key: "FEATURE_FLAG", change: "added", before: null, after: null, needs: "restart" },
  ],
};

async function open(pending: object, extra: Parameters<typeof appRoutes>[1] = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    appRoutes(
      {},
      {
        [`GET /api/apps/${TAB_DOMAIN}/pending`]: () => json(200, pending),
        "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
        ...extra,
      },
    ),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}`);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  return { ...harness, backend };
}

describe("what an application is set to that it does not run with yet", { timeout: 20_000 }, () => {
  it("says how many settings are not applied and what applying them takes, on the app's page", async () => {
    await open(RESTART);

    const banner = await screen.findByText("2 settings are not applied yet");
    expect(banner).toBeInTheDocument();
    expect(screen.getByText(/Restart it to apply them\./)).toBeInTheDocument();
  });

  it("says nothing when the application runs with what it is set to", async () => {
    const { backend } = await open({ ...RESTART, pending: false, needs: null, items: [], counts: {} });

    await waitFor(() => {
      expect(backend.callsTo(`GET /api/apps/${TAB_DOMAIN}/pending`)).not.toHaveLength(0);
    });
    expect(screen.queryByText(/not applied yet/)).not.toBeInTheDocument();
  });

  it("lists each setting with what it was and is, never a secret's value, and restarts from there", async () => {
    const { backend } = await open(RESTART, {
      [`POST /api/apps/${TAB_DOMAIN}/restart`]: () => json(200, { success: true, message: "Restarted", domain: TAB_DOMAIN }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Review" }));

    const dialog = await screen.findByRole("dialog", { name: `Settings not applied to ${TAB_DOMAIN}` });
    const list = within(dialog).getByRole("list", { name: `Settings not applied to ${TAB_DOMAIN}` });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText("memory_max_mb")).toBeInTheDocument();
    expect(within(list).getByText("was 256")).toBeInTheDocument();
    expect(within(list).getByText("now 512")).toBeInTheDocument();
    expect(within(list).getByText("FEATURE_FLAG")).toBeInTheDocument();
    expect(within(list).queryByText(/hunter2/)).not.toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Restart now" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/restart`)).toHaveLength(1);
    });
  });

  it("offers a build when something needs one, and not a restart", async () => {
    const rebuild = {
      ...RESTART,
      needs: "rebuild",
      counts: { restart: 0, rebuild: 1, next_deploy: 0 },
      items: [{ area: "build", key: "", change: "changed", before: null, after: null, needs: "rebuild" }],
    };
    await open(rebuild);
    await userEvent.click(await screen.findByRole("button", { name: "Review" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Build again now" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Restart now" })).not.toBeInTheDocument();
  });

  it("offers nothing to press for what only applies at the next deploy", async () => {
    const later = {
      ...RESTART,
      needs: "next_deploy",
      counts: { restart: 0, rebuild: 0, next_deploy: 1 },
      items: [{ area: "health", key: "path", change: "changed", before: "/", after: "/healthz", needs: "next_deploy" }],
    };
    await open(later);

    expect(await screen.findByText("1 setting is not applied yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: /now$/ })).not.toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    const { container } = await open(RESTART);
    await screen.findByText("2 settings are not applied yet");
    await expectNoAxeViolations(container);

    await act(() => setLocale("es"));
    expect(await screen.findByText("2 ajustes todavía no se aplican")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revisar" })).toBeInTheDocument();
  });
});
