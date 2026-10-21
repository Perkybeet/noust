import { screen } from "@testing-library/react";

import type { renderConsole } from "./console";

/** The ways in that are not on the row of "Where the code is": behind its "More ways" button. */
const BEHIND_MORE = new Set(["Compose", "From a recipe", "Import an application", "Already running", "Desde una receta", "Importar aplicación", "Ya en marcha"]);

/**
 * Chooses a way in of the new-application wizard by its name, the way a person does: a radio on the
 * row, or an item of the menu that "More ways" opens.
 */
export async function chooseWay(user: ReturnType<typeof renderConsole>["user"], name: string): Promise<void> {
  if (BEHIND_MORE.has(name)) {
    await user.click(await screen.findByRole("button", { name: /^(More ways|Más formas)$/ }));
    await user.click(await screen.findByRole("menuitem", { name }));
    return;
  }
  await user.click(await screen.findByRole("radio", { name }));
}
