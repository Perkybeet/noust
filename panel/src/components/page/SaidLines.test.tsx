import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Mono } from "../ui/Mono";
import { SaidLines } from "./SaidLines";

describe("SaidLines", () => {
  it("is a named list with one line for each thing, the system's words in mono and what it means under them", () => {
    render(
      <SaidLines
        label="What the provider said"
        lines={[
          { id: "worker-01", said: <>worker-01: the provider said <Mono>server limit reached for location fsn1</Mono></>, note: "It is billed until you delete it." },
          { id: "worker-02", said: <>worker-02: the provider said <Mono>quota exceeded</Mono></> },
        ]}
      />,
    );
    const list = screen.getByRole("list", { name: "What the provider said" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    const [first, second] = items;
    if (first === undefined || second === undefined) throw new Error("two lines expected");
    expect(within(first).getByText("server limit reached for location fsn1")).toHaveAttribute("translate", "no");
    expect(first).toHaveTextContent("It is billed until you delete it.");
    expect(second).not.toHaveTextContent("billed");
  });

  it("draws nothing when there is nothing to say", () => {
    const { container } = render(<SaidLines label="What it said" lines={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<SaidLines label="What it said" lines={[{ id: "a", said: <>a: <Mono>x</Mono></>, note: "n" }]} />);
    await expectNoAxeViolations(container);
  });
});
