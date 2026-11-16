/**
 * Which frame is drawn for a window of a given width, and what the preferences may still decide.
 * The frame is one thing at every desktop width, the main bar and the context column (4.2 design,
 * section 2, point 1); only how much room the main bar takes changes:
 *
 * - from 1024 px the main bar and the column are always there. The two open bars take 464 px (208 and
 *   256), which leaves the page 736 px at 1200 and 816 at 1280, enough for a page whose tables scroll
 *   inside themselves; below 1200 px the main bar is folded to icons (68 px) and, unfolded, opens over
 *   the page without pushing it: responsive by width, never by use, so nothing folds by itself;
 * - from 1200 px the main bar is unfolded, with its words, and the saved fold decides: a person who
 *   wants icons folds it with "Fold the bar" and the browser remembers (item 157: a laptop is 1280 or
 *   1366 px wide, and a bar of thirteen icons nobody has met is not read);
 * - below 1024 px there is no room for either: the areas are at the foot of the screen and the
 *   column is a section selector at the top of the page, which opens a sheet with the same list.
 *
 * The width is read from `window.innerWidth`, the width the media queries use, and only its tier
 * is observed: a window dragged across a tier redraws the frame once, not at every pixel.
 */

import { useSyncExternalStore } from "react";

import { usePreferences } from "./preferences";
import type { Preferences, RailFold } from "./preferences";

/** Below this the bar goes to the foot of the screen and the column is a selector in the page. */
export const PHONE_BELOW = 1024;
/** From here the main bar is drawn unfolded beside the column, and the saved fold decides. */
export const WIDE_FROM = 1200;

export type Tier = "phone" | "middle" | "wide";

export function tierOf(width: number): Tier {
  if (width < PHONE_BELOW) return "phone";
  if (width < WIDE_FROM) return "middle";
  return "wide";
}

export interface NavLayout {
  tier: Tier;
  /** The window is a phone: the areas are a bar at the foot and the column is a selector in the page. */
  phone: boolean;
  /** What is drawn: a saved fold only applies where there is room (from 1200 px). */
  rail: RailFold;
  /** The main bar is folded for want of room and unfolds over the page. */
  overlay: boolean;
}

/** The layout for a width and the saved choices: a pure function, so every edge of it is a test. */
export function layoutFor(width: number, preferences: Preferences): NavLayout {
  const tier = tierOf(width);
  const overlay = tier === "middle";
  return {
    tier,
    phone: tier === "phone",
    rail: overlay ? "folded" : preferences.rail,
    overlay,
  };
}

function subscribeToTier(notify: () => void): () => void {
  window.addEventListener("resize", notify);
  return () => {
    window.removeEventListener("resize", notify);
  };
}

/** The tier of the window, current: the component redraws only when it crosses one. */
export function useTier(): Tier {
  return useSyncExternalStore(
    subscribeToTier,
    () => tierOf(window.innerWidth),
    () => "wide",
  );
}

/** The frame to draw now. */
export function useNavLayout(): NavLayout {
  const tier = useTier();
  const preferences = usePreferences();
  // Any width of the tier gives the same layout: the tier's own lower edge stands for it.
  const width = { phone: 0, middle: PHONE_BELOW, wide: WIDE_FROM }[tier];
  return layoutFor(width, preferences);
}
