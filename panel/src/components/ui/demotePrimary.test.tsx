import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { Drawer } from "./Drawer";
import { DemotePrimary, ResetPrimary } from "./demotePrimary";

describe("DemotePrimary", () => {
  it("draws a primary button as a secondary one, in its colour and in what the contract reads", () => {
    render(
      <DemotePrimary active>
        <Button variant="primary">Update</Button>
      </DemotePrimary>,
    );
    const button = screen.getByRole("button", { name: "Update" });
    expect(button).toHaveAttribute("data-variant", "secondary");
    expect(button).toHaveClass("bg-surface", "border-border");
    expect(button).not.toHaveClass("bg-accent");
  });

  it("keeps its label and its size: nothing moves when the primary steps aside", () => {
    const { rerender } = render(
      <Button variant="primary" size="md">
        Update
      </Button>,
    );
    const before = screen.getByRole("button", { name: "Update" }).className;
    rerender(
      <DemotePrimary active>
        <Button variant="primary" size="md">
          Update
        </Button>
      </DemotePrimary>,
    );
    const after = screen.getByRole("button", { name: "Update" }).className;
    const sizes = (names: string): string[] => names.split(" ").filter((name) => /^(h-|px-|gap-|text-1|\[&_svg\])/.test(name));
    expect(sizes(after)).toEqual(sizes(before));
  });

  it("leaves the other variants alone, and everything while it is not active", () => {
    render(
      <DemotePrimary active={false}>
        <Button variant="primary">Update</Button>
        <Button variant="danger">Delete</Button>
        <Button variant="ghost">Discard</Button>
      </DemotePrimary>,
    );
    expect(screen.getByRole("button", { name: "Update" })).toHaveAttribute("data-variant", "primary");
    expect(screen.getByRole("button", { name: "Delete" })).toHaveAttribute("data-variant", "danger");
    expect(screen.getByRole("button", { name: "Discard" })).toHaveAttribute("data-variant", "ghost");
  });

  it("does not reach into a dialog opened from it: the dialog has its own primary", async () => {
    render(
      <DemotePrimary active>
        <Dialog open title="Update" footer={<Button variant="primary">Update now</Button>}>
          <p>Body</p>
        </Dialog>
      </DemotePrimary>,
    );
    expect(await screen.findByRole("button", { name: "Update" })).toHaveAttribute("data-variant", "primary");
  });

  it("does not reach into a drawer either", async () => {
    render(
      <DemotePrimary active>
        <Drawer open title="Details" footer={<Button variant="primary">Apply</Button>}>
          <p>Body</p>
        </Drawer>
      </DemotePrimary>,
    );
    expect(await screen.findByRole("button", { name: "Apply" })).toHaveAttribute("data-variant", "primary");
  });

  it("can be reset by hand", () => {
    render(
      <DemotePrimary active>
        <ResetPrimary>
          <Button variant="primary">Own</Button>
        </ResetPrimary>
      </DemotePrimary>,
    );
    expect(screen.getByRole("button", { name: "Own" })).toHaveAttribute("data-variant", "primary");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <DemotePrimary active>
        <Button variant="primary">Update</Button>
      </DemotePrimary>,
    );
    await expectNoAxeViolations(container);
  });
});
