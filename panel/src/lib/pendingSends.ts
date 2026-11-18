/**
 * The copies that were made and have not reached a destination (item 201), as the listing of every kind of copy says them:
 * `pending_sends` on each copy, one entry for each destination it is still owed to. The API types the entry as an open record
 * (a copy's listing has one shape for four kinds of copy), so this is the one reader that says what an entry holds.
 */

/** One destination a copy is still owed to. */
export interface PendingSend {
  /** The destination it is owed to. */
  destination: string;
  /** What the destination said the last time, verbatim: a program's or a service's own words. */
  reason: string;
  /** The deferred tries made after the run that took the copy. */
  attempts: number;
  /** When it was first owed, as the server gave it; null when it did not say. */
  since: string | null;
  /** When it is tried next; null when the server did not say. */
  nextAttemptAt: string | null;
  /** What the destination said is of the kind waiting does not mend: it needs a change there. */
  permanent: boolean;
}

/** What any kind of copy has of this: the field of its listing, which a server before this one does not send. */
export interface HasPendingSends {
  pending_sends?: readonly Readonly<Record<string, unknown>>[] | null | undefined;
}

function text(entry: Readonly<Record<string, unknown>>, key: string): string {
  const value = entry[key];
  return typeof value === "string" ? value : "";
}

/**
 * The destinations a copy is still owed to, the soonest to be tried first. An empty list is a copy that is everywhere it was meant to
 * be; so is a copy listed by a server that does not send the field, which is how a server before 4.3 says it.
 */
export function pendingSendsOf(copy: HasPendingSends): PendingSend[] {
  const sends: PendingSend[] = [];
  for (const entry of copy.pending_sends ?? []) {
    const destination = text(entry, "destination");
    if (destination === "") continue;
    const attempts = entry["attempts"];
    sends.push({
      destination,
      reason: text(entry, "reason"),
      attempts: typeof attempts === "number" && Number.isFinite(attempts) ? attempts : 0,
      since: text(entry, "since") || null,
      nextAttemptAt: text(entry, "next_attempt_at") || null,
      permanent: entry["permanent"] === true,
    });
  }
  // One without a time goes last: nothing says when it is tried.
  const last = "￿";
  return sends.sort((a, b) => (a.nextAttemptAt ?? last).localeCompare(b.nextAttemptAt ?? last));
}

/**
 * What a group of copies is owed, one entry for each destination (an application's backups, in the row that sums them up): the
 * soonest next try, the longest wait, the most tries, and what the destination said of the newest of them.
 */
export function pendingAcross(copies: readonly HasPendingSends[]): PendingSend[] {
  const byDestination = new Map<string, PendingSend>();
  for (const send of copies.flatMap(pendingSendsOf)) {
    const known = byDestination.get(send.destination);
    if (known === undefined) {
      byDestination.set(send.destination, send);
      continue;
    }
    byDestination.set(send.destination, {
      ...known,
      attempts: Math.max(known.attempts, send.attempts),
      since: [known.since, send.since].filter((value): value is string => value !== null).sort()[0] ?? null,
      nextAttemptAt: [known.nextAttemptAt, send.nextAttemptAt].filter((value): value is string => value !== null).sort()[0] ?? null,
      permanent: known.permanent || send.permanent,
    });
  }
  return [...byDestination.values()];
}

/** Whether any copy of a list is owed to a destination: the lists draw their column only then. */
export function anyPending(copies: readonly HasPendingSends[]): boolean {
  return copies.some((copy) => pendingSendsOf(copy).length > 0);
}
