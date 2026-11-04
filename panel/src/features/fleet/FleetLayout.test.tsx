import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderConsole } from "../../test/console";
import { fakeBackend } from "../../test/fakes";
import { SHOP, placementRoutes } from "./placements/testFixtures";

/**
 * The texts of the page a link leads to are read before it is drawn (routes/__root.tsx), which is
 * the time the previous page stays on screen. The test holds that read for one address, so the
 * moment between pressing a link and the next page arriving lasts as long as the test wants.
 */
const held = vi.hoisted(() => ({ path: "", release: (): void => undefined, until: null as Promise<void> | null }));

vi.mock("../../i18n/routeNamespaces", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../i18n/routeNamespaces")>();
  return {
    ...actual,
    loadRouteNamespaces: (...args: Parameters<typeof actual.loadRouteNamespaces>) => {
      const [locale, location] = args;
      const loading = actual.loadRouteNamespaces(locale, location);
      return held.until !== null && location.pathname === held.path ? Promise.all([loading, held.until]).then(() => undefined) : loading;
    },
  };
});

describe("the fleet's frame while the next page of the fleet arrives", () => {
  it("draws one title: the previous page's own, until the next page is the one on screen", { timeout: 30_000 }, async () => {
    fakeBackend(placementRoutes());
    const { user, location } = renderConsole("/fleet/apps");
    // The applications of the fleet have a header of their own; the layout draws none above it.
    expect(await screen.findByRole("heading", { level: 1, name: "Applications" })).toBeInTheDocument();
    const views = await screen.findByRole("navigation", { name: "Applications sections" });

    held.path = "/fleet/placements";
    held.until = new Promise<void>((resolve) => {
      held.release = resolve;
    });
    await user.click(within(views).getByRole("link", { name: "On several servers" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/fleet/placements");
    });
    // The router is already at the new address and the page on screen is still the old one: a layout that follows the
    // address drew its own title above it.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("navigation", { name: "Applications sections" })).toHaveLength(1);
    expect(screen.queryByRole("heading", { level: 3, name: SHOP })).not.toBeInTheDocument();

    act(() => {
      held.release();
    });
    expect(await screen.findByRole("heading", { level: 3, name: SHOP })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});
