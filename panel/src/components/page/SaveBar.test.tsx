import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, RouterProvider, createBrowserHistory, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import type { RouterHistory } from "@tanstack/react-router";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { PageHeader } from "../../app/PageHeader";
import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "../ui/Button";
import { SaveBar } from "./SaveBar";

/** The page to leave for; the typed router knows the console's routes, not this test's. */
const OTHER = "/other" as "/";

/** A settings page with a field and a bar, and a second page to leave for. */
function Form({ guard = true, start = 0 }: { guard?: boolean; start?: number }) {
  const [changes, setChanges] = useState(start);
  return (
    <main>
      <PageHeader title="Settings" primaryAction={<Button variant="primary">Update</Button>} />
      <button type="button" onClick={() => setChanges((n) => n + 1)}>
        Edit a field
      </button>
      <Link to={OTHER}>Other page</Link>
      <Link to="/" search={{ tab: "b" } as never}>
        Same page, other tab
      </Link>
      <SaveBar changes={changes} onDiscard={() => setChanges(0)} onSave={() => setChanges(0)} guard={guard} />
    </main>
  );
}

async function routed(ui: ReactNode, history: RouterHistory = createMemoryHistory({ initialEntries: ["/"] })) {
  const root = createRootRoute();
  const index = createRoute({ getParentRoute: () => root, path: "/", component: () => ui, validateSearch: (search: Record<string, unknown>) => ({ tab: typeof search["tab"] === "string" ? search["tab"] : "a" }) });
  const other = createRoute({ getParentRoute: () => root, path: "/other", component: () => <h1>The other page</h1> });
  const router = createRouter({ routeTree: root.addChildren([index, other]), history });
  const client = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { level: 1, name: "Settings" });
  return router;
}

