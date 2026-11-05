/**
 * Which job's end the task centre has already said, so the server's own notice for it does not say it twice.
 *
 * The server sends a `notice` right after the `job` event that ended a job, and the console has always turned it
 * into a toast. A task of the person's that ends is now said by the task centre (with a way to see it, or not at
 * all when they are on its page): an event is said in one channel, never two (docs/DESIGN.md, 6.12). The `job`
 * event is handled first, in the same frame of the stream, so it leaves a mark here and the `notice` that follows
 * takes it. Anything else the server says (a task that is not the person's, a failure nobody was waiting for)
 * has no mark and is told as before.
 */

/** The notice's own word for how the job ended: `active` (it worked), `failed` or `idle` (cancelled). */
export type NoticeState = "active" | "failed" | "idle";

/** How long a mark waits for its notice. They come in the same frame, so a moment is plenty. */
export const HANDLED_FOR_MS = 1_500;

let mark: { state: NoticeState; until: number } | null = null;

export function markJobEndHandled(state: NoticeState, now: number = Date.now()): void {
  mark = { state, until: now + HANDLED_FOR_MS };
}

/** Takes the mark when this notice is the one it was left for. */
export function takeJobEndHandled(state: string, now: number = Date.now()): boolean {
  const held = mark;
  if (held?.state !== state || now > held.until) return false;
  mark = null;
  return true;
}

/** Forgets the mark. For tests. */
export function resetHandled(): void {
  mark = null;
}
