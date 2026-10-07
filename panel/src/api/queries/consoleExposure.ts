/**
 * The console on a public name over TLS: whether it is published and where, what publishing
 * made (so unpublishing removes exactly that), whether the ENS profile allows it, and what the
 * checks say before anything is written. Publishing and unpublishing are jobs that ask for
 * sudo mode; the console restarts a few seconds after the job ends.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type ExposureBody = BodyOf<"/api/console/exposure", "post">;
export type ExposurePlan = ResponseOf<"/api/console/exposure/preview", "post">;

/** What `GET /api/console/exposure` says; the route answers a plain dictionary, read here with care. */
export interface ConsoleExposure {
  /** The console answers on a public name now. */
  exposed: boolean;
  /** The ENS profile lets it be published (it keeps it on loopback unless forced). */
  ensAllows: boolean;
  /** What the record says exists and does not: a site someone removed by hand. */
  problems: string[];
  domain: string | null;
  url: string | null;
  port: number | null;
  allowIps: string[];
  siteCreated: boolean;
  certificateCreated: boolean;
  proxyAdded: string | null;
  /** `active`, or `pending` for an attempt that did not finish. */
  state: string | null;
  createdAt: string | null;
  createdBy: string | null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** Reads the dictionary the route answers with into what the page needs. */
export function readExposure(raw: unknown): ConsoleExposure {
  const record = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  return {
    exposed: record["exposed"] === true,
    ensAllows: record["ens_allows"] !== false,
    problems: texts(record["problems"]),
    domain: text(record["domain"]),
    url: text(record["url"]),
    port: typeof record["port"] === "number" ? record["port"] : null,
    allowIps: texts(record["allow_ips"]),
    siteCreated: record["site_created"] === true,
    certificateCreated: record["certificate_created"] === true,
    proxyAdded: text(record["proxy_added"]),
    state: text(record["state"]),
    createdAt: text(record["created_at"]),
    createdBy: text(record["created_by"]),
  };
}

export const exposureKeys = {
  all: ["console", "exposure"] as const,
};

/** Whether the console is published, and where. */
export const consoleExposureQuery = () =>
  queryOptions({
    queryKey: exposureKeys.all,
    queryFn: async ({ signal }) => readExposure(await request("get", "/api/console/exposure", { signal })),
  });
