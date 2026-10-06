import { describe, expect, it } from "vitest";

import { ApiError } from "../../../api/client";
import type { DumpUpload } from "../../../api/queries/dumpImport";
import { CHUNK_BYTES, DUMP_EXTENSIONS, DumpUploader, MAX_AUTOMATIC_RETRIES, UploadMismatchError, backoffMs, extensionOf, fileProblem, isTransient } from "./uploader";
import type { UploadApi, UploadSnapshot } from "./uploader";

const CHUNK = 4;
const FORMAT = { kind: "sql", compression: "gzip", description: "A SQL dump, compressed with gzip.", engines: ["postgresql", "mysql"] };

function file(name: string, bytes: number): File {
  return new File([new Uint8Array(bytes)], name);
}

function network(): ApiError {
  return new ApiError(0, "network", "Failed to fetch", "Check the connection.");
}

function offsetRefusal(received: number): ApiError {
  return new ApiError(409, "upload_offset", "The chunk starts at the wrong offset", "Send it from the offset the server reports.", null, null, null, null, { received });
}

/** The server's side of an upload, in memory: it holds `received`, refuses any other offset. */
class FakeServer {
  received = 0;
  complete = false;
  discarded = false;
  appends: number[] = [];
  statuses = 0;
  /** Failures to throw from the next calls, by what is called; undefined lets a call through. */
  failures: Partial<Record<"create" | "append" | "status" | "complete", (Error | undefined)[]>> = {};

  readonly name: string;
  readonly size: number;

  constructor(name: string, size: number) {
    this.name = name;
    this.size = size;
  }

  private fail(call: "create" | "append" | "status" | "complete"): void {
    const next = this.failures[call]?.shift();
    if (next !== undefined) throw next;
  }

  private upload(): DumpUpload {
    return {
      id: "up1",
      file_name: this.name,
      size: this.size,
      received: this.received,
      sha256: null,
      created_at: "2026-10-06T10:00:00+00:00",
      updated_at: "2026-10-06T10:00:00+00:00",
      complete: this.complete,
      chunk_size: 16 * 1024 ** 2,
      ...(this.complete ? { format: FORMAT } : {}),
    };
  }

  /** Answers after a turn of the event loop, the way a request does, and throws what a call is set to throw. */
  private answer<T>(make: () => T): Promise<T> {
    return Promise.resolve().then(make);
  }

  api(): UploadApi {
    return {
      create: () =>
        this.answer(() => {
          this.fail("create");
          return this.upload();
        }),
      append: (_id, offset, chunk) =>
        this.answer(() => {
          this.fail("append");
          this.appends.push(offset);
          if (offset !== this.received) throw offsetRefusal(this.received);
          this.received += chunk.size;
          return this.upload();
        }),
      status: () =>
        this.answer(() => {
          this.statuses += 1;
          this.fail("status");
          return this.upload();
        }),
      complete: () =>
        this.answer(() => {
          this.fail("complete");
          if (this.received < this.size) throw new ApiError(409, "upload_incomplete", "Not every byte is in", null, null, null, null, null, { received: this.received });
          this.complete = true;
          return this.upload();
        }),
      discard: () =>
        this.answer(() => {
          this.discarded = true;
        }),
    };
  }
}

/** An uploader that never waits, and records the waits it was asked for. */
function uploaderFor(server: FakeServer, waits: number[] = []): { uploader: DumpUploader; seen: UploadSnapshot[] } {
  const uploader = new DumpUploader(server.api(), {
    chunkBytes: CHUNK,
    sleep: (ms) => {
      waits.push(ms);
      return Promise.resolve();
    },
  });
  const seen: UploadSnapshot[] = [];
  uploader.subscribe((snapshot) => seen.push(snapshot));
  return { uploader, seen };
}

describe("which files can be uploaded", () => {
  it("are dumps by their extension, whatever the case, and no other", () => {
    expect(fileProblem({ name: "shop.sql.gz", size: 10 }, 100)).toBeNull();
    expect(fileProblem({ name: "SHOP.DUMP", size: 10 }, 100)).toBeNull();
    for (const extension of DUMP_EXTENSIONS) expect(fileProblem({ name: `a.${extension}`, size: 1 }, 100), extension).toBeNull();
    expect(fileProblem({ name: "notes.txt", size: 10 }, 100)).toBe("extension");
    expect(fileProblem({ name: "dump", size: 10 }, 100)).toBe("extension");
  });

  it("are not empty, and not past the size the server allows", () => {
    expect(fileProblem({ name: "a.sql", size: 0 }, 100)).toBe("empty");
    expect(fileProblem({ name: "a.sql", size: 101 }, 100)).toBe("tooLarge");
    expect(fileProblem({ name: "a.sql", size: 100 }, 100)).toBeNull();
  });

  it("reads the last extension only", () => {
    expect(extensionOf("a.tar.gz")).toBe("gz");
    expect(extensionOf("noext")).toBe("");
  });
});

