/**
 * Importing a dump from the browser: an upload in chunks that can be resumed at the offset the
 * server reports, what the finished file is, whether an engine can load it, and the size the
 * server allows (`databases.import_max_bytes`, read from the configuration). The import itself
 * is a job the dialog starts (it asks for sudo mode like every restore).
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type DumpUpload = ResponseOf<"/api/databases/imports/uploads", "post">;
export type DumpFormat = NonNullable<DumpUpload["format"]>;
export type DumpInspection = ResponseOf<"/api/databases/imports/inspect", "post">;
export type DumpImportBody = BodyOf<"/api/databases/imports", "post">;

/** What the server allows when it is not asked: 10 GiB (`databases.import_max_bytes`). */
export const DEFAULT_IMPORT_MAX_BYTES = 10 * 1024 ** 3;

export const importKeys = {
  limit: ["databases", "imports", "limit"] as const,
  inspect: (uploadId: string, engine: string) => ["databases", "imports", "inspect", uploadId, engine] as const,
};

/**
 * The largest dump the server takes by upload. It is a setting (`databases.import_max_bytes`),
 * so it is read from the configuration; an account that cannot read the configuration, or a
 * server that does not say, gets the default, and the server refuses a size past its own limit
 * with a message that says what it is.
 */
export const importLimitQuery = () =>
  queryOptions({
    queryKey: importKeys.limit,
    queryFn: async ({ signal }): Promise<number> => {
      const answer = await request("get", "/api/config", { signal });
      const databases = (answer.config as Record<string, unknown> | undefined)?.["databases"];
      const value = typeof databases === "object" && databases !== null ? (databases as Record<string, unknown>)["import_max_bytes"] : undefined;
      return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : DEFAULT_IMPORT_MAX_BYTES;
    },
    staleTime: 60_000,
    retry: false,
  });

/** What a finished upload is and whether an engine can load it. Changes nothing. */
export const dumpInspectionQuery = (uploadId: string, engine: string) =>
  queryOptions({
    queryKey: importKeys.inspect(uploadId, engine),
    queryFn: ({ signal }) => request("post", "/api/databases/imports/inspect", { body: { upload_id: uploadId, engine }, signal }),
    staleTime: Infinity,
  });
