import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";
import { AuthLayout } from "./AuthLayout";
import { DashboardPage } from "./DashboardPage";
import { DetailPage } from "./DetailPage";
import { FileEditorPage } from "./FileEditorPage";
import { ListPage } from "./ListPage";
import { SaveBar } from "./SaveBar";
import { SettingsLayout } from "./SettingsLayout";
import { Stepper } from "./Stepper";
import { Wizard } from "./Wizard";

/** Renders inside a router (links) and a query client (the document title reads the session). */
async function renderRouted(ui: ReactNode, path = "/settings/general") {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "$", component: () => <main>{ui}</main> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [path] }) });
  const client = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findByRole("main");
  return view;
}

/** Text of the direct children of a template, in order, by their `data-slot`. */
function slots(container: HTMLElement): string[] {
  const template = container.querySelector("[data-template]");
  return [...(template?.querySelectorAll("[data-slot]") ?? [])].map((node) => node.getAttribute("data-slot") ?? "");
}

describe("ListPage (T1)", () => {
  it("stacks header, subset tabs, notice, filters, the list and the CLI hint, in that order", async () => {
    const { container } = await renderRouted(
      <ListPage
        header={{ title: "Backups", description: "Copies of your apps.", primaryAction: <Button variant="primary">Create backup</Button> }}
        tabs={<nav aria-label="Backup views">tabs</nav>}
        notice={<Notice title="Nothing verified yet" />}
        filters={<div role="search" aria-label="Filter backups" />}
        footer={<p>noust backup list</p>}
      >
        <p>the table</p>
      </ListPage>,
    );
    expect(container.querySelector("[data-template]")).toHaveAttribute("data-template", "list");
    expect(slots(container)).toEqual(["header", "tabs", "notice", "filters", "content", "footer"]);
    expect(screen.getByRole("heading", { level: 1, name: "Backups" }).closest("header")).not.toHaveClass("mb-8");
  });

  it("has no accessibility violations", async () => {
    const { container } = await renderRouted(
      <ListPage header={{ title: "Backups" }} filters={<div role="search" aria-label="Filter backups" />}>
        <p>the table</p>
      </ListPage>,
    );
    await expectNoAxeViolations(container);
  });
});

describe("DetailPage (T2)", () => {
  it("keeps a broken resource's banner between the header and the tabs, above every tab", async () => {
    const { container } = await renderRouted(
      <DetailPage
        header={{ title: "shop.example.com", mono: true }}
        banner={<Notice variant="banner" tone="error" title="The service failed" />}
        job={<p>job</p>}
        tabs={<nav aria-label="Application sections">tabs</nav>}
      >
        <p>tab content</p>
      </DetailPage>,
    );
    expect(slots(container)).toEqual(["header", "banner", "job", "tabs", "content"]);
  });
});

