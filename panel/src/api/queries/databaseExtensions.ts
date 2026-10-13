/**
 * The PostgreSQL extensions of a database (PostGIS, pgvector...): what the server offers, and which
 * are loaded. Loading and unloading are mutations beside the card that calls them.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type ExtensionList = ResponseOf<"/api/databases/databases/{engine}/{name}/extensions", "get">;
export type DatabaseExtension = NonNullable<ExtensionList["extensions"]>[number];

export const extensionKeys = {
  list: (engine: string, name: string) => ["databases", "db", engine, name, "extensions"] as const,
};

/** Everything the server offers for a database, with what is loaded in it. */
export const extensionsQuery = (engine: string, name: string) =>
  queryOptions({
    queryKey: extensionKeys.list(engine, name),
    queryFn: ({ signal }) => request("get", "/api/databases/databases/{engine}/{name}/extensions", { params: { engine, name }, signal }),
  });
