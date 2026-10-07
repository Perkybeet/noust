/** The upload endpoints, as the uploader's `UploadApi`: the one place the dialog talks to them. */

import { api, buildPath, request } from "../../../api/client";
import type { DumpUpload } from "../../../api/queries/dumpImport";
import type { UploadApi } from "./uploader";

export const uploadApi: UploadApi = {
  create: (fileName, size) => request("post", "/api/databases/imports/uploads", { body: { file_name: fileName, size, sha256: null } }),
  // The chunk's bytes are the whole body, which the OpenAPI contract cannot say (the route reads
  // the raw request), so this goes through the transport itself; the answer is the contract's.
  append: (id, offset, chunk, signal) =>
    api<DumpUpload>("PUT", buildPath("/api/databases/imports/uploads/{upload_id}", { upload_id: id }, { offset }), chunk, { signal }),
  status: (id) => request("get", "/api/databases/imports/uploads/{upload_id}", { params: { upload_id: id } }),
  complete: (id) => request("post", "/api/databases/imports/uploads/{upload_id}/complete", { params: { upload_id: id } }),
  discard: async (id) => {
    await request("delete", "/api/databases/imports/uploads/{upload_id}", { params: { upload_id: id } });
  },
};
