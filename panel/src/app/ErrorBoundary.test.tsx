import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportHost } from "../features/report/ReportHost";
import { closeReport } from "../features/report/store";
import { expectNoAxeViolations } from "../test/axe";
import { fakeBackend, json, signedInRoutes } from "../test/fakes";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom(): never {
  throw new TypeError("Cannot read properties of undefined (reading 'rows')");
}

async function brokenPage(path = "/databases/postgresql/shop") {
  fakeBackend({ ...signedInRoutes(), "GET /api/system/support-report": () => json(200, { version: "2.0.0", generated_at: "", hide_domains: false, markdown: "# report\n", sections: [] }) });
  // React logs the error it caught; the test is about what the screen offers, not about that log.
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const root = createRootRoute();
  const page = createRoute({
    getParentRoute: () => root,
    path: "$",
    component: () => (
      <>
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>
        <ReportHost />
      </>
    ),
  });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [path] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { level: 1, name: "This page broke" });
  return { ...view, user: userEvent.setup() };
}

afterEach(() => {
  closeReport();
});

describe("the screen of a page that broke", () => {
  it("says the rest of the console works, shows the error as it is and offers to report it", async () => {
    await brokenPage();

    expect(screen.getByText(/The rest of the console still works/)).toBeInTheDocument();
    expect(screen.getByText("Cannot read properties of undefined (reading 'rows')")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Report a problem" })).toBeInTheDocument();
  });

  it("opens the report with the error already in it and the area of the page that broke", async () => {
    const { user } = await brokenPage("/databases/postgresql/shop");

    await user.click(screen.getByRole("button", { name: "Report a problem" }));
    const dialog = await screen.findByRole("dialog", { name: "Report a problem" });

    const happened = within(dialog).getByRole<HTMLTextAreaElement>("textbox", { name: "What happened" });
    expect(happened.value).toBe("The page broke with this error:\nCannot read properties of undefined (reading 'rows')");
    expect(within(dialog).getByRole("combobox", { name: "Where does it happen" })).toHaveTextContent("Databases");
    expect(within(dialog).getByRole("textbox", { name: "Title" })).toHaveValue("");
  });

  it("has no accessibility violations with the report open", async () => {
    const { user } = await brokenPage();
    await user.click(screen.getByRole("button", { name: "Report a problem" }));
    await screen.findByRole("dialog", { name: "Report a problem" });

    await expectNoAxeViolations(document.body, { page: true });
  });
});
