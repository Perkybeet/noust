import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ErrorBlock } from "../../components/page/QueryState";
import { expectNoAxeViolations } from "../../test/axe";
import { CeremonyError } from "./webauthn";

/** What the pages that start a ceremony draw when the browser still had the last window open: the same error block as any failure. */
describe("a passkey window the browser was still closing, in the error block", () => {
  it("says why above the browser's own words, which are kept as they came", async () => {
    const error = new CeremonyError("OperationError", "A request is already pending.", "The browser still has another passkey request open. Reload the page to close it, then try again.");
    const { container } = render(<ErrorBlock error={error} title="Could not add the passkey" />);
    expect(screen.getByText("Could not add the passkey")).toBeInTheDocument();
    const hint = screen.getByText("The browser still has another passkey request open. Reload the page to close it, then try again.");
    const words = screen.getByText("OperationError: A request is already pending.");
    // The fix first, the browser's words after it.
    expect(hint.compareDocumentPosition(words) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await expectNoAxeViolations(container);
  });

  it("adds nothing above the words of a refusal the console has nothing to add to", () => {
    render(<ErrorBlock error={new CeremonyError("SecurityError", "The relying party ID is not a registrable domain suffix.")} title="Could not add the passkey" />);
    expect(screen.getByText("SecurityError: The relying party ID is not a registrable domain suffix.")).toBeInTheDocument();
    expect(screen.queryByText(/another passkey request open/)).not.toBeInTheDocument();
  });
});
