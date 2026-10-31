import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, useRouterState } from "@tanstack/react-router";
import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useRenderedPathname } from "./renderedPath";

/** A frame that says, in text, the path each way of reading it gives. */
function Frame() {
  const rendered = useRenderedPathname();
  const loading = useRouterState({ select: (state) => state.location.pathname });
  return (
    <div>
      <p data-testid="frame">{rendered}</p>
      <p data-testid="router">{loading}</p>
      <Outlet />
    </div>
  );
}

describe("the path the frame follows", () => {
  it("stays on the page that is drawn while the next one is loaded, and moves with it", async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const root = createRootRoute({ component: Frame });
    const wizard = createRoute({ getParentRoute: () => root, path: "/apps/new", component: () => <h1>New application</h1> });
    // The page the wizard hands over to: its data is what is awaited before it can be drawn.
    const app = createRoute({
      getParentRoute: () => root,
      path: "/apps/$domain",
      loader: async () => {
        await held;
        return null;
      },
      component: () => <h1>An application</h1>,
    });
    const router = createRouter({ routeTree: root.addChildren([wizard, app]), history: createMemoryHistory({ initialEntries: ["/apps/new"] }) });
    render(<RouterProvider router={router} />);
    await screen.findByRole("heading", { name: "New application" });
    expect(screen.getByTestId("frame")).toHaveTextContent("/apps/new");

    act(() => {
      void router.navigate({ to: "/apps/$domain", params: { domain: "shop.example.com" } });
    });
    // The router already says where it is going, and the page on screen is still the wizard: the frame is not told yet.
    await waitFor(() => {
      expect(screen.getByTestId("router")).toHaveTextContent("/apps/shop.example.com");
    });
    expect(screen.getByRole("heading", { name: "New application" })).toBeInTheDocument();
    expect(screen.getByTestId("frame")).toHaveTextContent("/apps/new");

    act(() => {
      release();
    });
    await screen.findByRole("heading", { name: "An application" });
    expect(screen.getByTestId("frame")).toHaveTextContent("/apps/shop.example.com");
  });
});
