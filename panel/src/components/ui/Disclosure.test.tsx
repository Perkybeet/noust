import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Disclosure } from "./Disclosure";

describe("Disclosure", () => {
  it("folds its options under one button that says whether it is open and what it controls", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <Disclosure label="More options">
        <p>Keep it for 30 days</p>
      </Disclosure>,
    );
    const toggle = screen.getByRole("button", { name: "More options" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Keep it for 30 days")).not.toBeVisible();
    const region = document.getElementById(toggle.getAttribute("aria-controls") ?? "");
    expect(region).toContainElement(screen.getByText("Keep it for 30 days"));
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Keep it for 30 days")).toBeVisible();
    await expectNoAxeViolations(container);
  });

  it("starts open when its options already differ from the defaults", () => {
    render(
      <Disclosure label="More options" defaultOpen>
        <p>Encrypted</p>
      </Disclosure>,
    );
    expect(screen.getByRole("button", { name: "More options" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Encrypted")).toBeVisible();
  });
});
