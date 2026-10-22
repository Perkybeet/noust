import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList, KeyValueListSkeleton } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { formatBytes } from "../../lib/format";
import type { ServiceInfo } from "./data";
import { useServiceRecord } from "./useServiceRecord";
import type { ServiceRecord } from "./useServiceRecord";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/** A service's facts: what it runs, its process, its memory, since when, and whether it starts at boot. */
export function ServiceOverviewTab({ name }: { name: string }) {
  const t = useT();
  const record = useServiceRecord(name);
  // The page above says why a service cannot be read (not found, a failed read), so only the
  // wait is told here.
  const query = { data: record.service, error: null, isPending: record.loading, isError: false };
  return (
    <QueryState
      query={query}
      label={name}
      skeleton={
        <Card level={2} title={t("services.detail.factsTitle")} icon={OBJECT_ICONS.service} padding="sm">
          <KeyValueListSkeleton rows={5} />
        </Card>
      }
    >
      {(data) => <ServiceFacts name={name} data={data} record={record} />}
    </QueryState>
  );
}

function ServiceFacts({ name, data, record }: { name: string; data: ServiceInfo; record: ServiceRecord }) {
  const t = useT();
  const items: KeyValueItem[] = [
    { label: t("services.detail.commandLabel"), value: data.description ?? null },
    { label: t("services.detail.mainPid"), value: data.active && data.pid ? data.pid : null },
    { label: t("services.detail.memoryLabel"), value: data.memory ? formatBytes(Number(data.memory), t.locale) : null, mono: false, copy: false },
    {
      label: t("services.detail.sinceLabel"),
      value: data.active && data.uptime ? <RelativeTime value={data.uptime} /> : t("services.table.notRunning"),
      mono: false,
      copy: false,
    },
    {
      label: t("services.detail.startsAtBoot"),
      // An on/off, told at a glance (item 56).
      value: <StatusPill state={data.enabled ? "running" : "stopped"} label={data.enabled ? t("services.detail.yes") : t("services.detail.no")} size="sm" />,
      mono: false,
      copy: false,
    },
    {
      label: t("services.detail.managedBy"),
      value: record.foreign ? t("services.detail.managedByOther") : record.app !== null ? t("services.detail.managedByApp", { domain: record.app }) : "Noust",
      mono: false,
      copy: false,
    },
  ];
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <Card level={2} title={t("services.detail.factsTitle")} icon={OBJECT_ICONS.service} padding="sm">
        <KeyValueList items={items} />
      </Card>
      <CommandHint command={record.foreign ? `systemctl status ${name}` : `noust service status ${name}`} label={t("services.fromTerminal")} />
    </div>
  );
}
