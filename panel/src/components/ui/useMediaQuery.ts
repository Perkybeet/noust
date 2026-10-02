import { useCallback, useSyncExternalStore } from "react";

/**
 * Whether a media query matches, kept current as the window changes. Read synchronously on the
 * first render, so a layout that depends on it (a table that becomes cards on a phone) is
 * drawn right the first time and never shifts.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (notify: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", notify);
      return () => {
        list.removeEventListener("change", notify);
      };
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** Tailwind's `sm` breakpoint, where the console's layouts change from phone to wider. */
export const SM_UP = "(min-width: 40rem)";
/**
 * The operator asked for less motion. The global rule in `app.css` already stops every CSS
 * animation; a component reads this too when it should not even mark something as moving.
 */
export const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
/** Tailwind's `lg` breakpoint, where the sidebar appears. */
export const LG_UP = "(min-width: 64rem)";
