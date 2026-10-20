import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { MenuItem, MenuSeparator } from "../ui/Menu";
import { RowMenu } from "./RowMenu";

describe("RowMenu", () => {
  it("is three dots named for the row, so twenty rows have twenty names", () => {
    render(
      <>
        <RowMenu name="shop.example.com">
          <MenuItem>Open</MenuItem>
        </RowMenu>
        <RowMenu name="docs.example.com">
          <MenuItem>Open</MenuItem>
        </RowMenu>
      </>,
    );
    expect(screen.getByRole("button", { name: "Actions for shop.example.com" })).toHaveAttribute("aria-haspopup", "menu");
    expect(screen.getByRole("button", { name: "Actions for docs.example.com" })).toBeInTheDocument();
  });

  it("opens the items it is given, and a press does what it says", async () => {
    const onDelete = vi.fn();
    render(
      <RowMenu name="API_KEY">
        <MenuItem>Edit</MenuItem>
        <MenuSeparator />
        <MenuItem destructive onClick={onDelete}>
          Delete
        </MenuItem>
      </RowMenu>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Actions for API_KEY" }));
    expect(await screen.findAllByRole("menuitem")).toHaveLength(2);
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("has no tooltip of its own: the row it sits in already names it", async () => {
    render(
      <RowMenu name="API_KEY">
        <MenuItem>Edit</MenuItem>
      </RowMenu>,
    );
    await userEvent.hover(screen.getByRole("button", { name: "Actions for API_KEY" }));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("is one phrase in Spanish, whatever the row", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(
      <RowMenu name="API_KEY">
        <MenuItem>Editar</MenuItem>
      </RowMenu>,
    );
    expect(screen.getByRole("button", { name: "Acciones de API_KEY" })).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    render(
      <RowMenu name="API_KEY">
        <MenuItem>Edit</MenuItem>
      </RowMenu>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Actions for API_KEY" }));
    await screen.findByRole("menu");
    await expectNoAxeViolations(document.body);
  });
});