describe("SaveBar", () => {
  describe("the question before leaving", () => {
    it("asks once, with the focus on keeping the edits, and says how many would be lost", async () => {
      const router = await routed(<Form />);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await userEvent.click(screen.getByRole("link", { name: "Other page" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Discard unsaved changes?" });
      expect(dialog).toHaveAccessibleDescription("Leaving this page discards 2 unsaved changes.");
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();
      });
      expect(router.state.location.pathname).toBe("/");
    });

    it("stays when told to keep editing, and the changes are still there", async () => {
      const router = await routed(<Form />);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await userEvent.click(screen.getByRole("link", { name: "Other page" }));
      await screen.findByRole("alertdialog");
      await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
      await waitFor(() => {
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      });
      expect(router.state.location.pathname).toBe("/");
      expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    });

    it("leaves when told to discard", async () => {
      const router = await routed(<Form />);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await userEvent.click(screen.getByRole("link", { name: "Other page" }));
      await screen.findByRole("alertdialog");
      await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
      expect(await screen.findByRole("heading", { name: "The other page" })).toBeInTheDocument();
      expect(router.state.location.pathname).toBe("/other");
    });

    it("asks nothing when there is nothing to lose", async () => {
      const router = await routed(<Form />);
      await userEvent.click(screen.getByRole("link", { name: "Other page" }));
      expect(await screen.findByRole("heading", { name: "The other page" })).toBeInTheDocument();
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      expect(router.state.location.pathname).toBe("/other");
    });

    it("asks nothing after the changes were saved or discarded", async () => {
      await routed(<Form />);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await userEvent.click(screen.getByRole("link", { name: "Other page" }));
      expect(await screen.findByRole("heading", { name: "The other page" })).toBeInTheDocument();
    });

    it("does not stop a change of tab on the same page", async () => {
      const router = await routed(<Form />);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await userEvent.click(screen.getByRole("link", { name: "Same page, other tab" }));
      await waitFor(() => {
        expect(router.state.location.search).toEqual({ tab: "b" });
      });
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });

    it("warns the browser before the tab is closed, only while there are changes", async () => {
      window.history.replaceState({}, "", "/");
      await routed(<Form />, createBrowserHistory());
      /** Whether the browser would be asked to hold the tab: the page cancels the event. */
      const holds = (): boolean => {
        const event = new Event("beforeunload", { cancelable: true });
        window.dispatchEvent(event);
        return event.defaultPrevented;
      };
      expect(holds()).toBe(false);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await waitFor(() => {
        expect(holds()).toBe(true);
      });
      await userEvent.click(screen.getByRole("button", { name: "Discard" }));
      await waitFor(() => {
        expect(holds()).toBe(false);
      });
    });

    it("lets a page ask its own question: guard={false} draws none", async () => {
      await routed(<Form guard={false} />);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await userEvent.click(screen.getByRole("link", { name: "Other page" }));
      expect(await screen.findByRole("heading", { name: "The other page" })).toBeInTheDocument();
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });

    it("has no accessibility violations with the question open", async () => {
      await routed(<Form />);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await userEvent.click(screen.getByRole("link", { name: "Other page" }));
      await expectNoAxeViolations(await screen.findByRole("alertdialog"));
    });

    it("asks in Spanish", async () => {
      await act(async () => {
        await setLocale("es");
      });
      await routed(<Form start={3} />);
      await userEvent.click(screen.getByRole("link", { name: "Other page" }));
      const dialog = await screen.findByRole("alertdialog", { name: "¿Descartar los cambios sin guardar?" });
      expect(dialog).toHaveAccessibleDescription("Si sales de esta página se descartan 3 cambios sin guardar.");
      expect(screen.getByRole("button", { name: "Seguir editando" })).toBeInTheDocument();
    });
  });

  describe("the primary of the view", () => {
    it("is the header's while nothing is edited, and Save's while something is", async () => {
      await routed(<Form />);
      expect(screen.getByRole("button", { name: "Update" })).toHaveAttribute("data-variant", "primary");
      expect(primaries()).toEqual(["Update"]);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await waitFor(() => {
        expect(primaries()).toEqual(["Save"]);
      });
      // The header keeps its label: it changes colour and nothing else.
      expect(screen.getByRole("button", { name: "Update" })).toHaveAttribute("data-variant", "secondary");
    });

    it("gives the primary back to the header when the changes are discarded", async () => {
      await routed(<Form />);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await waitFor(() => {
        expect(primaries()).toEqual(["Save"]);
      });
      await userEvent.click(screen.getByRole("button", { name: "Discard" }));
      await waitFor(() => {
        expect(primaries()).toEqual(["Update"]);
      });
    });

    it("leaves the page's header alone when the form is not the page's own: a drawer's bar has its own primary", async () => {
      function Drawerish() {
        const [changes, setChanges] = useState(0);
        return (
          <main>
            <PageHeader title="Settings" primaryAction={<Button variant="primary">Update</Button>} />
            <button type="button" onClick={() => setChanges(1)}>
              Edit a field
            </button>
            <SaveBar changes={changes} onDiscard={() => setChanges(0)} onSave={() => setChanges(0)} guard={false} yieldPrimary={false} />
          </main>
        );
      }
      await routed(<Drawerish />);
      await userEvent.click(screen.getByRole("button", { name: "Edit a field" }));
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("data-variant", "primary");
      });
      expect(screen.getByRole("button", { name: "Update" })).toHaveAttribute("data-variant", "primary");
    });

    it("keeps the header's primary while no bar holds changes, even with a bar on the page", async () => {
      await routed(<Form />);
      expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("data-variant", "secondary");
    });
  });

  it("is drawn alone, with no router to ask: no question, no failure", () => {
    render(<SaveBar changes={2} onSave={() => undefined} onDiscard={() => undefined} />);
    expect(screen.getByText("2 unsaved changes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("data-variant", "primary");
  });
});

/** The labels of the primary buttons on screen: the contract allows one. */
function primaries(): string[] {
  return [...document.querySelectorAll('[data-variant="primary"]')].map((button) => button.textContent);
}
