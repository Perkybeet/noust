import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { LoadingRegion, SLOW_AFTER_MS } from "./LoadingRegion";

describe("LoadingRegion", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("is busy and names what it reads at once, and says it visibly once it is slow", () => {
    vi.useFakeTimers();
    const { container } = render(
      <LoadingRegion label="Leyendo SSH" className="grid gap-4">
        <span data-testid="skeleton" />
      </LoadingRegion>,
    );

    const region = container.querySelector('[aria-busy="true"]');
    expect(region).not.toBeNull();
    expect(screen.getByText("Leyendo SSH")).toHaveClass("sr-only");
    expect(screen.getByTestId("skeleton")).toBeInTheDocument();
    expect(container.querySelector('[data-slot="loading-caption"]')).toBeNull();

    act(() => {
      vi.advanceTimersByTime(SLOW_AFTER_MS);
    });

    const caption = container.querySelector('[data-slot="loading-caption"]');
    expect(caption).toHaveTextContent("Leyendo SSH");
    // Screen readers already heard it: the visible copy is not read twice.
    expect(caption).toHaveAttribute("aria-hidden", "true");
  });

  it("has no accessibility violations once slow", async () => {
    vi.useFakeTimers();
    const { container } = render(
      <LoadingRegion label="Leyendo el cortafuegos">
        <span />
      </LoadingRegion>,
    );
    act(() => {
      vi.advanceTimersByTime(SLOW_AFTER_MS);
    });
    vi.useRealTimers();
    await expectNoAxeViolations(container);
  });
});
