import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { InlineMessage } from "./InlineMessage";

describe("InlineMessage", () => {
  it("is a line with no frame and no ground", () => {
    const { container } = render(<InlineMessage>Two copies run at once for a few seconds.</InlineMessage>);
    const line = container.firstElementChild;
    expect(line).toHaveClass("text-12");
    expect(line?.className).not.toMatch(/\bborder\b|\bbg-|\bpx-|\bpy-|\brounded/);
  });

  it("puts the colour in the glyph and keeps the sentence in the text colour", () => {
    const { container } = render(<InlineMessage tone="error">Could not reach the host.</InlineMessage>);
    expect(container.querySelector("svg")).toHaveClass("text-fail", "size-icon-xs");
    expect(container.firstElementChild).toHaveClass("text-fg");
  });

  it.each([
    ["info", "text-fg-muted", "Information:"],
    ["success", "text-ok", "Success:"],
    ["warning", "text-warn", "Warning:"],
    ["error", "text-fail", "Error:"],
  ] as const)("says %s three ways: a colour, a shape and a word", (tone, colour, word) => {
    const { container } = render(<InlineMessage tone={tone}>Something.</InlineMessage>);
    expect(container.querySelector("svg")).toHaveClass(colour);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText(word)).toHaveClass("sr-only");
  });

  it("is a warning unless it says otherwise: it is the line of a risk", () => {
    const { container } = render(<InlineMessage>Two copies run at once.</InlineMessage>);
    expect(container.firstElementChild).toHaveAttribute("data-tone", "warning");
  });

  it("keeps a link on the line of the sentence", () => {
    render(<InlineMessage trailing={<a href="/x">What changes</a>}>Two copies run at once.</InlineMessage>);
    const link = screen.getByRole("link", { name: "What changes" });
    expect(link.closest("p")).toHaveTextContent("Two copies run at once.");
  });

  it("is announced only when it is the result of what the operator just did", () => {
    const { rerender } = render(<InlineMessage tone="error">Could not save.</InlineMessage>);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    rerender(
      <InlineMessage tone="error" live>
        Could not save.
      </InlineMessage>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save.");
    rerender(
      <InlineMessage tone="success" live>
        Saved.
      </InlineMessage>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Saved.");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <div>
        <InlineMessage>A risk.</InlineMessage>
        <InlineMessage tone="error" live>
          A failure.
        </InlineMessage>
        <InlineMessage tone="info" trailing={<a href="/x">More</a>}>
          A note.
        </InlineMessage>
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});
