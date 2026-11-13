import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Power, ScrollText, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { ObjectMenu } from "./ObjectMenu";
import type { ObjectAction } from "./ObjectMenu";

async function renderRouted(ui: ReactNode) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "/", component: () => <main>{ui}</main> });
  const owner = createRoute({ getParentRoute: () => root, path: "/apps/$domain", component: () => <p>The application</p> });
  const router = createRouter({ routeTree: root.addChildren([page, owner]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const view = render(<RouterProvider router={router} />);
  await screen.findByRole("main");
  return { ...view, router };
}

const logs = (onSelect: () => void): ObjectAction => ({ id: "logs", label: "See logs", icon: <ScrollText />, onSelect });
const stop = (onSelect: () => void): ObjectAction => ({ id: "stop", label: "Stop", icon: <Power />, onSelect });
const remove = (onSelect: () => void): ObjectAction => ({ id: "remove", label: "Remove…", icon: <Trash2 />, onSelect, destructive: true });

describe("ObjectMenu", () => {
  it("is named after the object it is on, and offers the actions in order with what destroys last", async () => {
    const user = userEvent.setup();
    await renderRouted(<ObjectMenu name="nextcloud-app-1" actions={[remove(vi.fn()), logs(vi.fn()), stop(vi.fn())]} />);
    await user.click(screen.getByRole("button", { name: "Actions for nextcloud-app-1" }));
    const items = await screen.findAllByRole("menuitem");
    expect(items.map((item) => item.textContent)).toEqual(["See logs", "Stop", "Remove…"]);
    expect(screen.getAllByRole("separator")).toHaveLength(1);
  });

  it("runs the action that was chosen and nothing else", async () => {
    const user = userEvent.setup();
    const onStop = vi.fn();
    const onLogs = vi.fn();
    await renderRouted(<ObjectMenu name="web" actions={[logs(onLogs), stop(onStop)]} />);
    await user.click(screen.getByRole("button", { name: "Actions for web" }));
    await user.click(await screen.findByRole("menuitem", { name: "Stop" }));
    expect(onStop).toHaveBeenCalledOnce();
    expect(onLogs).not.toHaveBeenCalled();
  });

  it("draws no menu at all for an object that nothing may be done to, instead of a menu of disabled verbs", async () => {
    await renderRouted(<ObjectMenu name="shop-example-com-web-1" actions={[]} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("says once that the rest is done through the owner, as one line that leads there, never a disabled copy of each verb", async () => {
    const user = userEvent.setup();
    const { router } = await renderRouted(
      <ObjectMenu
        name="shop-example-com-web-1"
        actions={[logs(vi.fn())]}
        viaOwner={{ label: "Change from shop.example.com", description: "Restarting, stopping and removing are done from the application", icon: <Power />, target: { to: "/apps/$domain", params: { domain: "shop.example.com" } } }}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Actions for shop-example-com-web-1" }));
    const popup = await screen.findByRole("menu");
    expect(within(popup).queryAllByRole("menuitem", { hidden: false }).every((item) => item.getAttribute("aria-disabled") !== "true")).toBe(true);
    await user.click(within(popup).getByRole("menuitem", { name: /Change from shop.example.com/ }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/apps/shop.example.com");
    });
  });

  it("offers only the owner's line when the owner is the only way", async () => {
    const user = userEvent.setup();
    await renderRouted(
      <ObjectMenu name="x" actions={[]} viaOwner={{ label: "Change from shop.example.com", description: "From the application", icon: <Power />, target: { to: "/apps/$domain", params: { domain: "shop.example.com" } } }} />,
    );
    await user.click(screen.getByRole("button", { name: "Actions for x" }));
    expect(await screen.findAllByRole("menuitem")).toHaveLength(1);
  });

  it("has no accessibility violations", async () => {
    const { container } = await renderRouted(<ObjectMenu name="web" actions={[logs(vi.fn()), stop(vi.fn()), remove(vi.fn())]} />);
    await expectNoAxeViolations(container);
  });
});
