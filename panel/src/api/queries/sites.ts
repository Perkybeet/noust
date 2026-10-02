import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { ResponseOf } from "../client";

export type SiteList = ResponseOf<"/api/sites", "get">;
export type Site = ResponseOf<"/api/sites/{domain}", "get">;
/** One entry of the machine's list of sites. */
export type SiteEntry = SiteList["sites"][number];
export type SiteConfig = ResponseOf<"/api/sites/{domain}/config", "get">;
export type SiteTemplates = ResponseOf<"/api/sites/templates", "get">;

export const siteKeys = {
  all: ["sites"] as const,
  /** Every single site's own entry: the prefix of `detail` and `config`. */
  details: ["site"] as const,
  detail: (domain: string) => ["site", domain] as const,
  config: (domain: string) => ["site", domain, "config"] as const,
  templates: ["sites", "templates"] as const,
  /** The model of a text of the site: the saved file when `config` is null, else a draft. */
  structure: (domain: string, config: string | null) => ["site", domain, "structure", config ?? { saved: true }] as const,
  topology: (domain: string) => ["site", domain, "topology"] as const,
};

export const sitesQuery = () =>
  queryOptions({
    queryKey: siteKeys.all,
    queryFn: ({ signal }) => request("get", "/api/sites", { signal }),
  });

export const siteQuery = (domain: string) =>
  queryOptions({
    queryKey: siteKeys.detail(domain),
    queryFn: ({ signal }) => request("get", "/api/sites/{domain}", { params: { domain }, signal }),
  });

export const siteConfigQuery = (domain: string) =>
  queryOptions({
    queryKey: siteKeys.config(domain),
    queryFn: ({ signal }) => request("get", "/api/sites/{domain}/config", { params: { domain }, signal }),
  });

/** Templates a site can be created from, for the detected web server. */
export const siteTemplatesQuery = () =>
  queryOptions({
    queryKey: siteKeys.templates,
    queryFn: ({ signal }) => request("get", "/api/sites/templates", { signal }),
  });

/** A site file analysed: its model, or why it does not parse (`GET`/`POST /structure`). */
export type SiteStructureResponse = ResponseOf<"/api/sites/{domain}/structure", "get">;
/** The model of a site file: servers, locations, upstreams, includes, raw directives. */
export type SiteStructure = NonNullable<SiteStructureResponse["structure"]>;
export type SiteServer = SiteStructure["servers"][number];
export type SiteLocation = SiteServer["locations"][number];
export type SiteUpstream = SiteStructure["upstreams"][number];
export type SiteRawDirective = SiteStructure["directives"][number];
export type SiteNote = SiteStructure["notes"][number];
export type SiteParseFailure = NonNullable<SiteStructureResponse["error"]>;
/** The saved site's model with what is behind it now: owners, probes, certificates. */
export type SiteTopology = ResponseOf<"/api/sites/{domain}/topology", "get">;
export type SiteBackend = SiteTopology["backends"][number];
export type SiteCertificate = SiteTopology["certificates"][number];
/** Which server and location answer a request, and why (`POST /route`). */
export type SiteRoute = ResponseOf<"/api/sites/{domain}/route", "post">;
export type SiteRouteStep = SiteRoute["trace"][number];
/** The answer of `POST /config/edit`: the edited text, its model and the lines it changed. */
export type SiteEdit = ResponseOf<"/api/sites/{domain}/config/edit", "post">;

/**
 * One edit operation of `POST /config/edit` (noust.managers.siteconf.edit): `op` names it, the
 * other keys are its fields. Ids are those of the text the operation is applied to.
 */
export type SiteEditOp =
  | { op: "set_directive"; target: string; args: string[] }
  | { op: "set_directive"; parent: string; name: string; args: string[] }
  | { op: "add_directive"; parent: string; name: string; args: string[]; before?: string; after?: string }
  | { op: "remove_directive"; target: string }
  | { op: "remove_directive"; parent: string; name: string }
  | {
      op: "add_block";
      parent: string;
      name: string;
      args: string[];
      template: "proxy" | "static" | "redirect";
      to: string;
      code?: number;
      before?: string;
      after?: string;
    }
  | { op: "remove_block"; target: string }
  | { op: "duplicate_block"; target: string; args?: string[] }
  | { op: "move_block"; target: string; before: string }
  | { op: "move_block"; target: string; after: string };

/**
 * The structure of a site's text: the saved file (`config` null) or a draft. Keyed by the text
 * itself, so every view of the same draft shares one answer, and an edit that already returned
 * the model seeds it (`siteKeys.structure`).
 */
export const siteStructureQuery = (domain: string, config: string | null) =>
  queryOptions({
    queryKey: siteKeys.structure(domain, config),
    queryFn: ({ signal }) =>
      config === null
        ? request("get", "/api/sites/{domain}/structure", { params: { domain }, signal })
        : request("post", "/api/sites/{domain}/structure", { params: { domain }, body: { config }, signal }),
    // A text's model never changes; only the text does, and that is another key.
    staleTime: config === null ? 30_000 : Number.POSITIVE_INFINITY,
    gcTime: 5 * 60_000,
    retry: false,
  });

/** The saved site and what is behind it now: who holds each port, whether it answers. */
export const siteTopologyQuery = (domain: string) =>
  queryOptions({
    queryKey: siteKeys.topology(domain),
    queryFn: ({ signal }) => request("get", "/api/sites/{domain}/topology", { params: { domain }, signal }),
    // The backend caches its probes for ten seconds; asking more often learns nothing.
    refetchInterval: 15_000,
    retry: false,
  });