describe("sending a dump in chunks", () => {
  it("sends every chunk at the offset the server reports, then completes and says what the file is", async () => {
    const server = new FakeServer("shop.sql.gz", 10);
    const { uploader, seen } = uploaderFor(server);
    await uploader.start(file("shop.sql.gz", 10));
    expect(server.appends).toEqual([0, 4, 8]);
    expect(uploader.snapshot).toMatchObject({ phase: "done", received: 10, size: 10, uploadId: "up1", format: FORMAT });
    // The progress only ever goes forward.
    const received = seen.map((snapshot) => snapshot.received);
    expect(received).toEqual([...received].sort((a, b) => a - b));
  });

  it("uses chunks the server will take", () => {
    expect(CHUNK_BYTES).toBeLessThanOrEqual(16 * 1024 ** 2);
  });

  it("goes on from the server's own count when the connection drops, waiting longer each time", async () => {
    const server = new FakeServer("a.sql", 12);
    server.failures.append = [undefined, network(), network()];
    const waits: number[] = [];
    const { uploader, seen } = uploaderFor(server, waits);
    await uploader.start(file("a.sql", 12));
    expect(uploader.snapshot.phase).toBe("done");
    expect(waits).toEqual([1_000, 2_000]);
    expect(server.statuses).toBe(2);
    expect(seen.some((snapshot) => snapshot.phase === "waiting" && snapshot.retryInMs === 1_000)).toBe(true);
    // Nothing is sent twice: the file ends where it should.
    expect(server.received).toBe(12);
  });

  it("is told where to resume when a chunk was written but its answer was lost", async () => {
    const server = new FakeServer("a.sql", 8);
    // The first chunk reaches the server, and the answer never reaches the browser.
    const api = server.api();
    let lost = true;
    const lossy: UploadApi = {
      ...api,
      append: async (id, offset, chunk, signal) => {
        const answer = await api.append(id, offset, chunk, signal);
        if (lost) {
          lost = false;
          throw network();
        }
        return answer;
      },
    };
    const uploader = new DumpUploader(lossy, { chunkBytes: CHUNK, sleep: () => Promise.resolve() });
    await uploader.start(file("a.sql", 8));
    expect(uploader.snapshot).toMatchObject({ phase: "done", received: 8 });
    expect(server.received).toBe(8);
  });

  it("takes the offset a 409 reports and continues from it", async () => {
    const server = new FakeServer("a.sql", 8);
    const api = server.api();
    // The upload opens at 0, and then the server holds 4 (another tab of the same browser sent it).
    const moved: UploadApi = {
      ...api,
      create: async (name, size) => {
        const opened = await api.create(name, size);
        server.received = 4;
        return opened;
      },
    };
    const uploader = new DumpUploader(moved, { chunkBytes: CHUNK, sleep: () => Promise.resolve() });
    await uploader.start(file("a.sql", 8));
    expect(server.appends).toEqual([0, 4]);
    expect(uploader.snapshot).toMatchObject({ phase: "done", received: 8 });
  });

  it("completes again when the server says bytes are missing", async () => {
    const server = new FakeServer("a.sql", 4);
    const api = server.api();
    let first = true;
    const forgetful: UploadApi = {
      ...api,
      complete: async (id) => {
        if (first) {
          first = false;
          server.received = 0;
        }
        return api.complete(id);
      },
    };
    const uploader = new DumpUploader(forgetful, { chunkBytes: CHUNK, sleep: () => Promise.resolve() });
    await uploader.start(file("a.sql", 4));
    expect(uploader.snapshot.phase).toBe("done");
  });

  it("waits for the operator after the automatic retries, then goes on when told to", async () => {
    const server = new FakeServer("a.sql", 8);
    server.failures.append = Array.from({ length: MAX_AUTOMATIC_RETRIES + 1 }, () => network());
    const waits: number[] = [];
    const { uploader } = uploaderFor(server, waits);
    await uploader.start(file("a.sql", 8));
    expect(uploader.snapshot).toMatchObject({ phase: "paused", received: 0 });
    expect(waits).toHaveLength(MAX_AUTOMATIC_RETRIES);
    expect(waits.at(-1)).toBe(30_000);

    uploader.retryNow();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(uploader.snapshot).toMatchObject({ phase: "done", received: 8 });
  });

  it("stops at what the server refuses for good, with its words", async () => {
    const server = new FakeServer("a.sql", 8);
    const refusal = new ApiError(400, "validation_error", "Not enough space for the upload", "Free space on that disk first.");
    server.failures.append = [refusal];
    const { uploader } = uploaderFor(server);
    await uploader.start(file("a.sql", 8));
    expect(uploader.snapshot.phase).toBe("failed");
    expect(uploader.snapshot.error).toBe(refusal);
  });

  it("stops when the server will not even open the upload", async () => {
    const server = new FakeServer("a.sql", 8);
    const refusal = new ApiError(400, "upload_too_large", "The upload is 8 bytes; the limit is 4", "Raise databases.import_max_bytes.");
    server.failures.create = [refusal];
    const { uploader } = uploaderFor(server);
    await uploader.start(file("a.sql", 8));
    expect(uploader.snapshot).toMatchObject({ phase: "failed", uploadId: null });
    expect(server.appends).toEqual([]);
  });

  it("is thrown away on the server when cancelled, and sends nothing more", async () => {
    const server = new FakeServer("a.sql", 12);
    const api = server.api();
    const held: { uploader: DumpUploader | null } = { uploader: null };
    const cancelling: UploadApi = {
      ...api,
      append: async (id, offset, chunk, signal) => {
        const answer = await api.append(id, offset, chunk, signal);
        if (offset === 4) await held.uploader?.cancel();
        return answer;
      },
    };
    const uploader = new DumpUploader(cancelling, { chunkBytes: CHUNK, sleep: () => Promise.resolve() });
    held.uploader = uploader;
    await uploader.start(file("a.sql", 12));
    expect(uploader.snapshot.phase).toBe("cancelled");
    expect(server.discarded).toBe(true);
    expect(server.appends).toEqual([0, 4]);
  });
});

