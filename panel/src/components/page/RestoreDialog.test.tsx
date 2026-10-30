import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/errors";
import { expectNoAxeViolations } from "../../test/axe";
import { RestoreDialog } from "./RestoreDialog";
import type { RestoreDialogProps } from "./RestoreDialog";

const WORDS = {
  into: "Restore into",
  intoNew: "A new database",
  intoNewHint: "Beside this one; nothing that exists is touched.",
  intoReplace: "orders, replacing what it holds",
  intoReplaceHint: "A safety copy is taken first.",
  replaceNote: "Replaces what orders holds now. A safety copy is taken first.",
  newName: "Name of the new database",
  newNameHelp: "Letters, digits and underscores.",
  nameInvalid: "That name is not one the server accepts.",
  actionReplace: "Restore over orders",
  actionNew: "Restore into a new database",
};

function props(over: Partial<RestoreDialogProps> = {}): RestoreDialogProps {
  return {
    open: true,
    onOpenChange: () => undefined,
    title: "Restore a dump",
    source: "From orders.sql, on this server.",
    existing: "orders",
    defaultTarget: "new",
    suggestion: "orders_restored",
    isValidName: (name) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(name),
    words: WORDS,
    onRestore: () => Promise.resolve(),
    ...over,
  };
}

describe("RestoreDialog", () => {
  it("goes into a new place by default, which touches nothing and asks one question", async () => {
    const onRestore = vi.fn(() => Promise.resolve());
    render(<RestoreDialog {...props({ onRestore })} />);
    const dialog = await screen.findByRole("alertdialog", { name: "Restore a dump" });
    expect(within(dialog).getByRole("combobox", { name: "Restore into" })).toHaveTextContent("A new database");
    // The name the server suggests is in the field, and nothing has to be typed to confirm.
    expect(within(dialog).getByRole("textbox", { name: /Name of the new database/ })).toHaveValue("orders_restored");
    expect(within(dialog).queryByRole("textbox", { name: /to confirm/ })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore into a new database" }));
    await waitFor(() => {
      expect(onRestore).toHaveBeenCalledWith({ target: "new", name: "orders_restored" });
    });
  });

  it("waits for a name the server would accept, and says which rule it breaks", async () => {
    render(<RestoreDialog {...props()} />);
    const dialog = await screen.findByRole("alertdialog");
    const field = within(dialog).getByRole("textbox", { name: /Name of the new database/ });
    await userEvent.clear(field);
    await userEvent.type(field, "1-bad");
    expect(within(dialog).getByRole("button", { name: "Restore into a new database" })).toBeDisabled();
    expect(within(dialog).getByText("That name is not one the server accepts.")).toBeInTheDocument();
    await userEvent.clear(field);
    await userEvent.type(field, "good_name");
    expect(within(dialog).getByRole("button", { name: "Restore into a new database" })).toBeEnabled();
  });

  it("over what exists says what is replaced and kept, and asks for its name to be typed", async () => {
    const onRestore = vi.fn(() => Promise.resolve());
    render(<RestoreDialog {...props({ defaultTarget: "replace", onRestore })} />);
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Replaces what orders holds now. A safety copy is taken first.")).toBeInTheDocument();
    const action = within(dialog).getByRole("button", { name: "Restore over orders" });
    expect(action).toBeDisabled();
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Type orders to confirm/ }), "orders");
    expect(action).toBeEnabled();
    await userEvent.click(action);
    await waitFor(() => {
      expect(onRestore).toHaveBeenCalledWith({ target: "replace", name: "orders" });
    });
  });

  it("gives its own options the place where the restore goes, so one can exist only over what exists", async () => {
    render(<RestoreDialog {...props({ options: (target) => (target === "replace" ? <p>Its tables are dropped.</p> : <p>Beside it.</p>) })} />);
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Beside it.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("combobox", { name: "Restore into" }));
    await userEvent.click(await screen.findByRole("option", { name: /orders, replacing what it holds/ }));
    expect(within(dialog).getByText("Its tables are dropped.")).toBeInTheDocument();
    expect(within(dialog).queryByText("Beside it.")).not.toBeInTheDocument();
  });

  it("offers no choice where there is nothing to replace, or nothing new to make", async () => {
    const { rerender } = render(<RestoreDialog {...props({ existing: null })} />);
    let dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).queryByRole("combobox", { name: "Restore into" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: /Name of the new database/ })).toBeInTheDocument();

    rerender(<RestoreDialog {...props({ replaceOnly: true, defaultTarget: "replace" })} />);
    dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).queryByRole("combobox", { name: "Restore into" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: /Type orders to confirm/ })).toBeInTheDocument();
  });

  it("says a refusal as the system said it, and stays open", async () => {
    const onRestore = vi.fn(() => Promise.reject(new ApiError(409, "conflict", "A restore of orders is already running", "Wait for it to finish.")));
    render(<RestoreDialog {...props({ onRestore })} />);
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore into a new database" }));
    expect(await within(dialog).findByText("A restore of orders is already running")).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  it("names the server on a fleet and has no accessibility violations", async () => {
    render(<RestoreDialog {...props({ server: "web-2" })} />);
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("web-2");
    await expectNoAxeViolations(dialog);
  });
});
