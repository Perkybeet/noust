import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { LimitField } from "./LimitField";

function Harness({ initial, onValue }: { initial: string; onValue?: (value: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <LimitField
      label="Keep only the newest dumps"
      help="Older scheduled dumps are deleted. Without it, every one stays."
      valueLabel="How many"
      value={value}
      onChange={(next) => {
        setValue(next);
        onValue?.(next);
      }}
      suggestion="7"
    />
  );
}

describe("LimitField", () => {
  it("says there is no limit with a box that is not ticked, and asks for the number only once it is", async () => {
    const seen: string[] = [];
    render(<Harness initial="" onValue={(value) => seen.push(value)} />);
    const box = screen.getByRole("checkbox", { name: "Keep only the newest dumps" });
    expect(box).not.toBeChecked();
    expect(screen.queryByLabelText("How many")).not.toBeInTheDocument();

    await userEvent.click(box);
    expect(screen.getByLabelText("How many")).toHaveValue("7");
    expect(seen).toEqual(["7"]);
  });

  it("starts ticked when there is a limit, and sends nothing when it is unticked", async () => {
    const seen: string[] = [];
    render(<Harness initial="14" onValue={(value) => seen.push(value)} />);
    const box = screen.getByRole("checkbox", { name: "Keep only the newest dumps" });
    expect(box).toBeChecked();
    expect(screen.getByLabelText("How many")).toHaveValue("14");

    await userEvent.click(box);
    expect(seen).toEqual([""]);
    expect(screen.queryByLabelText("How many")).not.toBeInTheDocument();
  });

  it("brings back the number that was typed when the box is ticked again", async () => {
    render(<Harness initial="" />);
    const box = screen.getByRole("checkbox", { name: "Keep only the newest dumps" });
    await userEvent.click(box);
    const number = screen.getByLabelText("How many");
    await userEvent.clear(number);
    await userEvent.type(number, "21");

    await userEvent.click(box);
    await userEvent.click(box);
    expect(screen.getByLabelText("How many")).toHaveValue("21");
  });

  it("keeps the box when the number is wiped out, so it can be typed again", async () => {
    render(<Harness initial="5" />);
    const number = screen.getByLabelText("How many");
    await userEvent.clear(number);
    expect(screen.getByRole("checkbox", { name: "Keep only the newest dumps" })).toBeChecked();
    expect(screen.getByLabelText("How many")).toHaveValue("");
  });

  it("is named and described for a screen reader", async () => {
    const { container } = render(<Harness initial="5" />);
    expect(screen.getByRole("checkbox", { name: "Keep only the newest dumps" })).toHaveAccessibleDescription("Older scheduled dumps are deleted. Without it, every one stays.");
    await expectNoAxeViolations(container);
  });
});
