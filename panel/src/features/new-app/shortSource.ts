/** The names of a source a page may show, in a module of their own: the page of an application that failed to deploy shows one, and the wizard's logic is far heavier than that. */

/**
 * A source in a few characters, for places too narrow for all of it: the end of a path
 * (`src/storefront`), the repository of a URL (`you/app`).
 */
export function shortSource(value: string): string {
  const source = value.trim().replace(/\/+$/, "").replace(/\.git$/, "");
  const parts = source.split(/[/:]/).filter((part) => part !== "");
  return parts.slice(-2).join("/") || source;
}
