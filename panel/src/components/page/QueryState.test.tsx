import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, ApprovalPendingError, ElevationCancelledError } from "../../api/errors";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { ErrorBlock, QueryState } from "./QueryState";
import type { QueryLike } from "./QueryState";

function query<T>(overrides: Partial<QueryLike<T>>): QueryLike<T> {
  return { data: undefined, error: null, isPending: false, isError: false, ...overrides };
}

const FAILURE = new ApiError(
  502,
  "internal",
  "certbot: error: unrecognized arguments: --dry",
  "Update certbot with `apt install certbot`.",
);

function renderState(state: QueryLike<string[]>) {
  return render(
    <QueryState
      query={state}
      label="applications"
      skeleton={<div data-testid="skeleton" />}
      isEmpty={(rows) => rows.length === 0}
      empty={<p>No applications yet</p>}
    >
      {(rows) => (
        <ul>
          {rows.map((row) => (
            <li key={row}>{row}</li>
          ))}
        </ul>
      )}
    </QueryState>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("QueryState", () => {
  it("shows the skeleton while loading, busy and named for screen readers", () => {
    const { container } = renderState(query({ isPending: true }));
    expect(screen.getByTestId("skeleton")).toBeInTheDocument();
    expect(screen.getByText("Loading applications")).toHaveClass("sr-only");
    expect(container.firstElementChild).toHaveAttribute("aria-busy", "true");
  });

  it("shows a failure with the fix above and the system's words verbatim below", async () => {
    const refetch = vi.fn();
    renderState(query({ isError: true, error: FAILURE, refetch }));
    expect(screen.getByText("Could not load applications")).toBeInTheDocument();
    const hint = screen.getByText("Update certbot with `apt install certbot`.");
    const detail = screen.getByText("certbot: error: unrecognized arguments: --dry");
    expect(detail.tagName).toBe("PRE");
    // The fix comes first in reading order.
    expect(hint.compareDocumentPosition(detail) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("falls back to the page's own hint when the backend gives none", () => {
    render(
      <QueryState query={query<string>({ isError: true, error: new Error("socket hang up") })} label="metrics" skeleton={null} errorHint="Check that the panel is running.">
        {(data) => data}
      </QueryState>,
    );
    expect(screen.getByText("Check that the panel is running.")).toBeInTheDocument();
    expect(screen.getByText("socket hang up")).toBeInTheDocument();
  });

  it("shows the empty state when there is nothing to show", () => {
    renderState(query({ data: [] }));
    expect(screen.getByText("No applications yet")).toBeInTheDocument();
  });

  it("shows the content once loaded", () => {
    renderState(query({ data: ["shop.example.net", "example.org"] }));
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("keeps the last answer on screen when a refresh fails, and says so", () => {
    renderState(query({ data: ["shop.example.net"], isError: true, error: FAILURE }));
    expect(screen.getByText("shop.example.net")).toBeInTheDocument();
    expect(screen.getByText(/Could not refresh applications/)).toBeInTheDocument();
  });

  it("speaks Spanish once the language switches", async () => {
    await act(async () => {
      await setLocale("es");
    });
    renderState(query({ isPending: true }));
    expect(screen.getByText("Cargando applications")).toHaveClass("sr-only");
  });

  it("has no accessibility violations in any state", async () => {
    const { container, rerender } = renderState(query({ isPending: true }));
    await expectNoAxeViolations(container);
    rerender(
      <QueryState query={query<string[]>({ isError: true, error: FAILURE, refetch: vi.fn() })} label="applications" skeleton={null}>
        {() => null}
      </QueryState>,
    );
    await expectNoAxeViolations(container);
  });
});

describe("ErrorBlock", () => {
  it("is not a failure when the operator cancelled \"Confirm it's you\": it shows nothing", () => {
    const { container } = render(<ErrorBlock error={new ElevationCancelledError()} title="Could not save" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says a request waits for approval, neutrally, with the way to Approvals", () => {
    render(<ErrorBlock error={new ApprovalPendingError("approval_pending", "4")} title="Could not save" />);
    expect(screen.queryByText("Could not save")).not.toBeInTheDocument();
    expect(screen.getByText("Waiting for approval")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Approvals" })).toBeInTheDocument();
  });

  it("offers one follow-up beside Try again, after the system's words", () => {
    render(
      <ErrorBlock error={FAILURE} title="Could not renew" onRetry={() => undefined} action={<button type="button">View output</button>} />,
    );
    const follow = screen.getByRole("button", { name: "View output" });
    const retry = screen.getByRole("button", { name: "Try again" });
    expect(retry.compareDocumentPosition(follow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("certbot: error: unrecognized arguments: --dry").compareDocumentPosition(follow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("offers its follow-up without a retry too", () => {
    render(<ErrorBlock error={FAILURE} title="Could not renew" action={<button type="button">View output</button>} />);
    expect(screen.getByRole("button", { name: "View output" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("makes long system output scrollable from the keyboard, named after the failure", async () => {
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(192);
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(900);
    render(<ErrorBlock error={FAILURE} title="Renewal of shop.example.com failed" />);
    const output = await screen.findByRole("region", { name: "Renewal of shop.example.com failed: what the system said" });
    expect(output.tagName).toBe("PRE");
    expect(output).toHaveAttribute("tabindex", "0");
  });

  it("interrupts only when it reports something the operator just did", () => {
    const { rerender } = render(<ErrorBlock error={FAILURE} title="Restart failed" />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    rerender(<ErrorBlock error={FAILURE} title="Restart failed" live />);
    expect(screen.getByRole("alert")).toHaveTextContent("Restart failed");
  });

  it("shows a failing tool's own output verbatim, apart from the one-line detail", async () => {
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(192);
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(900);
    const withOutput = new ApiError(
      400,
      "query_failed",
      'ERROR: syntax error at or near "SELCT"',
      null,
      null,
      null,
      'psql:query.sql:1: ERROR:  syntax error at or near "SELCT"\nLINE 1: SELCT * FROM apps;\n        ^',
    );
    render(<ErrorBlock error={withOutput} title="The statement failed" />);
    expect(screen.getByText('ERROR: syntax error at or near "SELCT"')).toBeInTheDocument();
    const output = await screen.findByRole("region", { name: "The statement failed: the command's own output" });
    expect(output.tagName).toBe("PRE");
    expect(output).toHaveTextContent(/LINE 1: SELCT \* FROM apps;/);
  });

  it("does not repeat the output block when it says exactly the same thing as detail", () => {
    const same = new ApiError(400, "rejected", "Permission denied", null, null, null, "Permission denied");
    render(<ErrorBlock error={same} title="Could not fetch the source" />);
    expect(screen.getAllByText("Permission denied")).toHaveLength(1);
  });

  it("shows nothing extra when the error carries no output", () => {
    render(<ErrorBlock error={FAILURE} title="Restart failed" />);
    expect(screen.queryByText(": the command's own output", { exact: false })).not.toBeInTheDocument();
  });

  it("labels its retry button and system-output regions in Spanish", async () => {
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(192);
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(900);
    await act(async () => {
      await setLocale("es");
    });
    render(<ErrorBlock error={FAILURE} title="Restart failed" onRetry={() => undefined} />);
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Restart failed: lo que dijo el sistema" })).toBeInTheDocument();
  });
});
