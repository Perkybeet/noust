import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { DemotePrimary } from "./demotePrimary";
import { MenuItem, MenuSeparator } from "./Menu";
import { SplitButton } from "./SplitButton";

function update(props: Partial<Parameters<typeof SplitButton>[0]> = {}) {
  const onUpdate = vi.fn();
  const onNoCache = vi.fn();
  render(
    <SplitButton
      name="Update"
      variant="primary"
      onClick={onUpdate}
      menu={
        <>
          <MenuItem onClick={onNoCache}>Update without cache</MenuItem>
          <MenuSeparator />
          <MenuItem>Build only</MenuItem>
        </>
      }
      {...props}
    >
      Update
    </SplitButton>,
  );
  return { onUpdate, onNoCache };
}

describe("SplitButton", () => {
  it("is two buttons with their own names, in one group named for the action", () => {
    update();
    const group = screen.getByRole("group", { name: "Update" });
    expect(group.querySelectorAll("button")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Update" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "More options for Update" })).toHaveAttribute("aria-haspopup", "menu");
  });

  it("does the usual thing from the main part, and opens no menu", async () => {
    const { onUpdate } = update();
    await userEvent.click(screen.getByRole("button", { name: "Update" }));
    expect(onUpdate).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("does a variation from the menu, and not the usual thing", async () => {
    const { onUpdate, onNoCache } = update();
    await userEvent.click(screen.getByRole("button", { name: "More options for Update" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Update without cache" }));
    expect(onNoCache).toHaveBeenCalledOnce();
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("is one control: the same height, joined, and one ring round the pair", () => {
    update({ size: "md" });
    const [main, arrow] = screen.getAllByRole("button");
    expect(main).toHaveClass("h-control-md", "rounded-r-none");
    expect(arrow).toHaveClass("h-control-md", "w-control-md", "rounded-l-none");
    const group = screen.getByRole("group");
    expect(group.className).toContain("has-[:focus-visible]:outline-2");
    // The parts draw no ring of their own: the group's outweighs theirs.
    expect(group.className).toContain("[&_button:focus-visible]:outline-none");
  });

  it.each([
    ["sm", "h-control-sm", "w-control-sm"],
    ["lg", "h-control-lg", "w-control-lg"],
  ] as const)("keeps the two parts the height of its size: %s", (size, height, width) => {
    update({ size });
    const [main, arrow] = screen.getAllByRole("button");
    expect(main).toHaveClass(height);
    expect(arrow).toHaveClass(height, width);
  });

  it("is a primary of both parts, with a seam in the darker violet", () => {
    update({ variant: "primary" });
    const [main, arrow] = screen.getAllByRole("button");
    expect(main).toHaveAttribute("data-variant", "primary");
    expect(arrow).toHaveAttribute("data-variant", "primary");
    expect(arrow).toHaveClass("border-l-accent-hover");
  });

  it("is a secondary of both parts, the borders meeting in one line", () => {
    update({ variant: "secondary" });
    const [main, arrow] = screen.getAllByRole("button");
    expect(main).toHaveAttribute("data-variant", "secondary");
    expect(arrow).toHaveAttribute("data-variant", "secondary");
    expect(arrow).toHaveClass("-ml-px");
    expect(arrow).not.toHaveClass("border-l-accent-hover");
  });

  it("steps aside as one while the save bar holds changes", () => {
    render(
      <DemotePrimary active>
        <SplitButton name="Update" variant="primary" menu={<MenuItem>Build only</MenuItem>}>
          Update
        </SplitButton>
      </DemotePrimary>,
    );
    for (const button of screen.getAllByRole("button")) expect(button).toHaveAttribute("data-variant", "secondary");
    expect(screen.getAllByRole("button")[1]).not.toHaveClass("border-l-accent-hover");
  });

  it("draws the arc in the main part and cannot be pressed while it works", async () => {
    const { onUpdate } = update({ loading: true });
    const main = screen.getByRole("button", { name: "Update" });
    expect(main).toHaveAttribute("aria-busy", "true");
    expect(main.querySelector("svg.animate-spin")).not.toBeNull();
    expect(screen.getByRole("button", { name: "More options for Update" })).toBeDisabled();
    await userEvent.click(main);
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("can be disabled as a whole", () => {
    update({ disabled: true });
    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
  });

  it("opens the menu from the keyboard and closes it with Escape", async () => {
    update();
    const arrow = screen.getByRole("button", { name: "More options for Update" });
    arrow.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("menu")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    });
  });

  it("speaks Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    update({ name: "Actualizar" });
    expect(screen.getByRole("button", { name: "Más opciones de Actualizar" })).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    update();
    await userEvent.click(screen.getByRole("button", { name: "More options for Update" }));
    await screen.findByRole("menu");
    await expectNoAxeViolations(document.body);
  });
});
