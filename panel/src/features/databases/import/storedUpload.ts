/**
 * The upload this browser left unfinished, remembered so the next visit can offer to continue
 * it. The bytes are on the server (it holds them for a day); what the browser cannot keep is
 * the file, so the operator chooses it again and the upload goes on from where the server got
 * to. Only the identifier, the name and the size are kept: nothing of the dump.
 */

import { isApiError, request } from "../../../api/client";

export interface StoredUpload {
  id: string;
  name: string;
  size: number;
}

const KEY = "noust.dump-upload";

export function readStoredUpload(): StoredUpload | null {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(KEY) ?? "null");
    if (typeof parsed !== "object" || parsed === null) return null;
    const { id, name, size } = parsed as Partial<StoredUpload>;
    return typeof id === "string" && typeof name === "string" && typeof size === "number" ? { id, name, size } : null;
  } catch {
    // Storage that cannot be read (disabled, or not ours) remembers nothing.
    return null;
  }
}

export function writeStoredUpload(upload: StoredUpload): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(upload));
  } catch {
    // A browser that cannot store it still has the upload going; only a reload would forget it.
  }
}

export function clearStoredUpload(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // Nothing to clear where nothing can be stored.
  }
}

/**
 * Throws an upload away on the server. An upload the server no longer has (swept after a day, or
 * never the caller's) is already gone, and any other refusal leaves a file only Noust can read
 * and that the server sweeps by itself, so none of them is the operator's to deal with.
 */
export async function discardUpload(id: string): Promise<void> {
  try {
    await request("delete", "/api/databases/imports/uploads/{upload_id}", { params: { upload_id: id } });
  } catch (error: unknown) {
    if (!isApiError(error)) throw error;
  }
}
