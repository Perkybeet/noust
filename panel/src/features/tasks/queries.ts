import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import { runOnNode } from "../../api/nodeScope";
import type { ServerKey } from "./model";

/** How many of a job's last lines the panel shows when a row's log is asked for. */
export const LOG_TAIL = 6;

/** How often an open log of a running task is read again. */
export const LOG_POLL_MS = 3_000;

/**
 * The last lines of a job's captured log, read from the server the task is on: on "All servers" a row belongs to
 * a server that is not the one the console is talking to. The key names the server, so two servers' entries never meet.
 */
export const taskLogTailQuery = (server: ServerKey, id: string) =>
  queryOptions({
    queryKey: ["tasks", "log", server, id, LOG_TAIL] as const,
    queryFn: ({ signal }) => runOnNode(server, () => request("get", "/api/jobs/{job_id}/log", { params: { job_id: id }, query: { tail: LOG_TAIL }, signal })),
  });
