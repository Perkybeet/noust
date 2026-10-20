import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { LoadingRegion } from "../page/LoadingRegion";
import { SkeletonCard, SkeletonRows, SkeletonSection } from "./Skeleton";

describe("skeleton presets", () => {
  describe("SkeletonRows", () => {
    it("draws as many rows as the page expects, at least one", () => {
      const { container, rerender } = render(<SkeletonRows rows={4} />);
      expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(4);
      rerender(<SkeletonRows rows={0} />);
      expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(1);
    });

    it("has the frame of a RowList, so nothing moves when the rows arrive", () => {
      const { container } = render(<SkeletonRows rows={2} />);
      const frame = container.firstElementChild;
      expect(frame).toHaveClass("rounded-card", "border", "border-border", "divide-y", "divide-border", "bg-surface");
      for (const row of frame?.children ?? []) expect(row).toHaveClass("min-h-11", "px-4", "py-3");
    });

    it("varies the width of its lines, so it reads as names and not as a stripe", () => {
      const { container } = render(<SkeletonRows rows={3} />);
      const widths = [...container.querySelectorAll('[data-slot="skeleton"]')].map((line) => [...line.classList].find((name) => name.startsWith("w-")));
      expect(new Set(widths).size).toBeGreaterThan(1);
    });

    it("takes its heights from the scale and not from an arbitrary value", () => {
      const { container } = render(<SkeletonRows rows={1} />);
      expect(container.innerHTML).not.toMatch(/h-\[|w-\[/);
    });
  });

  describe("SkeletonCard", () => {
    it("has the padding of a card with a title: 16, 20 and 12 above the body, 20 below", () => {
      const { container } = render(<SkeletonCard lines={2} />);
      const card = container.firstElementChild;
      expect(card).toHaveClass("rounded-card", "border", "bg-surface");
      const [header, body] = container.querySelectorAll('[data-slot="skeleton-card"] > div');
      expect(header).toHaveClass("px-5", "pt-4", "pb-3");
      expect(header?.firstElementChild).toHaveClass("h-5");
      expect(body).toHaveClass("px-5", "pb-5");
      expect(body?.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(2);
    });

    it("is not a heading: an empty one would be a violation, and it says nothing", () => {
      render(<SkeletonCard />);
      expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    });
  });

  describe("SkeletonSection", () => {
    it("is a title at 24px, then rows", () => {
      const { container } = render(<SkeletonSection rows={3} />);
      const title = container.querySelector('[data-slot="skeleton-section"] > [data-slot="skeleton"]');
      expect(title).toHaveClass("h-6");
      expect(container.querySelector('[data-slot="skeleton-rows"]')?.children).toHaveLength(3);
    });
  });

  it("is hidden from assistive technology: the region around it says what is loading", async () => {
    const { container } = render(
      <LoadingRegion label="Loading variables">
        <SkeletonSection rows={2} />
        <SkeletonCard />
      </LoadingRegion>,
    );
    expect(screen.getByText("Loading variables")).toHaveClass("sr-only");
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
    for (const shape of container.querySelectorAll('[data-slot="skeleton-section"], [data-slot="skeleton-card"]')) expect(shape).toHaveAttribute("aria-hidden", "true");
    await expectNoAxeViolations(container);
  });
});
