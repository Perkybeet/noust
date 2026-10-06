/**
 * Sending a dump to the server in chunks, and finishing the job when the connection does not.
 *
 * A dump is as big as the database it came from, so it never travels as one request: the server
 * opens an upload (a name, a size it will not exceed), takes each chunk at the offset it reports
 * and refuses any other, and closes it when every byte is in. That is what makes it resumable:
 * after a dropped connection the server is asked where it got to and the next chunk starts
 * there; a chunk the server already holds is answered with the offset to continue from, never
 * written twice.
 *
 * This is the whole protocol with no React in it, written over an `UploadApi` so it is tested
 * without a network. The dialog drives one `DumpUploader` and draws its state.
 */

import type { DumpFormat, DumpUpload } from "../../../api/queries/dumpImport";
import { isApiError } from "../../../api/client";

/** What a dump file may end in (`noust.managers.database.uploads.ALLOWED_EXTENSIONS`). */
export const DUMP_EXTENSIONS: readonly string[] = ["sql", "gz", "zip", "tar", "bz2", "xz", "dump", "archive"];

/** The most one chunk carries: well under the 16 MiB the server accepts, so a slow link sees progress. */
export const CHUNK_BYTES = 8 * 1024 ** 2;

/** Retries with the connection in doubt before the upload waits for the operator. */
export const MAX_AUTOMATIC_RETRIES = 8;

/** Seconds to wait before retry number `attempt` (0 is the first): 1, 2, 4... up to 30. */
export function backoffMs(attempt: number): number {
  return Math.min(30_000, 1_000 * 2 ** attempt);
}

/** A file's last extension, lower case, without the dot; empty when it has none. */
export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
}

export type FileProblem = "extension" | "empty" | "tooLarge";

/** Whether a chosen file can be uploaded at all, before a byte moves. */
export function fileProblem(file: { name: string; size: number }, limit: number): FileProblem | null {
  if (!DUMP_EXTENSIONS.includes(extensionOf(file.name))) return "extension";
  if (file.size <= 0) return "empty";
  if (file.size > limit) return "tooLarge";
  return null;
}

/** What the uploader asks of the server: the API, or a stand-in in a test. */
export interface UploadApi {
  create: (fileName: string, size: number) => Promise<DumpUpload>;
  append: (id: string, offset: number, chunk: Blob, signal: AbortSignal) => Promise<DumpUpload>;
  status: (id: string) => Promise<DumpUpload>;
  complete: (id: string) => Promise<DumpUpload>;
  discard: (id: string) => Promise<void>;
}

/** Where an upload is. `waiting` is a connection in doubt, retrying by itself; `paused` waits for the operator. */
export type UploadPhase = "idle" | "starting" | "uploading" | "waiting" | "paused" | "completing" | "done" | "failed" | "cancelled";

export interface UploadSnapshot {
  phase: UploadPhase;
  fileName: string;
  size: number;
  received: number;
  uploadId: string | null;
  /** The retry that is under way or next, from 1. */
  attempt: number;
  /** When the next automatic retry happens, as milliseconds from now; null when none is scheduled. */
  retryInMs: number | null;
  /** What the server says the file is, once the upload is complete. */
  format: DumpFormat | null;
  /** What went wrong, for `failed` and, as the last cause, for `waiting` and `paused`. */
  error: unknown;
}

const IDLE: UploadSnapshot = { phase: "idle", fileName: "", size: 0, received: 0, uploadId: null, attempt: 0, retryInMs: null, format: null, error: null };

/** Statuses that say the connection or a proxy failed, not that the request was wrong. */
const TRANSIENT_STATUS: ReadonlySet<number> = new Set([0, 408, 425, 429, 500, 502, 503, 504]);

/** Whether an error is the connection's, which a later attempt may well not have. */
export function isTransient(error: unknown): boolean {
  return isApiError(error) && TRANSIENT_STATUS.has(error.status) && error.error !== "elevation_required";
}

