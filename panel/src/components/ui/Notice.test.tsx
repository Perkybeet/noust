import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "./Button";
import { Notice } from "./Notice";

describe("Notice", () => {
  it("says its severity in words as well as with a shape and a colour", () => {
    render(<Notice tone="warning" title="The certificate expires in 5 days" />);
    expect(screen.getByText("Warning:")).toHaveClass("sr-only");
    expect(screen.getByText("The certificate expires in 5 days")).toBeInTheDocument();
  });

  it.each([
    ["info", "border-border"],
    ["success", "border-ok-border"],
    ["warning", "border-warn-border"],
    ["error", "border-fail-border"],
  ] as const)("draws %s with its state border token, never an alpha", (tone, border) => {
    const { container } = render(<Notice tone={tone} title="Something" />);
    const frame = container.firstElementChild;
    expect(frame).toHaveClass(border);
    expect(frame?.className).not.toMatch(/\/\d+/);
    expect(frame).toHaveAttribute("data-tone", tone);
  });

  it("keeps information achromatic: info is not a state", () => {
    const { container } = render(<Notice title="Noust was called WASM" />);
    expect(container.firstElementChild).toHaveClass("bg-surface-raised");
    expect(container.querySelector("svg")).toHaveClass("text-fg-muted");
  });

  it("is a live region only when it reports something the operator just did", () => {
    const { rerender } = render(<Notice tone="error" title="Could not save" />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    rerender(<Notice tone="error" title="Could not save" live />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save");
    rerender(<Notice tone="success" title="Saved" live />);
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
  });

  it("offers one action and a dismiss button", async () => {
    const onDismiss = vi.fn();
    render(
      <Notice tone="warning" title="Unsaved" action={<Button size="sm">Review</Button>} onDismiss={onDismiss}>
        Two settings changed.
      </Notice>,
    );
    expect(screen.getByRole("button", { name: "Review" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("is roomier as a banner than inline", () => {
    const { container, rerender } = render(<Notice title="Inline" />);
    expect(container.firstElementChild).toHaveClass("rounded-control");
    rerender(<Notice variant="banner" title="Banner" />);
    expect(container.firstElementChild).toHaveClass("rounded-card");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <div>
        <Notice title="Information" />
        <Notice tone="success" title="Saved" live />
        <Notice tone="warning" variant="banner" title="Expiring" action={<Button size="sm">Renew</Button>} onDismiss={() => undefined}>
          The certificate of shop.example.com expires in 5 days.
        </Notice>
        <Notice tone="error">The unit failed to start.</Notice>
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});
