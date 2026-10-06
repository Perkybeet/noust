/**
 * A database made reachable from the network, on purpose: what the server says of one
 * (`GET /api/databases/public/{engine}/{database}`), the plan that says what opening it would
 * do and what the operator accepts by going ahead, and the list of every public database.
 * Opening, showing the connection again and closing are mutations beside the page that calls
 * them (they ask for sudo mode).
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type PublicDetail = ResponseOf<"/api/databases/public/{engine}/{database}", "get">;
export type PublicExposure = NonNullable<PublicDetail["exposure"]>;
export type PublicConnection = NonNullable<PublicDetail["connection"]>;
export type PublicList = ResponseOf<"/api/databases/public", "get">;
export type PublicPlan = ResponseOf<"/api/databases/public/plan", "post">;
export type PublicBody = BodyOf<"/api/databases/public", "post">;
export type PublicOpened = ResponseOf<"/api/databases/public", "post">;
export type PublicClosed = ResponseOf<"/api/databases/public/{engine}/{database}", "delete">;

export const publicKeys = {
  all: ["databases", "public"] as const,
  list: ["databases", "public", "list"] as const,
  detail: (engine: string, database: string) => ["databases", "public", "detail", engine, database] as const,
};

/** Whether one database is public, and how it is reached with the password masked. */
export const publicDetailQuery = (engine: string, database: string) =>
  queryOptions({
    queryKey: publicKeys.detail(engine, database),
    queryFn: ({ signal }) => request("get", "/api/databases/public/{engine}/{database}", { params: { engine, database }, signal }),
  });

/** Every database reachable from the network on this server. */
export const publicListQuery = () =>
  queryOptions({
    queryKey: publicKeys.list,
    queryFn: ({ signal }) => request("get", "/api/databases/public", { signal }),
  });
