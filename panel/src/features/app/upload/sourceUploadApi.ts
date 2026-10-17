/**
 * The endpoints of an archive upload (`/api/uploads`), as the chunked uploader's `UploadApi`.
 *
 * The protocol is the database dumps' (the server holds the bytes it has and says where to resume,
 * a chunk at any other offset is refused with the offset), so the dumps' `DumpUploader` drives it
 * and only the paths and the chunk size differ. A chunk is half the default request limit, so no
 * limit had to be raised for it.
 */

import { api, buildPath, request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import type { UploadApi } from "../../databases/import/uploader";

export type SourceUpload = ResponseOf<"/api/uploads", "post">;

/** The most one chunk carries (`noust.web.api.source_uploads.CHUNK_BYTES`). */
export const ARCHIVE_CHUNK_BYTES = 512 * 1024;

/** What an archive's name may end in (`noust.managers.source_manager.detect_archive_format`). */
export const ARCHIVE_ENDINGS: readonly string[] = [".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz"];

/** Whether a file is named like an archive Noust extracts: judged before a byte moves. */
export function isArchiveName(name: string): boolean {
  const lowered = name.toLowerCase();
  return ARCHIVE_ENDINGS.some((ending) => lowered.endsWith(ending));
}

/**
 * The calls for one upload, and a way to read what finishing it answered (the `source` an
 * application is set to), which the uploader's own state does not carry.
 */
export function makeSourceUploadApi(): { api: UploadApi; finished: () => SourceUpload | null } {
  let last: SourceUpload | null = null;
  return {
    api: {
      create: (fileName, size) => request("post", "/api/uploads", { body: { file_name: fileName, size, sha256: null } }),
      // The chunk's bytes are the whole body, which the OpenAPI contract cannot say (the route
      // reads the raw request), so this goes through the transport itself; the answer is the contract's.
      append: (id, offset, chunk, signal) => api<SourceUpload>("PUT", buildPath("/api/uploads/{upload_id}", { upload_id: id }, { offset }), chunk, { signal }),
      status: (id) => request("get", "/api/uploads/{upload_id}", { params: { upload_id: id } }),
      complete: async (id) => {
        last = await request("post", "/api/uploads/{upload_id}/complete", { params: { upload_id: id } });
        return last;
      },
      discard: async (id) => {
        await request("delete", "/api/uploads/{upload_id}", { params: { upload_id: id } });
      },
    },
    finished: () => last,
  };
}
