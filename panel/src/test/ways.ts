import { screen } from "@testing-library/react";

import type { renderConsole } from "./console";

/** The ways of starting that are not a thing to paste or search for: folded under "Other ways to start". */
const OTHER_WAYS = new Set(["Compose", "Import an application", "Already running", "From GitLab", "Connect GitHub", "Importar aplicación", "Ya en marcha", "Desde GitLab", "Conectar GitHub"]);

/** The recipes of the catalog, all of them: the start of the step shows the popular ones until this is pressed. */
const RECIPES = new Set(["From a recipe", "Desde una receta"]);

/**
 * Gets to a way of starting a new application by its name, the way a person does. A repository, a folder or
 * an image is not a way to choose: it is pasted in the one box, which the step opens on. The catalog is the
 * "Browse all recipes" button under it, and the rest of the ways are behind "Other ways to start".
 */
export async function chooseWay(user: ReturnType<typeof renderConsole>["user"], name: string): Promise<void> {
  if (RECIPES.has(name)) {
    await user.click(await screen.findByRole("button", { name: /^(Browse (all \d+ recipes|the \d+ recipe)|Ver (las \d+ recetas|la \d+ receta))$/ }));
    return;
  }
  if (OTHER_WAYS.has(name)) {
    await openOtherWays(user);
    await user.click(await screen.findByRole("button", { name }));
  }
}

/** Opens the folded "Other ways to start" of the first step, once: asking again leaves it open. */
export async function openOtherWays(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  const fold = await screen.findByRole("button", { name: /^(Other ways to start|Otras formas de empezar)$/ });
  if (fold.getAttribute("aria-expanded") !== "true") await user.click(fold);
}
