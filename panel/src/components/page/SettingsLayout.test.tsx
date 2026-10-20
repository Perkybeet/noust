import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { Mono } from "../ui/Mono";
import { SettingsLayout } from "./SettingsLayout";
import type { SettingsNavItem } from "./SettingsLayout";

async function renderRouted(ui: ReactNode) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "$", component: () => <main>{ui}</main> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ["/settings"] }) });
  const client = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findByRole("main");
  return view;
}

/** `count` sections, each its own URL. */
function sections(count: number, extra: (index: number) => Partial<SettingsNavItem> = () => ({})): SettingsNavItem[] {
  return Array.from({ length: count }, (_, index) => ({ to: `/settings/s${String(index)}`, label: `Section ${String(index)}`, ...extra(index) }));
}

function nav(): HTMLElement {
  const found = screen.getAllByRole("navigation", { name: "Settings" })[0];
  if (!found) throw new Error("no navigation");
  return found;
}

describe("SettingsLayout, a long list", () => {
  describe("the filter", () => {
    it("is there from twelve sections and not before", async () => {
      const { unmount } = await renderRouted(
        <SettingsLayout label="Settings" items={sections(11)}>
          <p>x</p>
        </SettingsLayout>,
      );
      expect(screen.queryByRole("searchbox", { name: "Filter sections" })).not.toBeInTheDocument();
      unmount();
      await renderRouted(
        <SettingsLayout label="Settings" items={sections(12)}>
          <p>x</p>
        </SettingsLayout>,
      );
      expect(screen.getByRole("searchbox", { name: "Filter sections" })).toBeInTheDocument();
    });

    it("can be asked for sooner", async () => {
      await renderRouted(
        <SettingsLayout label="Settings" items={sections(5)} filterFrom={5}>
          <p>x</p>
        </SettingsLayout>,
      );
      expect(screen.getByRole("searchbox", { name: "Filter sections" })).toBeInTheDocument();
    });

    it("is the page's search, so the / shortcut reaches it", async () => {
      await renderRouted(
        <SettingsLayout label="Settings" items={sections(12)}>
          <p>x</p>
        </SettingsLayout>,
      );
      expect(screen.getByRole("searchbox", { name: "Filter sections" })).toHaveAttribute("data-page-search");
    });

    it("narrows the list to the sections whose name has every word typed, in any case", async () => {
      await renderRouted(
        <SettingsLayout label="Settings" items={sections(14, (index) => ({ label: ["Alert rules", "Channels", "API tokens"][index] ?? `Section ${String(index)}` }))}>
          <p>x</p>
        </SettingsLayout>,
      );
      await userEvent.type(screen.getByRole("searchbox", { name: "Filter sections" }), "ALERT rul");
      const links = within(nav()).getAllByRole("link");
      expect(links.map((link) => link.textContent)).toEqual(["Alert rules"]);
    });

    it("finds a section by its old name", async () => {
      await renderRouted(
        <SettingsLayout label="Settings" items={sections(13, (index) => (index === 0 ? { label: "Channels", keywords: "Notifications" } : {}))}>
          <p>x</p>
        </SettingsLayout>,
      );
      await userEvent.type(screen.getByRole("searchbox", { name: "Filter sections" }), "notif");
      expect(within(nav()).getAllByRole("link").map((link) => link.textContent)).toEqual(["Channels"]);
    });

    it("says so when nothing matches, and gives the whole list back when it is cleared", async () => {
      await renderRouted(
        <SettingsLayout label="Settings" items={sections(12)}>
          <p>x</p>
        </SettingsLayout>,
      );
      const box = screen.getByRole("searchbox", { name: "Filter sections" });
      await userEvent.type(box, "zzz");
      expect(screen.getByRole("status")).toHaveTextContent("No section matches this filter.");
      expect(within(nav()).queryAllByRole("link")).toHaveLength(0);
      await userEvent.clear(box);
      expect(within(nav()).getAllByRole("link")).toHaveLength(12);
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });

    it("keeps the destructive section last, and in the filter", async () => {
      await renderRouted(
        <SettingsLayout label="Settings" items={[...sections(12), { to: "/settings/delete", label: "Delete application", danger: true }]}>
          <p>x</p>
        </SettingsLayout>,
      );
      await userEvent.type(screen.getByRole("searchbox", { name: "Filter sections" }), "delete");
      expect(within(nav()).getAllByRole("link").map((link) => link.textContent)).toEqual(["Delete application"]);
    });

    it("is named in Spanish", async () => {
      await act(async () => {
        await setLocale("es");
      });
      await renderRouted(
        <SettingsLayout label="Settings" items={sections(12)}>
          <p>x</p>
        </SettingsLayout>,
      );
      expect(screen.getByRole("searchbox", { name: "Filtrar secciones" })).toBeInTheDocument();
    });
  });

  describe("the headings", () => {
    const grouped = [
      { to: "/settings", label: "General", group: "This server" },
      { to: "/settings/variables", label: "Variables", group: "This server" },
      { to: "/settings/channels", label: "Channels", group: "Monitoring" },
      { to: "/settings/alerts", label: "Alert rules", group: "Monitoring" },
      { to: "/settings/accounts", label: "Accounts", group: "People and access" },
    ] satisfies SettingsNavItem[];

    it("names each group once, over the sections that are its own", async () => {
      await renderRouted(
        <SettingsLayout label="Settings" items={grouped}>
          <p>x</p>
        </SettingsLayout>,
      );
      const monitoring = within(nav()).getByRole("list", { name: "Monitoring" });
      expect(within(monitoring).getAllByRole("link").map((link) => link.textContent)).toEqual(["Channels", "Alert rules"]);
      expect(within(nav()).getByRole("list", { name: "People and access" })).toBeInTheDocument();
    });

    it("leads the heading with the scope on a fleet: the server's name, then the group", async () => {
      await renderRouted(
        <SettingsLayout
          label="Settings"
          items={[
            { to: "/settings/m", label: "Channels", group: "Monitoring", scope: <Mono>web-2</Mono>, scopeKey: "web-2" },
            { to: "/settings/d", label: "API tokens", group: "Developers", scope: <Mono>web-2</Mono>, scopeKey: "web-2" },
            { to: "/settings/a", label: "Accounts", group: "People and access", scope: "Central", scopeKey: "central" },
          ]}
        >
          <p>x</p>
        </SettingsLayout>,
      );
      expect(within(nav()).getByRole("list", { name: "web-2 Monitoring" })).toBeInTheDocument();
      expect(within(nav()).getByRole("list", { name: "web-2 Developers" })).toBeInTheDocument();
      expect(within(nav()).getByRole("list", { name: "Central People and access" })).toBeInTheDocument();
      expect(screen.getAllByText("web-2")[0]).toHaveAttribute("translate", "no");
    });

    it("sets one block apart from the next, and not one group from another in the same block", async () => {
      await renderRouted(
        <SettingsLayout
          label="Settings"
          items={[
            { to: "/settings/m", label: "Channels", group: "Monitoring", scope: "web-2" },
            { to: "/settings/d", label: "API tokens", group: "Developers", scope: "web-2" },
            { to: "/settings/a", label: "Accounts", group: "People", scope: "Central" },
          ]}
        >
          <p>x</p>
        </SettingsLayout>,
      );
      const [monitoring, developers, people] = ["web-2 Monitoring", "web-2 Developers", "Central People"].map((name) => within(nav()).getByRole("list", { name }).parentElement);
      expect(monitoring).not.toHaveClass("border-t");
      expect(developers).toHaveClass("mt-3");
      expect(developers).not.toHaveClass("border-t");
      expect(people).toHaveClass("border-t", "pt-4");
    });

    it("shows the group alone on a lone server", async () => {
      await renderRouted(
        <SettingsLayout label="Settings" items={grouped}>
          <p>x</p>
        </SettingsLayout>,
      );
      expect(within(nav()).getByRole("list", { name: "Monitoring" })).toBeInTheDocument();
      expect(within(nav()).queryByText("·")).not.toBeInTheDocument();
    });

    it("lists a heading only while one of its sections matches", async () => {
      await renderRouted(
        <SettingsLayout label="Settings" items={[...grouped, ...sections(8)]}>
          <p>x</p>
        </SettingsLayout>,
      );
      await userEvent.type(screen.getByRole("searchbox", { name: "Filter sections" }), "alert");
      expect(within(nav()).getByRole("list", { name: "Monitoring" })).toBeInTheDocument();
      expect(within(nav()).queryByRole("list", { name: "This server" })).not.toBeInTheDocument();
    });
  });

  it("scrolls inside the screen when it is taller than it, with room for its focus ring", async () => {
    const { container } = await renderRouted(
      <SettingsLayout label="Settings" items={sections(24)}>
        <p>x</p>
      </SettingsLayout>,
    );
    const sticky = container.querySelector("[data-slot='nav'] > div");
    expect(sticky).toHaveClass("sticky", "overflow-y-auto", "p-1", "-m-1");
  });

  it("has no accessibility violations, filtered and not", async () => {
    const { container } = await renderRouted(
      <SettingsLayout
        label="Settings"
        items={[
          ...sections(12, (index) => ({ group: index < 6 ? "Monitoring" : "Developers", scope: <Mono>web-2</Mono>, scopeKey: "web-2" })),
          { to: "/settings/delete", label: "Delete", danger: true },
        ]}
      >
        <p>x</p>
      </SettingsLayout>,
    );
    await expectNoAxeViolations(container);
    await userEvent.type(screen.getByRole("searchbox", { name: "Filter sections" }), "section 1");
    await expectNoAxeViolations(container);
  });
});
