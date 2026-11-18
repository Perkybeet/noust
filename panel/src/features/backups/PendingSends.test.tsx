import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import type { PendingSend } from "../../lib/pendingSends";
import { PendingSends } from "./PendingSends";

const IN_AN_HOUR = new Date(Date.now() + 3_600_000).toISOString();

function owed(over: Partial<PendingSend> = {}): PendingSend {
  return { destination: "offsite", reason: "connection to offsite timed out", attempts: 2, since: new Date(Date.now() - 7_200_000).toISOString(), nextAttemptAt: IN_AN_HOUR, permanent: false, ...over };
}

/** The part of the detail that is about one destination. */
function destinationIn(detail: HTMLElement, destination: string): HTMLElement {
  const found = detail.querySelector(`[data-pending-destination="${destination}"]`);
  if (!(found instanceof HTMLElement)) throw new Error(`no detail for ${destination}`);
  return found;
}

describe("a copy waiting to be sent", () => {
  it("draws nothing for a copy that is everywhere", () => {
    const { container } = render(<PendingSends sends={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is the state of work in progress, said in words with the destination and when it is tried again", async () => {
    const { container } = render(<PendingSends sends={[owed()]} />);
    expect(screen.getByText("Pending to offsite")).toBeInTheDocument();
    // The shape and the colour of "in progress", never a state of its own that nobody has learnt.
    const pill = container.querySelector("[data-state]");
    expect(pill).toHaveAttribute("data-state", "deploying");
    expect(screen.getByText(/Tries again/)).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says how many destinations when it is owed to several, and names them in the detail with what each said, verbatim", async () => {
    const user = userEvent.setup();
    render(<PendingSends sends={[owed(), owed({ destination: "bucket", reason: "403 AccessDenied: the key cannot write", attempts: 0, permanent: true })]} />);
    expect(screen.getByText("Pending to 2 destinations")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Why it has not been sent yet" }));
    const detail = await screen.findByRole("dialog", { name: "Waiting to be sent" });
    const offsite = destinationIn(detail, "offsite");
    const bucket = destinationIn(detail, "bucket");
    expect(within(offsite).getByText("connection to offsite timed out")).toBeInTheDocument();
    expect(within(offsite).getByText("Tried again 2 times since")).toBeInTheDocument();
    expect(within(bucket).getByText("403 AccessDenied: the key cannot write")).toBeInTheDocument();
    expect(within(bucket).getByText("Not tried again yet")).toBeInTheDocument();
    // What waiting does not mend is said where it is, and only there.
    expect(within(bucket).getByText("Waiting will not fix this: it needs a change at the destination.")).toBeInTheDocument();
    expect(within(offsite).queryByText(/Waiting will not fix this/)).not.toBeInTheDocument();
    expect(within(detail).getByText(/Retention does not delete it while it is owed/)).toBeInTheDocument();
  });

  it("says it is tried on the next pass when the time already came, not that it will be tried in the past", () => {
    render(<PendingSends sends={[owed({ nextAttemptAt: new Date(Date.now() - 60_000).toISOString() })]} />);
    expect(screen.getByText("Tries again on the next pass")).toBeInTheDocument();
  });

  it("does not make up a time the server did not give", () => {
    render(<PendingSends sends={[owed({ nextAttemptAt: null })]} />);
    expect(screen.queryByText(/Tries again/)).not.toBeInTheDocument();
  });
});
