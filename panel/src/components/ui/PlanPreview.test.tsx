import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { PlanPreview } from "./PlanPreview";
import type { Plan } from "./PlanPreview";

const sentence = (code: string, text: string, params: Record<string, string> = {}, hint: string | null = null) => ({ code, params, text, hint });

function plan(overrides: Partial<Plan> = {}): Plan {
  return {
    operation: "app.update",
    subject: "shop.example.com",
    effect: sentence("plans.update.release", "Builds shop.example.com from main as a new release.", { domain: "shop.example.com", branch: "main" }),
    commands: [
      { argv: ["npm", "install"], text: "npm install", probe: false, cwd: null, runs_as: "sandbox:noust-build", step: "step-1", writes_to: null, check: false, seconds: null },
      { argv: ["systemctl", "restart", "shop.service"], text: "systemctl restart shop.service", probe: false, cwd: null, runs_as: null, step: "step-2", writes_to: null, check: false, seconds: null },
      { argv: ["GET", "http://127.0.0.1:3100/"], text: "GET http://127.0.0.1:3100/", probe: true, cwd: null, runs_as: null, step: "step-2", writes_to: null, check: true, seconds: 30 },
    ],
    files: [],
    steps: [],
    findings: [],
    unknown: [sentence("plans.unknown.build", "Which install and build commands run: they come from the repository's own files.")],
    needs: { elevation: false, approval: false, confirmations: [] },
    blocked: null,
    asked_remote: false,
    omitted: 0,
    ...overrides,
  };
}

describe("PlanPreview", () => {
  it("is folded by default, says how much is in it and what it does not know, and opens on a press", async () => {
    render(<PlanPreview plan={plan()} />);
    const toggle = screen.getByRole("button", { name: /See what it will do/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("3 commands, 1 thing it does not know");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Noust works this out with a simulation and runs nothing.")).toBeInTheDocument();
  });

  it("lists the orders in the order they would run, marking the ones that only read and the one Noust asks itself", () => {
    render(<PlanPreview plan={plan()} defaultOpen />);
    const list = screen.getByRole("list", { name: "Commands" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("npm install"),
      expect.stringContaining("systemctl restart shop.service"),
      expect.stringContaining("GET http://127.0.0.1:3100/"),
    ]);
    const [first, , third] = items;
    if (first === undefined || third === undefined) throw new Error("the plan lists fewer commands than it has");
    expect(within(first).getByText("As sandbox:noust-build")).toBeInTheDocument();
    expect(within(third).getByText(/Noust asks it itself/)).toBeInTheDocument();
    expect(within(third).getByText(/Up to 30 s/)).toBeInTheDocument();
  });

  it("draws what it does not know inside, never folded under another button, and says so even when it knows everything else", () => {
    render(<PlanPreview plan={plan({ unknown: [sentence("test.unknown.build", "Which commands run."), sentence("test.unknown.health", "Whether it answers.")] })} defaultOpen />);
    const section = screen.getByRole("region", { name: "What it does not know" });
    expect(within(section).getByText(/Which commands run\./)).toBeVisible();
    expect(within(section).getByText(/Whether it answers\./)).toBeVisible();
    expect(within(section).queryByRole("button")).toBeNull();
  });

  it("groups the commands under the phases the operation announces, verbatim", () => {
    render(
      <PlanPreview
        plan={plan({ steps: [{ key: "step-1", title: "Installing dependencies", commands: [0] }, { key: "step-2", title: "Activating release 20261113", commands: [1, 2] }] })}
        defaultOpen
      />,
    );
    expect(screen.getByText("Installing dependencies")).toHaveAttribute("translate", "no");
    expect(screen.getByRole("list", { name: "Activating release 20261113" })).toBeInTheDocument();
  });

  it("shows only the names of a file of variables, and never the content of one that holds secrets", () => {
    render(
      <PlanPreview
        plan={plan({
          files: [
            { path: "/srv/app/.noust-env", change: "create", shown: "names", lines: [{ kind: "add", text: "NOUST_BRANCH" }], truncated: false, reason: null, destination: null },
            { path: "/srv/app/shared/.env", change: "modify", shown: "none", lines: [], truncated: false, reason: "secret", destination: null },
            { path: "/srv/app/current", change: "link", shown: "none", lines: [], truncated: false, reason: null, destination: "releases/20261113" },
          ],
        })}
        defaultOpen
      />,
    );
    expect(screen.getByText("NOUST_BRANCH")).toBeInTheDocument();
    expect(screen.getByText("Only the names are shown, never the values.")).toBeInTheDocument();
    expect(screen.getByText("Its content is not shown: it holds secrets.")).toBeInTheDocument();
    expect(screen.getByText("releases/20261113")).toBeInTheDocument();
  });

  it("says why it would be refused now and still shows what it would have done", () => {
    render(<PlanPreview plan={plan({ blocked: sentence("plans.blocked.nothing_new", "Nothing new to deploy") })} defaultOpen />);
    expect(screen.getByText("It would be refused now")).toBeInTheDocument();
    expect(screen.getByText("Nothing new to deploy")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Commands" })).toBeInTheDocument();
  });

  it("says what it will ask when it is applied: sudo mode, a second person and what must be confirmed", () => {
    render(
      <PlanPreview
        plan={plan({ needs: { elevation: true, approval: true, confirmations: [{ kind: "frozen", field: "frozen_ok", sentence: sentence("plans.confirm.frozen", "Deploys are frozen until noon", {}, "Confirm it only if it is urgent.") }] } })}
        defaultOpen
      />,
    );
    expect(screen.getByText("It asks you to confirm it is you.")).toBeInTheDocument();
    expect(screen.getByText("It waits for a second person to approve it.")).toBeInTheDocument();
    expect(screen.getByText(/Deploys are frozen until noon/)).toBeInTheDocument();
    expect(screen.getByText(/Confirm it only if it is urgent\./)).toBeInTheDocument();
  });

  it("says what did not fit and that it asked over the network", () => {
    render(<PlanPreview plan={plan({ omitted: 3, asked_remote: true })} defaultOpen />);
    expect(screen.getByText("3 more commands and files do not fit and are not shown.")).toBeInTheDocument();
    expect(screen.getByText("To work this out it asked the repository or the registry over the network.")).toBeInTheDocument();
  });

  it("has no accessibility violations, folded or open", async () => {
    const { container } = render(<PlanPreview plan={plan({ steps: [{ key: "step-1", title: "Installing", commands: [0] }] })} defaultOpen />);
    await expectNoAxeViolations(container);
  });
});
