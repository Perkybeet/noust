import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { QueryOf, ResponseOf } from "../client";

/** A range read: one regular grid over exactly the requested domain, for several metrics. */
export type MetricsRead = ResponseOf<"/api/metrics/query", "get">;
export type MetricsReadSeries = MetricsRead["series"][number];
/** Whether history is being recorded, and why not, as every metrics answer carries it. */
export type MetricsCollector = MetricsRead["collector"];
/** Why something is not measured: a stable code, the fix and the system's own words. */
export type MetricsReason = NonNullable<MetricsCollector["reason"]>;

/** The named windows a range read accepts. */
export type MetricWindow = NonNullable<QueryOf<"/api/metrics/query", "get">["window"]>;

/** Why an application's metrics are, or are not, there. */
export type AppMetricsStatus = ResponseOf<"/api/apps/{domain}/metrics", "get">;

/** The collector's newest sample, metric name to value, as the `metrics` event carries it. */
export type MetricsSnapshot = Record<string, number>;

/** What a range read asks for: a named window ending now, or an explicit stretch (a zoom). */
export type MetricsSpan = { window: MetricWindow } | { from: number; to: number };

export const metricKeys = {
  all: ["metrics"] as const,
  /** Fed only by the `metrics` server event; there is no REST read of the live sample. */
  latest: ["metrics", "latest"] as const,
  catalogue: ["metrics", "catalogue"] as const,
  read: (metrics: readonly string[], span: MetricsSpan) => ["metrics", "read", [...metrics], span] as const,
  app: (domain: string) => ["metrics", "app", domain] as const,
};

export const metricsCatalogueQuery = () =>
  queryOptions({
    queryKey: metricKeys.catalogue,
    queryFn: ({ signal }) => request("get", "/api/metrics", { signal }),
  });

/**
 * Several metrics over a window or a stretch, as one grid the charts share: every series has the
 * same timestamps, so a crosshair index means the same moment in every chart of a page.
 */
export const metricsReadQuery = (metrics: readonly string[], span: MetricsSpan) =>
  queryOptions({
    queryKey: metricKeys.read(metrics, span),
    queryFn: ({ signal }) =>
      request("get", "/api/metrics/query", {
        query: "window" in span ? { metric: [...metrics], window: span.window } : { metric: [...metrics], from: span.from, to: span.to },
        signal,
      }),
  });

/** Whether an application's CPU and memory are being recorded and, when not, why and how to fix it. */
export const appMetricsStatusQuery = (domain: string) =>
  queryOptions({
    queryKey: metricKeys.app(domain),
    queryFn: ({ signal }) => request("get", "/api/apps/{domain}/metrics", { params: { domain }, signal }),
  });
