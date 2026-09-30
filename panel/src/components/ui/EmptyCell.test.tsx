import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyCell } from "./EmptyCell";

describe("EmptyCell", () => {
  it("shows a dash on screen and tells a screen reader why the cell is empty", () => {
    render(<EmptyCell reason="No certificate" />);
    expect(screen.getByText("–")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("No certificate")).toHaveClass("sr-only");
  });

  it("says none when the caller has no better reason", () => {
    render(<EmptyCell />);
    expect(screen.getByText("None")).toHaveClass("sr-only");
  });
});
