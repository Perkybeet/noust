import { loadCatalog, watchMisses } from "../i18n/catalogs";
import { CHROME, catalogsIdle, dropNamespaces } from "../i18n/catalogs";
import * as english from "../i18n/en";

/**
 * A browser that has just opened the console: English holds its chrome and nothing else, which is
 * what the bundle ships. Everything a page draws after this has to come from what the router loads
 * for it, and every `t()` that finds its namespace missing is recorded.
 *
 * Tests are otherwise warm (`setup.ts` loads English whole), which is what makes them synchronous
 * for whoever writes them; the ones that check the loading itself start here instead.
 *
 * @returns The misses so far, as `locale:namespace`, and `restore`, which makes the catalog whole again.
 */
export function goCold(): { readonly misses: string[]; readonly restore: () => Promise<void> } {
  dropNamespaces("en", CHROME);
  dropNamespaces("es", CHROME);
  const misses: string[] = [];
  const stop = watchMisses((locale, namespace) => {
    const entry = `${locale}:${namespace}`;
    if (!misses.includes(entry)) misses.push(entry);
  });
  return {
    misses,
    restore: async () => {
      stop();
      // What a page asked for is still arriving: let it land here, not in the next test's cold start.
      await catalogsIdle();
      await loadCatalog("en");
    },
  };
}

/** Every message key of the English catalog: what `t()` returns when it finds no text. */
const KEYS: ReadonlySet<string> = (() => {
  const keys = new Set<string>();
  const visit = (node: unknown, prefix: string): void => {
    if (typeof node === "string") {
      keys.add(prefix);
    } else if (typeof node === "object" && node !== null) {
      const entries = Object.entries(node);
      // A plural is one message, whose key is the object's own.
      if (entries.some(([name]) => name === "other")) keys.add(prefix);
      else for (const [name, child] of entries) visit(child, prefix === "" ? name : `${prefix}.${name}`);
    }
  };
  visit({ ...english }, "");
  return keys;
})();

/**
 * The message keys drawn as text (or as an accessible name, a title, a placeholder) in a document:
 * a `t()` that found nothing prints its key, and an operator reads `databases.list.title`.
 *
 * @param root - Where to look.
 * @returns The keys found, once each.
 */
export function drawnKeys(root: ParentNode = document.body): string[] {
  const found = new Set<string>();
  const check = (text: string | null): void => {
    const trimmed = text?.trim();
    if (trimmed !== undefined && KEYS.has(trimmed)) found.add(trimmed);
  };
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) check(node.textContent);
  for (const element of root.querySelectorAll("[aria-label], [title], [placeholder], [alt]")) {
    for (const name of ["aria-label", "title", "placeholder", "alt"]) check(element.getAttribute(name));
  }
  return [...found].sort();
}