describe("continuing an upload the server still holds", () => {
  it("goes on from where the server got to, with the same file chosen again", async () => {
    const server = new FakeServer("a.sql", 12);
    server.received = 8;
    const { uploader } = uploaderFor(server);
    await uploader.continueUpload("up1", file("a.sql", 12));
    expect(server.appends).toEqual([8]);
    expect(uploader.snapshot).toMatchObject({ phase: "done", received: 12 });
  });

  it("drops what is held when the file is not the one that was being sent", async () => {
    const server = new FakeServer("a.sql", 12);
    server.received = 8;
    const { uploader } = uploaderFor(server);
    await uploader.continueUpload("up1", file("other.sql", 20));
    expect(uploader.snapshot.phase).toBe("failed");
    expect(uploader.snapshot.error).toBeInstanceOf(UploadMismatchError);
    expect(server.discarded).toBe(true);
  });

  it("finishes at once an upload that already has every byte", async () => {
    const server = new FakeServer("a.sql", 4);
    server.received = 4;
    server.complete = true;
    const { uploader } = uploaderFor(server);
    await uploader.continueUpload("up1", file("a.sql", 4));
    expect(uploader.snapshot).toMatchObject({ phase: "done", format: FORMAT });
    expect(server.appends).toEqual([]);
  });
});

describe("what counts as the connection's fault", () => {
  it("is no answer or a proxy's, never a refusal of the request", () => {
    expect(isTransient(network())).toBe(true);
    expect(isTransient(new ApiError(502, "http_error", "Bad gateway"))).toBe(true);
    expect(isTransient(new ApiError(503, "http_error", "Unavailable"))).toBe(true);
    expect(isTransient(new ApiError(400, "validation_error", "No"))).toBe(false);
    expect(isTransient(new ApiError(403, "forbidden", "No"))).toBe(false);
    expect(isTransient(new Error("boom"))).toBe(false);
  });

  it("waits one second, then doubles up to half a minute", () => {
    expect([0, 1, 2, 3, 4, 5, 6].map(backoffMs)).toEqual([1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000]);
  });
});
