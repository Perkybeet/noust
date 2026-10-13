/**
 * A DNS record somebody else made at a name Noust wants to point somewhere. Noust never
 * overwrites one by surprise: the server refuses (`409 record_conflict`) and says which record
 * is in the way and what it holds, and the operator confirms replacing exactly that one. The
 * same refusal comes from an application's DNS, a domain added, and a tunnel's route.
 */

import { isApiError } from "../../api/client";

export interface RecordConflict {
  hostname: string;
  /** The provider's id of the record in the way: what the confirmation names. */
  record_id: string;
  /** What it holds now. */
  current: string;
  /** What Noust wants it to hold. */
  wanted: string;
}

/** The record a refusal names, or null when the error is something else. */
export function conflictOf(error: unknown): RecordConflict | null {
  if (!isApiError(error) || error.error !== "record_conflict") return null;
  const { hostname, record_id: id, current, wanted } = error.extra;
  if (typeof hostname !== "string" || typeof id !== "string" || typeof current !== "string" || typeof wanted !== "string") return null;
  return { hostname, record_id: id, current, wanted };
}
