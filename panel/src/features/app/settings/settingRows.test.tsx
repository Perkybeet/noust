import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RowLoading } from "./settingRows";

describe("RowLoading", () => {
  it("stands for one row with a line of help by default, and says what it is reading", () => {
    const { container } = render(<RowLoading label="Loading the delivery settings" />);
    expect(screen.getByText("Loading the delivery settings")).toBeInTheDocument();
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
    // A label bar and one bar of help.
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(2);
  });

  it("draws a row for each entry, each with the lines of help it will carry, so the group does not grow when the answer comes", () => {
    const { container } = render(<RowLoading label="Loading" rows={[1, 2, 3]} />);
    const rows = container.querySelectorAll("div.px-5.py-3");
    expect(rows).toHaveLength(3);
    expect([...rows].map((row) => row.querySelectorAll('[data-slot="skeleton"]').length)).toEqual([2, 3, 4]);
  });
});
