import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { SignInArt } from "./SignInArt";

describe("SignInArt: what fills the picture's slot on the screens before the console", () => {
  it("is the noust from the side, as decoration, as wide as the slot it is in", () => {
    const { container } = render(<SignInArt />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("viewBox", "0 0 400 230");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    // It takes its width from the slot and keeps its proportions: a picture put in its place does the same.
    expect(svg).toHaveClass("h-auto", "w-full");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<SignInArt />);
    await expectNoAxeViolations(container);
  });
});
