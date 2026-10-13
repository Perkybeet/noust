import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import { appLogsQuery } from "../../../api/queries/apps";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { LogViewer } from "../../../components/ui/LogViewer";
import type { LogLine } from "../../../components/ui/LogViewer";
import { Select } from "../../../components/ui/Select";
import type { SelectOption } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { withLevel } from "./journalLines";

/** Lines read from the containers each time. */
const LINES = 500;

/** How often the output is read again while the tab is in view. */
const REFRESH_MS = 5_000;

/** What the API says when the containers have printed nothing; there is no line in it. */
const NOTHING_PRINTED = "No logs available";

const ALL = "all";
const UNIT = "unit";
const SERVICE = "service:";

/**
 * Whether an application runs as a Docker Compose project, which its unit only starts: the types
 * the server judges by their containers (`COMPOSE_BACKED_TYPES`).
 */
export function isStack(appType: string): boolean {
  return appType === "docker-compose" || appType === "container";
}

function linesOf(text: string): LogLine[] {
  if (text === NOTHING_PRINTED) return [];
  return text
    .split("\n")
    .filter((line) => line !== "")
    .map((line, index) => withLevel({ id: index + 1, text: line }));
}

/**
 * The Logs tab of a Docker Compose stack or a container application. The unit only starts the
 * containers once and says nothing of the application, so what the containers printed is what the
 * tab shows, from every service or from one of them; the unit's own journal (what Noust ran) is the
 * last choice, passed in as `unit`.
 *
 * Compose has no stream the console can follow without holding a connection to Docker open for
 * every viewer, so the tab reads the last lines again every few seconds while it is in view, and
 * "Refresh" reads them now.
 */
export function StackLogs({ domain, unit }: { domain: string; unit: ReactNode }) {
  const t = useT();
  const [onUnit, setOnUnit] = useState(false);
  const [service, setService] = useState<string | null>(null);

  // The query keeps its answer while the unit's journal is shown, so the list of services is still
  // there to choose from when the operator comes back.
  const query = useQuery({
    ...appLogsQuery(domain, { lines: LINES, ...(service !== null ? { service } : {}) }),
    enabled: !onUnit,
    refetchInterval: onUnit ? false : REFRESH_MS,
    placeholderData: keepPreviousData,
  });
  const lines = useMemo(() => linesOf(query.data?.logs ?? ""), [query.data?.logs]);

  const options: readonly SelectOption[] = [
    { value: ALL, label: t("appPages.logs.sourceAll") },
    ...(query.data?.services ?? []).map((name) => ({ value: `${SERVICE}${name}`, label: name })),
    { value: UNIT, label: t("appPages.logs.sourceUnit") },
  ];
  const choose = (value: string): void => {
    setOnUnit(value === UNIT);
    if (value !== UNIT) setService(value === ALL ? null : value.slice(SERVICE.length));
  };

  return (
    <div className="flex flex-col gap-3">
      <div>
        <Select
          label={t("appPages.logs.sourceLabel")}
          options={options}
          value={onUnit ? UNIT : service === null ? ALL : `${SERVICE}${service}`}
          onValueChange={choose}
          size="sm"
        />
      </div>
      {onUnit ? (
        unit
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <p className="text-12 text-fg-muted">{t("appPages.logs.containersNote")}</p>
            <Button
              size="sm"
              variant="ghost"
              icon={<RefreshCw aria-hidden="true" />}
              loading={query.isFetching}
              onClick={() => void query.refetch()}
            >
              {t("appPages.logs.refresh")}
            </Button>
          </div>
          {query.isError ? (
            <ErrorBlock
              live
              compact
              error={query.error}
              title={t("appPages.logs.containersFailed")}
              hint={t("appPages.logs.containersFailedHint")}
            />
          ) : null}
          <div className="h-editor">
            <LogViewer
              lines={lines}
              height="fill"
              pageSearch
              loading={query.isPending}
              label={t("appPages.logs.journalLabel", { domain })}
              filename={`${domain}${service !== null ? `-${service}` : ""}.log`}
              emptyMessage={query.isPending ? t("appPages.logs.readingContainers") : t("appPages.logs.emptyContainers")}
            />
          </div>
          <CommandHint command={`noust logs ${domain} --follow`} label={t("appPages.fromTerminal")} />
        </div>
      )}
    </div>
  );
}
