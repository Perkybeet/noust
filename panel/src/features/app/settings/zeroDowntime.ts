/** The drain the backend accepts, in seconds (`MAX_DRAIN_SECONDS` in `wasm.deployers.bluegreen`). */
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
export function parseDrain(text: string): ParsedDrain {
  const value = text.trim();
  if (!/^\d+$/.test(value)) return { seconds: null, error: `Give a whole number of seconds from ${String(DRAIN_MIN)} to ${String(DRAIN_MAX)}.` };
  const seconds = Number.parseInt(value, 10);
  if (seconds < DRAIN_MIN || seconds > DRAIN_MAX) {
    return { seconds: null, error: `Drain from ${String(DRAIN_MIN)} to ${String(DRAIN_MAX)} seconds, not ${String(seconds)}.` };
  }
  return { seconds, error: null };
}

/**
 * An instance's name, capitalised for a heading: "blue" is "Blue". The colours are names, not
 * colours to paint: on screen, colour only ever encodes state.
 */
export function instanceName(color: string): string {
  return color === "" ? "Instance" : `${color.charAt(0).toUpperCase()}${color.slice(1)}`;
}
