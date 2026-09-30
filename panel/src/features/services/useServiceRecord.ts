import { useQuery } from "@tanstack/react-query";

import { isApiError } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { serviceQuery, servicesQuery } from "../../api/queries/services";
import type { ServiceInfo } from "./data";
import { appOfUnit } from "./data";

export interface ServiceRecord {
  /** The unit, as the per-name read or the all-units listing answered it. */
  service: ServiceInfo | undefined;
  /** It runs on this machine but Noust did not create it: read only. */
  foreign: boolean;
  /** Neither read knows it. */
  notFound: boolean;
  /** Still reading. */
  loading: boolean;
  /** The per-name read failed for a reason other than "not Noust's". */
  error: unknown;
  refetch: () => void;
  refetching: boolean;
  /** The application the unit runs, when it runs one. */
  app: string | null;
  /** Whether the applications are still being read, so `app` is not known yet. */
  appPending: boolean;
}

/**
 * One unit, whoever created it. `GET /api/services/{name}` answers only what the store tracks
 * and 404s for a unit Noust did not create; the all-units listing is the only way to tell such a
 * unit from one that does not exist, so it is read only once the plain read has 404d.
 */
export function useServiceRecord(name: string): ServiceRecord {
  const detail = useQuery(serviceQuery(name));
  const missing = detail.isError && isApiError(detail.error) && detail.error.status === 404;
  const everything = useQuery({ ...servicesQuery(false), enabled: missing });
  const apps = useQuery(appsQuery());
  const foreignUnit = missing ? everything.data?.services.find((candidate) => candidate.name === name) : undefined;
  const service = detail.data ?? foreignUnit;
  return {
    service,
    foreign: foreignUnit !== undefined,
    notFound: missing && everything.data !== undefined && foreignUnit === undefined,
    loading: service === undefined && (detail.isPending || (missing && everything.isPending)),
    error: detail.isError && !missing ? detail.error : null,
    refetch: () => void detail.refetch(),
    refetching: detail.isRefetching,
    app: appOfUnit(apps.data?.apps ?? [], name)?.domain ?? null,
    appPending: apps.isPending,
  };
}
