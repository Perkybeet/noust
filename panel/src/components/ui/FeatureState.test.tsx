import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "./Button";
import { FeatureState } from "./FeatureState";

function preferReducedMotion(): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

function frame(container: HTMLElement): HTMLElement {
  const element = container.querySelector<HTMLElement>("[data-feature-state]");
  if (!element) throw new Error("no FeatureState rendered");
  return element;
}

describe("FeatureState", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("says each state three ways: colour, a glyph of its own and a word", () => {
    const { container, rerender } = render(<FeatureState state="on" />);
    const glyphs = new Set<string | null>();

    expect(screen.getByText("Enabled")).toHaveClass("text-ok");
    expect(frame(container)).toHaveAttribute("data-state", "on");
    glyphs.add(frame(container).querySelector("[data-glyph]")?.getAttribute("data-glyph") ?? null);

    rerender(<FeatureState state="off" />);
    expect(screen.getByText("Disabled")).toHaveClass("text-idle");
    glyphs.add(frame(container).querySelector("[data-glyph]")?.getAttribute("data-glyph") ?? null);

    rerender(<FeatureState state="problem" />);
    expect(screen.getByText("Enabled, with a problem")).toHaveClass("text-warn");
    glyphs.add(frame(container).querySelector("[data-glyph]")?.getAttribute("data-glyph") ?? null);

    expect(glyphs).toEqual(new Set(["on", "off", "problem"]));
  });

  it("is green only when on, amber when on with a problem, grey when off", () => {
    const { container, rerender } = render(<FeatureState state="on" />);
    expect(frame(container)).toHaveClass("bg-ok-soft", "border-ok-border");
    rerender(<FeatureState state="problem" />);
    expect(frame(container)).toHaveClass("bg-warn-soft", "border-warn-border");
    rerender(<FeatureState state="off" />);
    expect(frame(container)).toHaveClass("bg-idle-soft", "border-idle-border");
  });

  it("takes its own title, what it implies, and the action beside the title", () => {
    render(
      <FeatureState state="off" title="Instant rollback is off" action={<Button size="sm">Turn on</Button>}>
        Each deploy replaces the last one; going back means building again.
      </FeatureState>,
    );
    const title = screen.getByText("Instant rollback is off");
    const action = screen.getByRole("button", { name: "Turn on" });
    expect(title.parentElement).toContainElement(action);
    expect(screen.getByText(/going back means building again/)).toBeInTheDocument();
  });

  it("pulses once when its state changes, never on the first render", () => {
    const { container, rerender } = render(<FeatureState state="off" />);
    expect(frame(container)).not.toHaveClass("animate-pulse-once");
    rerender(<FeatureState state="on" />);
    expect(frame(container)).toHaveClass("animate-pulse-once");
  });

  it("does not pulse when the operator asks for reduced motion", () => {
    preferReducedMotion();
    const { container, rerender } = render(<FeatureState state="off" />);
    rerender(<FeatureState state="on" />);
    expect(frame(container)).not.toHaveClass("animate-pulse-once");
  });

  it("has no accessibility violations in any state", async () => {
    const { container } = render(
      <div>
        <FeatureState state="on">Alerts go to two channels.</FeatureState>
        <FeatureState state="off" action={<Button size="sm">Enable</Button>}>
          Nothing is sent.
        </FeatureState>
        <FeatureState state="problem" title="Enabled, but no channel">
          Add a channel or nothing is sent.
        </FeatureState>
      </div>,
    );
    await expectNoAxeViolations(container);
  });

  it("speaks Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(<FeatureState state="off" />);
    expect(screen.getByText("Desactivado")).toBeInTheDocument();
  });
});
