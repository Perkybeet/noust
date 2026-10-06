import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { TablePagination, pageWindow } from "./TablePagination";

describe("pageWindow", () => {
  it("counts rows from 1 and stops at the total", () => {
    expect(pageWindow(57, 20, 0)).toEqual({ from: 1, to: 20, pages: 3, page: 0 });
    expect(pageWindow(57, 20, 2)).toEqual({ from: 41, to: 57, pages: 3, page: 2 });
  });

  it("keeps a page that no longer exists on the last one", () => {
    expect(pageWindow(57, 20, 9)).toEqual({ from: 41, to: 57, pages: 3, page: 2 });
    expect(pageWindow(57, 20, -3).page).toBe(0);
  });

  it("holds every row on one page for all, and for a size larger than the list", () => {
    expect(pageWindow(57, "all", 4)).toEqual({ from: 1, to: 57, pages: 1, page: 0 });
    expect(pageWindow(57, 100, 1)).toEqual({ from: 1, to: 57, pages: 1, page: 0 });
  });

  it("has nothing to show for an empty list", () => {
    expect(pageWindow(0, 20, 0)).toEqual({ from: 0, to: 0, pages: 1, page: 0 });
  });
});

function setup(overrides: Partial<Parameters<typeof TablePagination>[0]> = {}) {
  const onPageChange = vi.fn();
  const onPageSizeChange = vi.fn();
  render(
    <TablePagination total={57} page={0} pageSize={20} onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} {...overrides} />,
  );
  return { onPageChange, onPageSizeChange };
}

describe("TablePagination", () => {
  it("says which rows are shown out of how many", () => {
    setup({ page: 1 });
    expect(screen.getByText("Showing 21–40 of 57")).toBeInTheDocument();
  });

  it("goes to the next and the previous page, and cannot go past either end", async () => {
    const { onPageChange } = setup({ page: 0 });
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it("disables Next on the last page and goes back from it", async () => {
    const { onPageChange } = setup({ page: 2 });
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it("offers 20, 50, 100 and all, with the current size chosen", async () => {
    const { onPageSizeChange } = setup({ pageSize: 50 });
    const trigger = screen.getByRole("combobox", { name: "Rows per page" });
    expect(trigger).toHaveTextContent("50");
    await userEvent.click(trigger);
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["20", "50", "100", "All"]);
    await userEvent.click(screen.getByRole("option", { name: "All" }));
    expect(onPageSizeChange).toHaveBeenCalledWith("all");
  });

  it("names the page in words for a screen reader, without a second 'Page 2 of 3' on screen", () => {
    setup({ page: 1 });
    expect(screen.getByRole("group", { name: "Pagination" })).toBeInTheDocument();
    expect(screen.getByText("Page 2 of 3")).toHaveClass("sr-only");
  });

  it("has no accessibility violations", async () => {
    setup({ page: 1 });
    await expectNoAxeViolations(document.body);
  });

  it("speaks Spanish once the language switches", async () => {
    await act(async () => {
      await setLocale("es");
    });
    setup({ page: 1 });
    const group = screen.getByRole("group", { name: "Paginación" });
    expect(within(group).getByText("Mostrando 21–40 de 57")).toBeInTheDocument();
    expect(within(group).getByRole("combobox", { name: "Filas por página" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Página siguiente" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });
});
