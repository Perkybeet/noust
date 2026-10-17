/**
 * How this server fits together, read from it (checklist M-69): names, applications, databases,
 * scheduled work and the data an application keeps, in fixed layers with what connects them. The
 * server draws no positions; the console lays the layers out the way the site diagram does.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type MachineMap = ResponseOf<"/api/map", "get">;
export type MapNode = MachineMap["layers"][number]["nodes"][number];
export type MapEdge = MachineMap["edges"][number];

export const mapKeys = {
  all: ["map"] as const,
};

export const mapQuery = () =>
  queryOptions({
    queryKey: mapKeys.all,
    queryFn: ({ signal }) => request("get", "/api/map", { signal }),
  });
