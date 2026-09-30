import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { JobProgress } from "./JobProgress";

describe("JobProgress", () => {
  it("shows a running job: an amber spinner, the verb and the current step verbatim", () => {
    const { container } = render(<JobProgress state="running" title="Deploying shop.example.com" step="npm run build" />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Deploying shop.example.com");
    expect(screen.getByText("npm run build").tagName).toBe("CODE");
    expect(container.querySelector("svg.animate-spin")).toHaveClass("text-warn");
  });

  it("shows a queued job as waiting, with a still glyph", () => {
    const { container } = render(<JobProgress state="queued" title="Update of shop.example.com" />);
    expect(screen.getByText("Waiting to start.")).toBeInTheDocument();
    expect(container.querySelector("svg.animate-spin")).toBeNull();
    expect(container.querySelector('svg[data-glyph="dashed"]')).not.toBeNull();
  });

  it("confirms a success quietly and lets it be dismissed", async () => {
    const onDismiss = vi.fn();
    render(<JobProgress state="succeeded" title="The certificate covers every domain" onDismiss={onDismiss} />);
    expect(screen.getByRole("status")).toHaveTextContent("The certificate covers every domain");
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("shows a failure with the fix above and the system's words verbatim, announced", () => {
    render(
      <JobProgress
        state="failed"
        title="The deploy failed"
        hint="Fix the build and deploy again."
        error={{ detail: "npm ERR! code ELIFECYCLE" }}
        onDismiss={() => undefined}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The deploy failed");
    expect(alert).toHaveTextContent("Fix the build and deploy again.");
    expect(alert).toHaveTextContent("npm ERR! code ELIFECYCLE");
  });

  it("keeps its follow-up when it fails: the output that explains the failure is a click away", () => {
    render(<JobProgress state="failed" title="The update failed" error={{ detail: "E: dpkg was interrupted" }} action={<button type="button">View output</button>} />);
    expect(screen.getByRole("alert")).toContainElement(screen.getByRole("button", { name: "View output" }));
  });

  it("stays quiet when something else already announced the outcome", () => {
    render(<JobProgress state="failed" title="The deploy failed" error={{ detail: "boom" }} announce={false} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says why when a job ends without a reason", () => {
    render(<JobProgress state="failed" title="The deploy failed" />);
    expect(screen.getByText("The job ended without saying why.")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <div>
        <JobProgress state="running" title="Deploying shop.example.com" step="npm ci" />
        <JobProgress state="queued" title="Backup of shop.example.com" />
        <JobProgress state="succeeded" title="Renewed" onDismiss={() => undefined} />
        <JobProgress state="failed" title="Failed" error={{ detail: "boom" }} onDismiss={() => undefined} />
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});
