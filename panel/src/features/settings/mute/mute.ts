/**
 * Turning every notification off for a while: what "until when" means, as moments of the person's own clock.
 *
 * The answer is written as `notifications.muted_until`, a moment with its time zone before which the server's
 * notifier sends nothing (docs/notifications.md); "until I turn them on" is the master switch off. The moments
 * are worked out here, on the clock of whoever asks, because "tomorrow at 09:00" is theirs and not the server's.
 */

import { formatClock, formatDate, parseTimestamp } from "../../../lib/format";

export const MUTE_KEY = "notifications.muted_until";

/** For an hour, until tomorrow morning, or until the person turns them on: what they are asked. */
export type MuteChoice = "hour" | "morning" | "off";

/** The hour of the morning a pause "until tomorrow" ends at. */
export const MORNING_HOUR = 9;

/**
 * When a pause that ends at a moment ends.
 *
 * @param choice `hour` or `morning`: the two that are a moment.
 * @param now Now, on the person's clock.
 * @returns The moment: an hour from now, or the next 09:00 of the next day.
 */
export function mutedUntilFor(choice: "hour" | "morning", now: Date): Date {
  if (choice === "hour") return new Date(now.getTime() + 60 * 60 * 1000);
  // "Tomorrow" is the next calendar day, even at five in the morning: a person who says it at that hour means to sleep.
  const morning = new Date(now);
  morning.setDate(morning.getDate() + 1);
  morning.setHours(MORNING_HOUR, 0, 0, 0);
  return morning;
}

/**
 * The moment a stored pause ends, if it has not ended yet.
 *
 * @param stored What `notifications.muted_until` holds: empty, or a moment with its zone.
 * @param now Now.
 * @returns The moment while it is still to come, null for no pause, an ended one or one nobody can read.
 */
export function pausedUntil(stored: string, now: Date): Date | null {
  if (stored.trim() === "") return null;
  const until = parseTimestamp(stored);
  return until !== null && until.getTime() > now.getTime() ? until : null;
}

/**
 * A pause's end the way a person says it: the clock if it ends today ("14:05"), else the day and the clock ("Nov 6 09:00").
 * Minutes are enough: the pause is not a deadline to the second, and the seconds of "an hour from now" are noise.
 *
 * @param until When the pause ends.
 * @param now Now, on the person's clock.
 * @returns The text that goes in "Silenced until {when}".
 */
export function untilLabel(until: Date, now: Date): string {
  const today = until.getFullYear() === now.getFullYear() && until.getMonth() === now.getMonth() && until.getDate() === now.getDate();
  return today ? formatClock(until) : `${formatDate(until, { year: false })} ${formatClock(until)}`;
}
