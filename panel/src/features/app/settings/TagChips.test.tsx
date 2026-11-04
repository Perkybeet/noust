import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { TagChips } from "./ResourceTagsDialog";

/** The longest a tag can be: 32 letters, as wide as a phone's card cannot hold beside its menu. */
const LONGEST = "t".repeat(32);

describe("the tags under a name", () => {
  it("draws nothing for a resource with none", () => {
    const { container, rerender } = render(<TagChips tags={undefined} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<TagChips tags={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is a list of chips, each tag once", () => {
    render(<TagChips tags={["production", "nightly"]} />);
    const chips = within(screen.getByRole("list", { name: "Tags" })).getAllByRole("listitem");
    expect(chips.map((chip) => chip.textContent)).toEqual(["production", "nightly"]);
  });

  it("ends a tag wider than its line in an ellipsis, and keeps the whole tag in its title (item 122)", async () => {
    // Measured at 390px: a 32-letter chip ran 7px past the box that holds the card's name and was cut there;
    // at 320px, 77px. The chip is never wider than the line it is on.
    const { container } = render(<TagChips tags={[LONGEST, "short"]} />);
    const [long, short] = within(screen.getByRole("list", { name: "Tags" })).getAllByRole("listitem");
    if (!long || !short) throw new Error("no chips");
    expect(long).toHaveClass("max-w-full", "min-w-0");
    const word = within(long).getByText(LONGEST);
    expect(word).toHaveClass("truncate");
    expect(word).toHaveAttribute("title", LONGEST);
    expect(word.parentElement).toHaveClass("max-w-full");
    expect(within(short).getByText("short")).toHaveAttribute("title", "short");
    await expectNoAxeViolations(container);
  });
});
