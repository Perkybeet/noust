import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { translate } from "../../../i18n/translate";
import type { MessageKey } from "../../../i18n/types";

/** The drain the backend accepts, in seconds (`MAX_DRAIN_SECONDS` in `noust.deployers.bluegreen`). */
export const DRAIN_MIN = 0;
export const DRAIN_MAX = 300;

export interface ParsedDrain {
  seconds: number | null;
  error: string | null;
}

/**
 * The drain field, read as the backend will: a whole number of seconds from 0 to 300. Checked
 * here for a quick answer; the backend checks again and its refusal lands on the field.
 */
export function parseDrain(text: string, locale: Locale = getLocale()): ParsedDrain {
  const value = text.trim();
  if (!/^\d+$/.test(value)) {
    return { seconds: null, error: translate(locale, "appSettings.zeroDowntime.drainInvalid", { min: DRAIN_MIN, max: DRAIN_MAX }) };
  }
  const seconds = Number.parseInt(value, 10);
  if (seconds < DRAIN_MIN || seconds > DRAIN_MAX) {
    return { seconds: null, error: translate(locale, "appSettings.zeroDowntime.drainRange", { min: DRAIN_MIN, max: DRAIN_MAX, value: seconds }) };
  }
  return { seconds, error: null };
}

const COLOR_KEY: Readonly<Record<string, MessageKey>> = {
  blue: "appSettings.zeroDowntime.colorBlue",
  green: "appSettings.zeroDowntime.colorGreen",
};

/**
 * An instance's name, capitalised for a heading: "blue" is "Blue". The colours are names, not
 * colours to paint: on screen, colour only ever encodes state.
 */
export function instanceName(color: string, locale: Locale = getLocale()): string {
  if (color === "") return translate(locale, "appSettings.zeroDowntime.instanceFallback");
  const key = COLOR_KEY[color.toLowerCase()];
  if (key !== undefined) return translate(locale, key);
  return `${color.charAt(0).toUpperCase()}${color.slice(1)}`;
}
