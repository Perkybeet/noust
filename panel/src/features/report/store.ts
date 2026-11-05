/**
 * Who asks for the report of a problem to open, and where it stands.
 *
 * It opens from two places the frame owns, the help menu and the error screen, and from nothing else, so the
 * request lives outside the tree: whoever asks, the one dialog the frame mounts (`ReportHost`) answers. The words
 * of the dialog are a namespace of their own, fetched when it is first asked for and not with the page.
 */

import { useSyncExternalStore } from "react";

import { getLocale } from "../../app/locale";
import { loadNamespaces } from "../../i18n/catalogs";

export interface ReportRequest {
  /** Changes with every opening, so a second one starts from nothing. */
  id: number;
  /** The menu, or the error screen (which brings the error with it). */
  from: "help" | "error";
  /** The page the person was on, which says where it happens. */
  pathname: string;
  /** What the error screen showed, verbatim; null from the menu. */
  error: string | null;
}

let current: ReportRequest | null = null;
let counter = 0;
const listeners = new Set<() => void>();

function publish(next: ReportRequest | null): void {
  current = next;
  for (const listener of listeners) listener();
}

/**
 * Opens the report of a problem.
 *
 * @param options - Who asks, the page the person is on and, from the error screen, the error as it was shown.
 * @returns When the dialog has been asked to open: its words are loaded first, so it never opens in keys.
 */
export async function openReport(options: { from: ReportRequest["from"]; pathname: string; error?: string }): Promise<void> {
  const locale = getLocale();
  // Both: the language's own, and English for any sentence it has not been given yet.
  await Promise.all([loadNamespaces("en", ["report"]), loadNamespaces(locale, ["report"])]).catch(() => undefined);
  counter += 1;
  publish({ id: counter, from: options.from, pathname: options.pathname, error: options.error ?? null });
}

/** Closes it. */
export function closeReport(): void {
  publish(null);
}

/** The request the dialog is open for; null while it is closed. */
export function useReportRequest(): ReportRequest | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => current,
  );
}
