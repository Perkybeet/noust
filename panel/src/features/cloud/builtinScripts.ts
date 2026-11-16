import { hasMessage } from "../../i18n";
import type { PlainKey, T } from "../../i18n";

/**
 * What a script or fragment says it is for. The ones Noust ships are described in this console's language
 * (the server's English is what a library of the operator's own, or a built-in this console does not know,
 * shows): their names are fixed, so the words are keyed by name.
 */
export function scriptDescription(t: T, entry: { name: string; builtin: boolean; description: string }): string {
  const key = `cloud.scripts.builtinDescription.${entry.name.replace(/-/g, "_")}`;
  return entry.builtin && hasMessage(key) ? t(key as PlainKey) : entry.description;
}
