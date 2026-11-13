import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { NotOffered, ProposalCard } from "./ProposalCard";
import type { ProposalView } from "./ProposalCard";
import { ReadSteps } from "./ReadSteps";
import { UsageLine, tightestReading } from "./UsageLine";

function proposal(overrides: Partial<ProposalView> = {}): ProposalView {
  return {
    id: "p1",
    title: "Restart shop.example.com",
    klass: "ask",
    state: "waiting",
    permission: "apps.operate",
    call: "POST /api/apps/shop.example.com/restart",
    facts: [{ label: "Application", value: "shop.example.com" }],
    plan: null,
    why: "Your policy asks you before the assistant restarts something in production.",
    ...overrides,
  };
}

describe("ProposalCard", () => {
  it("says the class in words and what the person approves is the exact call", async () => {
    render(<ProposalCard proposal={proposal()} />);
    const card = screen.getByRole("article");
    expect(within(card).getByText("Restart shop.example.com")).toBeInTheDocument();
    expect(within(card).getByText("Asks for your approval")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Why it is so" }));
    expect(screen.getByText("POST /api/apps/shop.example.com/restart")).toBeInTheDocument();
    expect(screen.getByText("apps.operate")).toBeInTheDocument();
  });

  it("has one primary button, in the first card that waits, and never an approve-all", () => {
    render(
      <>
        <ProposalCard proposal={proposal({ id: "a" })} primary />
        <ProposalCard proposal={proposal({ id: "b" })} />
      </>,
    );
    const approves = screen.getAllByRole("button", { name: "Approve" });
    expect(approves.map((button) => button.getAttribute("data-variant"))).toEqual(["primary", "secondary"]);
    expect(screen.queryByRole("button", { name: /approve all/i })).toBeNull();
  });

  it("asks to confirm it is you where sudo mode is needed, with the key on the button", () => {
    render(<ProposalCard proposal={proposal({ klass: "you" })} primary />);
    expect(screen.getByText("Asks for your approval and for you to confirm it is you")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve…" })).toBeInTheDocument();
  });

  it("calls the handlers and nothing else", async () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(<ProposalCard proposal={proposal()} primary onApprove={onApprove} onReject={onReject} />);
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onReject).toHaveBeenCalledTimes(1);
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("is a record with no buttons once it was done on its own", () => {
    render(<ProposalCard proposal={proposal({ klass: "alone", state: "done", decidedAt: "10:44", result: "Restarted shop.example.com." })} />);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
    expect(screen.getByText("Restarted shop.example.com.")).toBeInTheDocument();
    expect(screen.getByText(/The assistant did it at 10:44 because your policy allows it\./)).toBeInTheDocument();
  });

  it("shows a failure with the system's words verbatim", () => {
    render(<ProposalCard proposal={proposal({ state: "failed", result: "Could not restart.", output: "Job for shop.service failed" })} />);
    expect(screen.getByText("Could not restart.")).toBeInTheDocument();
    expect(screen.getByText("Job for shop.service failed")).toBeInTheDocument();
  });

  it("says a second person decides when the rule of two applies, without a button that would satisfy it alone", () => {
    render(<ProposalCard proposal={proposal({ klass: "two", state: "second", approval: 17 })} />);
    expect(screen.getByText(/It waits for a second person to approve it\. The request is number 17\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("highlights a value that is neither the person's nor Noust's, with where it was found, and forces the question", () => {
    render(
      <ProposalCard
        proposal={proposal({ facts: [{ label: "Domain", value: "cdn.example.org", foreign: { kind: "host", where: "Found in the log of deployment 14, line 41." } }] })}
        primary
      />,
    );
    expect(screen.getByText("A value is neither yours nor Noust's")).toBeInTheDocument();
    expect(screen.getByText("Found in the log of deployment 14, line 41.")).toBeInTheDocument();
    expect(screen.getByText("A value that comes neither from you nor from Noust")).toBeInTheDocument();
    expect(screen.getByText("A host name")).toBeInTheDocument();
  });

  it("has no violations for any of its states", async () => {
    const { container } = render(
      <>
        <ProposalCard proposal={proposal()} primary />
        <ProposalCard proposal={proposal({ id: "q", state: "rejected", decidedBy: "marta", decidedAt: "10:50" })} />
        <ProposalCard proposal={proposal({ id: "r", state: "running" })} />
      </>,
    );
    await expectNoAxeViolations(container);
  });
});

describe("NotOffered", () => {
  it("is a line with a reason and never a button that does nothing", async () => {
    const { container } = render(<NotOffered title="Delete the application shop.example.com" why="Your policy does not let the assistant do this." hint="Whoever administers security changes it." />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Not offered")).toBeInTheDocument();
    expect(screen.getByText("Delete the application shop.example.com")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says that nobody changes what the floor forbids, and draws no link to a policy", () => {
    render(<NotOffered floor title="Open a terminal" why="The assistant never does this." policyLink={<a href="/x">policy</a>} />);
    expect(screen.getByText("Nobody changes this: the assistant never does it.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "policy" })).toBeNull();
  });
});

describe("ReadSteps", () => {
  it("folds under the answer as how many things it read, and opens to the list", async () => {
    render(<ReadSteps steps={[{ id: "1", what: "Read the log of deployment 14", state: "done", size: "120 lines" }, { id: "2", what: "Read the names of the variables", state: "done" }]} />);
    const toggle = screen.getByRole("button", { name: "Read 2 things" });
    await userEvent.click(toggle);
    expect(screen.getByRole("list", { name: "What it read" })).toBeInTheDocument();
    expect(screen.getByText("120 lines")).toBeInTheDocument();
  });

  it("says how many values that looked like secrets were hidden, and that a failed read failed, in words", () => {
    render(<ReadSteps defaultOpen steps={[{ id: "1", what: "Read the log", state: "done", masked: 2 }, { id: "2", what: "Read the journal", state: "failed", error: "The server does not answer." }]} />);
    expect(screen.getByText("2 values that looked like secrets were hidden before they were sent.")).toBeInTheDocument();
    expect(screen.getByText(". Could not read it")).toBeInTheDocument();
    expect(screen.getByText("The server does not answer.")).toBeInTheDocument();
  });

  it("is a polite status while it works", async () => {
    const { container } = render(<ReadSteps running steps={[{ id: "1", what: "Reading the journal", state: "running" }]} />);
    expect(screen.getByText("Investigating now").closest('[role="status"]')).toHaveAttribute("aria-live", "polite");
    await expectNoAxeViolations(container);
  });

  it("draws nothing when it read nothing", () => {
    const { container } = render(<ReadSteps steps={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("UsageLine", () => {
  const tokens = (used: number) => ({ id: "tokens", label: "What it has read", used, cap: 400_000, valueText: `${String(used / 1000)} k of 400 k` });

  it("picks the limit that is nearest, and leaves out the steps until they are near", () => {
    const steps = { id: "steps", label: "Steps of this turn", used: 3, cap: 20, valueText: "3 of 20", onlyWhenNear: true };
    expect(tightestReading([tokens(100_000), steps])?.id).toBe("tokens");
    expect(tightestReading([tokens(100_000), { ...steps, used: 17 }])?.id).toBe("steps");
    expect(tightestReading([steps])).toBeNull();
  });

  it("is silent about a limit with plenty left and warns, in words, when it is near", async () => {
    const { rerender, container } = render(<UsageLine readings={[tokens(100_000)]} />);
    expect(screen.queryByText(/Near the limit/)).toBeNull();
    rerender(<UsageLine readings={[tokens(340_000)]} />);
    expect(screen.getByText("Near the limit: 340 k of 400 k. Whoever administers the assistant changes it.")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("draws nothing when there is nothing to show", () => {
    const { container } = render(<UsageLine readings={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
