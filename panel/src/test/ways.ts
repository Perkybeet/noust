import { screen } from "@testing-library/react";

import type { renderConsole } from "./console";

type User = ReturnType<typeof renderConsole>["user"];

/** The recipes of the catalog, all of them: the card of the catalog shows every one. */
const RECIPES = new Set(["From a recipe", "Desde una receta"]);
const COMPOSE = new Set(["Compose"]);
const IMPORT = new Set(["Import an application", "Importar aplicación"]);
const RUNNING = new Set(["Already running", "Ya en marcha"]);

/** The five cards of the first page, by the start of their name in either language. */
const CARD = {
  catalog: /^(From the catalog|Del catálogo)/,
  files: /^(Upload files|Subir los ficheros)/,
  existing: /^(Something that already runs here|Algo que ya corre aquí)/,
};

/**
 * Gets to a way of starting a new application by its name, the way a person does. The page opens on five cards (a
 * repository, the catalog, an image, files, something that already runs here); a repository, a folder or an image
 * is not a way to choose but is pasted in the box the page opens on. Compose is a button of the files card, an import
 * is the second choice of the card for what already runs, and a GitLab or GitHub to connect is a button under the
 * box of the repository card.
 */
export async function chooseWay(user: User, name: string): Promise<void> {
  if (RECIPES.has(name)) {
    await user.click(await screen.findByRole("radio", { name: CARD.catalog }));
    return;
  }
  if (COMPOSE.has(name)) {
    await user.click(await screen.findByRole("radio", { name: CARD.files }));
    await user.click(await screen.findByRole("button", { name: "Compose" }));
    return;
  }
  if (IMPORT.has(name)) {
    await user.click(await screen.findByRole("radio", { name: CARD.existing }));
    await user.click(await screen.findByRole("radio", { name: /^(From another platform or a file|De otra plataforma o de un fichero)$/ }));
    return;
  }
  if (RUNNING.has(name)) {
    await user.click(await screen.findByRole("radio", { name: CARD.existing }));
    return;
  }
  await user.click(await screen.findByRole("button", { name }));
}

/** Opens the card of the files, where an archive is uploaded and a Dockerfile or a Compose file is pasted. */
export async function openFiles(user: User): Promise<void> {
  await user.click(await screen.findByRole("radio", { name: CARD.files }));
}
