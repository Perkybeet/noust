import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Disclosure } from "./Disclosure";

describe("Disclosure variant=card", () => {
  it("is a bordered surface of the card's radius with a button that says whether it is open", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <Disclosure variant="card" label="Advanced options">
        <p>Keep the old version for 30 seconds.</p>
      </Disclosure>,
    );
    expect(container.firstElementChild).toHaveClass("rounded-card", "border", "border-border", "bg-surface");
    const button = screen.getByRole("button", { name: "Advanced options" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveAttribute("data-variant", "ghost");
    expect(screen.getByText("Keep the old version for 30 seconds.")).not.toBeVisible();
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveAttribute("aria-controls", screen.getByText("Keep the old version for 30 seconds.").parentElement?.id);
    expect(screen.getByText("Keep the old version for 30 seconds.")).toBeVisible();
  });

  it("is a button and not a <details>: it folds the way every other one does", () => {
    const { container } = render(
      <Disclosure variant="card" label="Advanced options">
        <p>Body</p>
      </Disclosure>,
    );
    expect(container.querySelector("details, summary")).toBeNull();
  });

  it("holds its body 16px in from the card's edge and 16 above its end", () => {
    render(
      <Disclosure variant="card" label="Advanced options" defaultOpen>
        <p>Body</p>
      </Disclosure>,
    );
    expect(screen.getByText("Body").parentElement).toHaveClass("px-4", "pb-4");
  });

  it("says in one line what is inside without opening it, as part of the button's name", () => {
    render(
      <Disclosure variant="card" label="Advanced options" summary="3 of 12 changed">
        <p>Body</p>
      </Disclosure>,
    );
    expect(screen.getByRole("button", { name: "Advanced options 3 of 12 changed" })).toBeInTheDocument();
    expect(screen.getByText("3 of 12 changed")).toHaveClass("text-12", "text-fg-muted");
  });

  it("starts open when what it holds already differs from the defaults", () => {
    render(
      <Disclosure variant="card" label="Advanced options" defaultOpen>
        <p>Body</p>
      </Disclosure>,
    );
    expect(screen.getByRole("button", { name: "Advanced options" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Body")).toBeVisible();
  });

  it("leaves the plain one as it was", () => {
    const { container } = render(
      <Disclosure label="More options">
        <p>Body</p>
      </Disclosure>,
    );
    expect(container.firstElementChild).not.toHaveClass("rounded-card");
  });

  it("has no accessibility violations, closed and open", async () => {
    const { container } = render(
      <div>
        <Disclosure variant="card" label="Closed" summary="Using the defaults">
          <p>Body</p>
        </Disclosure>
        <Disclosure variant="card" label="Open" defaultOpen>
          <p>Body</p>
        </Disclosure>
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});