function offsetOf(error: unknown): number | null {
  if (!isApiError(error) || error.status !== 409) return null;
  const received = error.extra["received"];
  return typeof received === "number" && Number.isInteger(received) && received >= 0 ? received : null;
}

export interface UploaderOptions {
  chunkBytes?: number;
  /** Waits `ms`, or less when told to retry now; a test passes one that does not wait. */
  sleep?: (ms: number, wake: Promise<void>) => Promise<void>;
}

function defaultSleep(ms: number, wake: Promise<void>): Promise<void> {
  return Promise.race([new Promise<void>((resolve) => setTimeout(resolve, ms)), wake]);
}

type Listener = (snapshot: UploadSnapshot) => void;

/**
 * One upload at a time: start it, watch it, retry it now, cancel it. After a dropped connection
 * it asks the server where it got to and goes on from there by itself, waiting longer each time
 * (1 second, 2, 4... up to 30) and giving the operator a Retry after the eighth.
 */
export class DumpUploader {
  private state: UploadSnapshot = IDLE;
  private readonly listeners = new Set<Listener>();
  private controller: AbortController | null = null;
  private wake: (() => void) | null = null;
  private file: File | null = null;
  private running = false;
  private readonly chunkBytes: number;
  private readonly sleep: (ms: number, wake: Promise<void>) => Promise<void>;

  private readonly api: UploadApi;

  constructor(api: UploadApi, options: UploaderOptions = {}) {
    this.api = api;
    this.chunkBytes = options.chunkBytes ?? CHUNK_BYTES;
    this.sleep = options.sleep ?? defaultSleep;
  }

  get snapshot(): UploadSnapshot {
    return this.state;
  }

