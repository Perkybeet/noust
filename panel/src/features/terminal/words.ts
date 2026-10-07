/**
 * What a session's end and its clock are called on screen.
 *
 * The server says why a session ended with a closed vocabulary (`END_*` in
 * `noust/terminal/sessions.py`); each word has its sentence in the `terminal.ended` catalog, and
 * a word this console does not know is said as "The session ended" rather than guessed at.
 */

import type { T } from "../../i18n";
import type { Ended } from "./connection";

const REASONS: Readonly<Record<string, "idle" | "limit" | "closed" | "killed" | "revoked" | "disconnected" | "shutdown">> = {
  idle: "idle",
  limit: "limit",
  closed: "closed",
  killed: "killed",
  revoked: "revoked",
  disconnected: "disconnected",
  shutdown: "shutdown",
};

/** How a session ended, as a sentence for the person; the idle limit is said in minutes. */
export function endedSentence(t: T, ended: Ended, idleSeconds: number): string {
  if (ended.reason === "exited") {
    return ended.exitCode !== null && ended.exitCode !== 0
      ? t("terminal.ended.exitedWith", { code: ended.exitCode })
      : t("terminal.ended.exited");
  }
  const reason = REASONS[ended.reason];
  if (reason === undefined) return t("terminal.ended.unknown");
  return reason === "idle"
    ? t("terminal.ended.idle", { minutes: Math.max(1, Math.round(idleSeconds / 60)) })
    : t(`terminal.ended.${reason}`);
}

/** Whole seconds left until `at` (a `Date.now()` value), never below zero. */
export function secondsUntil(at: number, now: number): number {
  return Math.max(0, Math.ceil((at - now) / 1000));
}

/** `1:05:09`, `12:03` or `0:42`: a countdown, which a person reads at a glance. */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = String(whole % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}