describe("SettingsLayout (T3)", () => {
  const items = [
    { to: "/settings/general", label: "General" },
    { to: "/settings/deploys", label: "Deploys" },
    { to: "/settings/delete", label: "Delete", danger: true },
  ];

  it("navigates its subsections as links, each its own URL, with the current one marked", async () => {
    await renderRouted(
      <SettingsLayout label="Application settings" items={items}>
        <p>General form</p>
      </SettingsLayout>,
    );
    const nav = screen.getAllByRole("navigation", { name: "Application settings" })[0];
    if (!nav) throw new Error("no nav");
    expect(within(nav).getByRole("link", { name: "General" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Deploys" })).toHaveAttribute("href", "/settings/deploys");
    expect(screen.getByText("General form")).toBeInTheDocument();
  });

  it("sets the destructive subsection apart, last", async () => {
    await renderRouted(
      <SettingsLayout label="Application settings" items={items}>
        <p>x</p>
      </SettingsLayout>,
    );
    const nav = screen.getAllByRole("navigation", { name: "Application settings" })[0];
    if (!nav) throw new Error("no nav");
    const lists = within(nav).getAllByRole("list");
    expect(lists).toHaveLength(2);
    const last = lists[1];
    if (!last) throw new Error("no second list");
    expect(within(last).getByRole("link", { name: "Delete" })).toBeInTheDocument();
  });

  it("on a phone, is an index on its own route and a back link on a subsection", async () => {
    const { container, unmount } = await renderRouted(
      <SettingsLayout label="Application settings" items={items} index>
        <p>General form</p>
      </SettingsLayout>,
    );
    expect(container.querySelector("[data-slot='content']")).toHaveClass("max-sm:hidden");
    unmount();
    await renderRouted(
      <SettingsLayout label="Application settings" items={items} backTo="/settings">
        <p>Deploys form</p>
      </SettingsLayout>,
      "/settings/deploys",
    );
    expect(screen.getByRole("link", { name: "All sections" })).toHaveAttribute("href", "/settings");
  });

  it("renders the one navigation the screen shows, so it is one landmark wherever it is read", async () => {
    const { unmount } = await renderRouted(
      <SettingsLayout label="Application settings" items={items} index>
        <p>General form</p>
      </SettingsLayout>,
    );
    // A phone's index (jsdom matches no media query): the list is the page.
    expect(screen.getAllByRole("navigation", { name: "Application settings" })).toHaveLength(1);
    unmount();
    await renderRouted(
      <SettingsLayout label="Application settings" items={items} backTo="/settings">
        <p>Deploys form</p>
      </SettingsLayout>,
      "/settings/deploys",
    );
    expect(screen.getAllByRole("navigation", { name: "Application settings" })).toHaveLength(1);
  });

  it("on a wider screen, shows the index's content beside the one navigation", async () => {
    const native = Object.getOwnPropertyDescriptor(window, "matchMedia");
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: (query: string) => ({ matches: true, media: query, onchange: null, addEventListener: () => undefined, removeEventListener: () => undefined }),
    });
    try {
      const { container } = await renderRouted(
        <SettingsLayout label="Application settings" items={items} index>
          <p>General form</p>
        </SettingsLayout>,
      );
      expect(screen.getAllByRole("navigation", { name: "Application settings" })).toHaveLength(1);
      expect(container.querySelector("[data-slot='nav']")).not.toBeNull();
      expect(container.querySelector("[data-slot='index']")).toBeNull();
      await expectNoAxeViolations(container);
    } finally {
      if (native !== undefined) Object.defineProperty(window, "matchMedia", native);
    }
  });

  it("has no accessibility violations on a phone's index", async () => {
    const { container } = await renderRouted(
      <SettingsLayout label="Application settings" items={items} index>
        <p>General form</p>
      </SettingsLayout>,
    );
    await expectNoAxeViolations(container);
  });

  it("has no accessibility violations", async () => {
    const { container } = await renderRouted(
      <SettingsLayout label="Application settings" items={items}>
        <p>General form</p>
      </SettingsLayout>,
    );
    await expectNoAxeViolations(container);
  });
});

describe("SaveBar", () => {
  it("counts the unsaved changes and makes Save the primary action while there are some", async () => {
    const onSave = vi.fn();
    const onDiscard = vi.fn();
    render(<SaveBar changes={2} onSave={onSave} onDiscard={onDiscard} />);
    expect(screen.getByText("2 unsaved changes")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toHaveAttribute("data-variant", "primary");
    await userEvent.click(save);
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(onSave).toHaveBeenCalledOnce();
    expect(onDiscard).toHaveBeenCalledOnce();
  });

  it("says there is nothing to save, and saves nothing, when nothing changed", () => {
    render(<SaveBar changes={0} onSave={() => undefined} onDiscard={() => undefined} />);
    expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
  });

  it("is always there, so its arrival never moves the page", () => {
    const { container } = render(<SaveBar changes={0} onSave={() => undefined} onDiscard={() => undefined} />);
    expect(container.firstElementChild).toHaveClass("sticky", "bottom-0");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<SaveBar changes={1} onSave={() => undefined} onDiscard={() => undefined} />);
    await expectNoAxeViolations(container);
  });
});

describe("DashboardPage (T4)", () => {
  it("leads with the key figures, then attention beside activity, then the charts", async () => {
    const { container } = await renderRouted(
      <DashboardPage
        header={{ title: "Overview" }}
        figures={<p>figures</p>}
        attention={<p>attention</p>}
        activity={<p>activity</p>}
        charts={<p>charts</p>}
      />,
    );
    expect(slots(container)).toEqual(["header", "figures", "attention", "activity", "charts"]);
  });

  it("shows only the first steps on an empty server", async () => {
    const { container } = await renderRouted(
      <DashboardPage header={{ title: "Overview" }} figures={<p>figures</p>} charts={<p>charts</p>} firstSteps={<p>Deploy your first application</p>} />,
    );
    expect(slots(container)).toEqual(["header", "firstSteps"]);
  });
});

describe("Stepper and Wizard (T5)", () => {
  const steps = [
    { id: "source", label: "Source" },
    { id: "address", label: "Address" },
    { id: "deploy", label: "Deploy" },
  ];

  it("marks each step done, current or not started, in words", () => {
    render(<Stepper steps={steps} current="address" />);
    const list = screen.getByRole("list", { name: "Steps" });
    expect(within(list).getByText("Source: done")).toHaveClass("sr-only");
    expect(within(list).getByText("Address: current step")).toBeInTheDocument();
    expect(within(list).getByText("Deploy: not started")).toBeInTheDocument();
    expect(within(list).getAllByRole("listitem")[1]).toHaveAttribute("aria-current", "step");
  });

  it("lets the operator go back to a done step, and only to a done step", async () => {
    const onSelect = vi.fn();
    render(<Stepper steps={steps} current="address" onSelect={onSelect} />);
    await userEvent.click(screen.getByRole("button", { name: /Source/ }));
    expect(onSelect).toHaveBeenCalledWith("source");
    expect(screen.queryByRole("button", { name: /Deploy/ })).not.toBeInTheDocument();
  });

  it("is horizontal in a dialog", () => {
    render(<Stepper steps={steps} current="source" orientation="horizontal" />);
    expect(screen.getByRole("list", { name: "Steps" })).toHaveAttribute("data-orientation", "horizontal");
  });

  it("keeps Continue enabled and points at what is missing instead of moving on", async () => {
    const onNext = vi.fn();
    const onMissing = vi.fn();
    await renderRouted(
      <Wizard steps={steps} current="address" title="Address" actions={{ next: { onClick: onNext }, missing: "Enter the domain the app answers on.", onMissing }}>
        <p>fields</p>
      </Wizard>,
    );
    const next = screen.getByRole("button", { name: "Continue" });
    expect(next).toBeEnabled();
    await userEvent.click(next);
    expect(onNext).not.toHaveBeenCalled();
    expect(onMissing).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toHaveTextContent("Enter the domain the app answers on.");
  });

  it("moves on when nothing is missing, and back", async () => {
    const onNext = vi.fn();
    const onBack = vi.fn();
    await renderRouted(
      <Wizard steps={steps} current="address" title="Address" actions={{ next: { onClick: onNext }, back: { onClick: onBack } }}>
        <p>fields</p>
      </Wizard>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(onNext).toHaveBeenCalledOnce();
    expect(onBack).toHaveBeenCalledOnce();
    expect(screen.getByRole("heading", { level: 2, name: "Address" })).toBeInTheDocument();
  });

  it("names what has been chosen so far, a landmark beside the step", async () => {
    await renderRouted(
      <Wizard steps={steps} current="address" title="Address" summary={<p>shop.example.com</p>} actions={{ next: { onClick: () => undefined } }}>
        <p>fields</p>
      </Wizard>,
    );
    expect(screen.getByRole("complementary", { name: "Chosen so far" })).toHaveTextContent("shop.example.com");
  });

  it("has no accessibility violations", async () => {
    const { container } = await renderRouted(
      <Wizard steps={steps} current="address" title="Address" description="Where it answers." actions={{ next: { onClick: () => undefined } }}>
        <p>fields</p>
      </Wizard>,
    );
    await expectNoAxeViolations(container);
  });
});

describe("FileEditorPage (T6)", () => {
  it("gives the editor the screen's height and a bar to test, and test and save", async () => {
    const onTest = vi.fn();
    const onTestAndSave = vi.fn();
    const { container } = await renderRouted(
      <FileEditorPage
        header={{ title: "example.net", mono: true }}
        notice={<Notice title="Tested with nginx -t before it is saved" />}
        bar={{ changes: 1, onDiscard: () => undefined, onTest, onTestAndSave }}
      >
        <textarea aria-label="Site configuration" />
      </FileEditorPage>,
    );
    expect(container.querySelector("[data-slot='editor']")).toHaveClass("h-editor");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    await userEvent.click(screen.getByRole("button", { name: "Test and save" }));
    expect(onTest).toHaveBeenCalledOnce();
    expect(onTestAndSave).toHaveBeenCalledOnce();
  });

  it("ends with its footer, the terminal's way, under the bar", async () => {
    const { container } = await renderRouted(
      <FileEditorPage
        header={{ title: "example.net", mono: true }}
        bar={{ changes: 0, onDiscard: () => undefined, onTest: () => undefined, onTestAndSave: () => undefined }}
        footer={<p>noust site show example.net</p>}
      >
        <textarea aria-label="Site configuration" />
      </FileEditorPage>,
    );
    expect(slots(container).at(-1)).toBe("footer");
    expect(screen.getByText("noust site show example.net")).toBeInTheDocument();
  });
});

describe("AuthLayout (T7)", () => {
  it("is one column of 400px: the machine, the title, the form and the terminal's way", async () => {
    render(
      <AuthLayout title="Sign in" description="Use an access token." host="web-1.example.com" footer={<p>noust web token</p>}>
        <form aria-label="Sign in form" />
      </AuthLayout>,
    );
    expect(screen.getByRole("main").firstElementChild).toHaveClass("max-w-auth");
    expect(screen.getByRole("heading", { level: 1, name: "Sign in" })).toHaveAttribute("data-page-title");
    expect(screen.getByText("web-1.example.com")).toHaveAttribute("translate", "no");
    const { container } = render(<div />);
    await expectNoAxeViolations(container);
  });
});