  /** Whether the operator cancelled: read through a method, because every await is a chance for them to. */
  private isCancelled(): boolean {
    return this.state.phase === "cancelled";
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private set(patch: Partial<UploadSnapshot>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener(this.state);
  }

  /** Opens an upload for the file and sends it. Resolves when the upload ends, one way or another. */
  async start(file: File): Promise<void> {
    if (this.running) return;
    this.file = file;
    this.controller = new AbortController();
    this.state = { ...IDLE, phase: "starting", fileName: file.name, size: file.size };
    this.set({});
    this.running = true;
    try {
      const upload = await this.api.create(file.name, file.size);
      this.set({ uploadId: upload.id, received: upload.received, phase: "uploading" });
      await this.send(upload.id);
    } catch (error: unknown) {
      this.fail(error);
    } finally {
      this.running = false;
    }
  }

  /**
   * Goes on with an upload the server still holds, from where it got to: after a reload, or
   * with the same file chosen again. The file must be the one that was being sent.
   */
  async continueUpload(id: string, file: File): Promise<void> {
    if (this.running) return;
    this.file = file;
    this.controller = new AbortController();
    this.state = { ...IDLE, phase: "starting", fileName: file.name, size: file.size, uploadId: id };
    this.set({});
    this.running = true;
    try {
      const upload = await this.api.status(id);
      if (upload.size !== file.size || upload.file_name !== file.name) {
        // Not the file that was being sent: what is held is of no use, and the server drops it.
        await this.api.discard(id);
        throw new UploadMismatchError(upload.file_name, upload.size);
      }
      this.set({ received: upload.received, format: upload.format ?? null, phase: upload.complete ? "done" : "uploading" });
      if (!upload.complete) await this.send(id);
    } catch (error: unknown) {
      this.fail(error);
    } finally {
      this.running = false;
    }
  }

  /** Retries now, instead of waiting for the next automatic attempt, or after the automatic ones ran out. */
  retryNow(): void {
    if (this.wake !== null) {
      this.wake();
      return;
    }
    if (this.state.phase === "paused" && !this.running && this.file !== null && this.state.uploadId !== null) {
      const id = this.state.uploadId;
      this.running = true;
      this.controller ??= new AbortController();
      this.set({ attempt: 0, retryInMs: null, phase: "uploading" });
      void (async () => {
        try {
          await this.send(id);
        } catch (error: unknown) {
          this.fail(error);
        } finally {
          this.running = false;
        }
      })();
    }
  }

  /** Stops sending and tells the server to throw the upload away. */
  async cancel(): Promise<void> {
    this.controller?.abort();
    this.wake?.();
    const id = this.state.uploadId;
    this.set({ phase: "cancelled", retryInMs: null });
    if (id !== null) {
      try {
        await this.api.discard(id);
      } catch (error: unknown) {
        // The server sweeps an upload nobody finishes after a day; a failure here leaves a file
        // that is private to Noust and goes by itself, so it is not worth an error of its own.
        if (!isApiError(error)) throw error;
      }
    }
  }

  private fail(error: unknown): void {
    if (this.isCancelled()) return;
    this.set({ phase: "failed", error, retryInMs: null });
  }

  /** The loop: chunk after chunk, then the completion, retrying what the connection dropped. */
  private async send(id: string): Promise<void> {
    const file = this.file;
    if (file === null) return;
    let retries = 0;
    let offsetCorrections = 0;
    for (;;) {
      if (this.isCancelled()) return;
      try {
        if (this.state.received < file.size) {
          const from = this.state.received;
          const chunk = file.slice(from, Math.min(from + this.chunkBytes, file.size));
          const signal = (this.controller ??= new AbortController()).signal;
          const answer = await this.api.append(id, from, chunk, signal);
          // Cancelled while the chunk was on its way: the answer is of no interest any more.
          if (this.isCancelled()) return;
          retries = 0;
          offsetCorrections = 0;
          this.set({ received: answer.received, phase: "uploading", attempt: 0, retryInMs: null, error: null });
          continue;
        }
        this.set({ phase: "completing" });
        const finished = await this.api.complete(id);
        if (this.isCancelled()) return;
        this.set({ phase: "done", format: finished.format ?? null, received: finished.received });
        return;
      } catch (error: unknown) {
        if (this.isCancelled() || (error instanceof DOMException && error.name === "AbortError")) return;
        const offset = offsetOf(error);
        if (offset !== null) {
          // The server holds more or less than this side thought: go on from what it holds.
          offsetCorrections += 1;
          if (offsetCorrections > 3) throw error;
          this.set({ received: offset, phase: "uploading" });
          continue;
        }
        if (!isTransient(error)) throw error;
        if (retries >= MAX_AUTOMATIC_RETRIES) {
          this.set({ phase: "paused", error, retryInMs: null, attempt: retries });
          return;
        }
        const wait = backoffMs(retries);
        retries += 1;
        this.set({ phase: "waiting", error, retryInMs: wait, attempt: retries });
        await this.sleep(
          wait,
          new Promise<void>((resolve) => {
            this.wake = resolve;
          }),
        );
        this.wake = null;
        if (this.isCancelled()) return;
        // After a doubt, the server's own count is the only one to trust.
        try {
          const upload = await this.api.status(id);
          this.set({ received: upload.received, phase: "uploading" });
          if (upload.complete) {
            this.set({ phase: "done", format: upload.format ?? null });
            return;
          }
        } catch (statusError: unknown) {
          if (!isTransient(statusError)) throw statusError;
          // Still no answer: the next turn of the loop sends again and counts this as a retry.
        }
      }
    }
  }
}

/** The file chosen again is not the one the server holds an upload of. */
export class UploadMismatchError extends Error {
  readonly heldName: string;
  readonly heldSize: number;

  constructor(heldName: string, heldSize: number) {
    super("The file is not the one that was being uploaded");
    this.name = "UploadMismatchError";
    this.heldName = heldName;
    this.heldSize = heldSize;
  }
}
