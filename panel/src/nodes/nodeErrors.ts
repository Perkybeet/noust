/**
 * The words around a node the central could not use. The central's `detail`, `hint` and
 * `output` (ssh's stderr, the node's own answer) stay verbatim; only the title, and a hint
 * when the central sent none, come from the catalog.
 */

import { isNodeError } from "../api/errors";
import type { T } from "../i18n";

export interface NodeErrorWords {
  /** "web-2 is not answering". */
  title: string;
  /** The central's hint when it sent one, otherwise a sentence of the console's. */
  hint: string;
}

/** The title and hint for a node error (node_unreachable, node_refused); null for any other error. */
export function nodeErrorWords(t: T, error: unknown, fallbackNode: string | null = null): NodeErrorWords | null {
  if (!isNodeError(error)) return null;
  const node = error.node ?? fallbackNode;
  if (node === null) return null;
  const refused = error.error === "node_refused";
  return {
    title: t(refused ? "fleet.errors.refusedTitle" : "fleet.errors.unreachableTitle", { node }),
    hint: error.hint ?? t(refused ? "fleet.errors.refusedHint" : "fleet.errors.unreachableHint", { node }),
  };
}
