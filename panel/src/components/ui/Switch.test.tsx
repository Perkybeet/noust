import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Switch } from "./Switch";

describe("Switch", () => {
  it("turns on and off from its label", async () => {
    const onCheckedChange = vi.fn();
    render(<Switch label="Deploy on push" onCheckedChange={onCheckedChange} />);
    const control = screen.getByRole("switch", { name: "Deploy on push" });
    expect(control).toHaveAttribute("aria-checked", "false");
    await userEvent.click(screen.getByText("Deploy on push"));
    expect(control).toHaveAttribute("aria-checked", "true");
    expect(onCheckedChange).toHaveBeenLastCalledWith(true);
  });

  it("follows a controlled value", () => {
    const { rerender } = render(<Switch aria-label="Maintenance page" checked={false} />);
    expect(screen.getByRole("switch", { name: "Maintenance page" })).toHaveAttribute("aria-checked", "false");
    rerender(<Switch aria-label="Maintenance page" checked />);
    expect(screen.getByRole("switch", { name: "Maintenance page" })).toHaveAttribute("aria-checked", "true");
  });

  it("is named by its label and described by its second line", async () => {
    render(<Switch label="Deploy on push" description="Build when main receives a commit." />);
    const control = screen.getByRole("switch", { name: "Deploy on push" });
    expect(control).toHaveAccessibleDescription("Build when main receives a commit.");
    await userEvent.click(screen.getByText("Build when main receives a commit."));
    expect(control).toHaveAttribute("aria-checked", "true");
  });

  it("is named and described by the row that holds it", () => {
    render(
      <div>
        <span id="name">Send notifications</span>
        <span id="help">Nothing is sent until a channel is set up.</span>
        <Switch aria-labelledby="name" aria-describedby="help" />
      </div>,
    );
    const control = screen.getByRole("switch", { name: "Send notifications" });
    expect(control).toHaveAccessibleDescription("Nothing is sent until a channel is set up.");
  });

  describe("busy", () => {
    it("keeps its value, draws an arc on the thumb and says it is busy", () => {
      render(<Switch aria-label="Maintenance page" checked busy />);
      const control = screen.getByRole("switch", { name: "Maintenance page" });
      expect(control).toHaveAttribute("aria-busy", "true");
      expect(control).toHaveAttribute("aria-checked", "true");
      expect(control.querySelector('[data-slot="switch-arc"]')).toHaveClass("animate-spin");
    });

    it("does not answer a press until the change settles", async () => {
      const onCheckedChange = vi.fn();
      const { rerender } = render(<Switch aria-label="Maintenance page" checked={false} busy onCheckedChange={onCheckedChange} />);
      await userEvent.click(screen.getByRole("switch"));
      expect(onCheckedChange).not.toHaveBeenCalled();
      rerender(<Switch aria-label="Maintenance page" checked={false} onCheckedChange={onCheckedChange} />);
      await userEvent.click(screen.getByRole("switch"));
      expect(onCheckedChange).toHaveBeenCalledExactlyOnceWith(true);
    });

    it("draws no arc when it is not busy", () => {
      render(<Switch aria-label="Maintenance page" checked />);
      const control = screen.getByRole("switch");
      expect(control).not.toHaveAttribute("aria-busy");
      expect(control.querySelector('[data-slot="switch-arc"]')).toBeNull();
    });
  });

  it("is sized from the scale and the shadow token, with nothing arbitrary", () => {
    render(<Switch aria-label="Maintenance page" />);
    const control = screen.getByRole("switch");
    expect(control).toHaveClass("h-5", "w-8", "p-0.5");
    expect(control.className).not.toMatch(/\[|rgb\(/);
    expect(control.firstElementChild?.className).not.toMatch(/\[|rgb\(/);
    expect(control.firstElementChild).toHaveClass("size-3.5", "shadow-raised");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <div>
        <Switch label="Deploy on push" description="Build when main receives a commit." defaultChecked />
        <Switch aria-label="Maintenance page" />
        <Switch label="Two-factor authentication" disabled defaultChecked />
        <Switch aria-label="Working" checked busy />
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});
