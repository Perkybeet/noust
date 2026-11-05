/**
 * Where an application comes from, as the first page of "New application" asks it (4.2, item 99): five ways, one page,
 * and the way "something that already runs here" is one of them instead of a button beside the wizard. The wizard's own
 * state is a `SourceMode` (what it was doing before the five existed); an origin is the card that holds one or more
 * of them, and these two functions are the whole of how the cards and the modes go back and forth.
 */

import type { PlainKey } from "../../i18n";
import type { SourceMode } from "./SourceStep";

export type Origin = "repo" | "catalog" | "image" | "files" | "existing";

/** In the order the cards are drawn, the commonest first. */
export const ORIGINS: readonly Origin[] = ["repo", "catalog", "image", "files", "existing"];

/** The words of each card, as catalog keys. */
export const ORIGIN_WORDS: Readonly<Record<Origin, { label: PlainKey; description: PlainKey }>> = {
  repo: { label: "newApp.origins.repo.label", description: "newApp.origins.repo.description" },
  catalog: { label: "newApp.origins.catalog.label", description: "newApp.origins.catalog.description" },
  image: { label: "newApp.origins.image.label", description: "newApp.origins.image.description" },
  files: { label: "newApp.origins.files.label", description: "newApp.origins.files.description" },
  existing: { label: "newApp.origins.existing.label", description: "newApp.origins.existing.description" },
};

/**
 * The card a mode is under. A source that is an uploaded archive (`upload:<id>`) is files, whatever mode the upload
 * left the wizard in: the person chose it on the files card and the card they see lit must be that one.
 */
export function originOf(mode: SourceMode, source = ""): Origin {
  switch (mode) {
    case "recipe":
      return "catalog";
    case "image":
      return "image";
    case "files":
    case "compose":
      return "files";
    case "adopt":
    case "import":
      return "existing";
    case "github":
    case "gitlab":
    case "manual":
      return /^upload:[0-9a-f]{32}$/.test(source.trim()) ? "files" : "repo";
  }
}

/**
 * The mode a card starts in. The repository card opens on the GitHub list where this server has GitHub connected, and on
 * the box otherwise; the files card on the files page; the card of what already runs on the stack that runs.
 */
export function modeOfOrigin(origin: Origin, githubConnected: boolean): SourceMode {
  switch (origin) {
    case "repo":
      return githubConnected ? "github" : "manual";
    case "catalog":
      return "recipe";
    case "image":
      return "image";
    case "files":
      return "files";
    case "existing":
      return "adopt";
  }
}
