/**
 * Which frame is drawn for a window of a given width, and what the preferences may still decide.
 * The rules are the owner's (4.1 navigation design, section 4):
 *
 * - below 1280 px there is one bar: two columns need about 1410 px, measured on the mockups;
 * - from 1280 to 1439 px the main bar is folded to icons and, unfolded, opens over the page without
 *   pushing it: responsive by width, never by use, so nothing folds by itself;
 * - from 1440 px the saved preference decides, both the presentation and the fold.
 *
 * The width is read from `window.innerWidth`, the width the media queries use, and only its tier
 * is observed: a window dragged across a tier redraws the frame once, not at every pixel.
 */

import { useSyncExternalStore } from "react";

import { usePreferences } from "./preferences";
import type { NavPresentation, Preferences, RailFold } from "./preferences";

/** Below this the frame is one bar. */
export const SINGLE_BELOW = 1280;
/** From here to `WIDE_FROM` the main bar is folded and opens over the page. */
export const RAIL_FOLDED_ABOVE = 1280;
/** From here the preference rules. */
export const WIDE_FROM = 1440;
/** Below this the bar goes to the foot of the screen and the menu is a drawer. */
export const PHONE_BELOW = 1024;

export type Tier = "phone" | "narrow" | "middle" | "wide";

export function tierOf(width: number): Tier {
  if (width < PHONE_BELOW) return "phone";
  if (width < SINGLE_BELOW) return "narrow";
  if (width < WIDE_FROM) return "middle";
  return "wide";
}

export interface NavLayout {
  tier: Tier;
  /** The window is a phone: the areas are a bar at the foot and the rest is a drawer. */
  phone: boolean;
  presentation: NavPresentation;
  /** What is drawn: a saved fold only applies where there is room (from 1440 px). */
  rail: RailFold;
  /** The main bar is folded for want of room and unfolds over the page. */
  overlay: boolean;
}

/** The layout for a width and the saved choices: a pure function, so every edge of it is a test. */
export function layoutFor(width: number, preferences: Preferences): NavLayout {
  const tier = tierOf(width);
  const double = (tier === "middle" || tier === "wide") && preferences.presentation === "double";
  const overlay = double && tier === "middle";
  return {
    tier,
    phone: tier === "phone",
    presentation: double ? "double" : "single",
    rail: overlay ? "folded" : double ? preferences.rail : "expanded",
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
  const width = { phone: 0, narrow: PHONE_BELOW, middle: SINGLE_BELOW, wide: WIDE_FROM }[tier];
  return layoutFor(width, preferences);
}
