import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { adoptionQuery } from "../../api/queries/adoption";
import type { SiteAdoption } from "../../api/queries/adoption";
import type { App } from "../../api/queries/apps";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList, KeyValueListSkeleton } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Card } from "../../components/ui/Card";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import type { T } from "../../i18n";

function facts(adoption: SiteAdoption, t: T): KeyValueItem[] {
  const prose = { mono: false, copy: false } as const;
  return [
    { label: t("runtime.adopted.type"), value: t(`runtime.adopted.kind.${adoption.kind}`), ...prose },
    { label: t("runtime.adopted.site"), value: `${adoption.webserver} ${adoption.site_file}`, hint: t("runtime.adopted.siteHint") },
    ...(adoption.root !== null ? [{ label: t("runtime.adopted.directory"), value: adoption.root, hint: t("runtime.adopted.directoryHint") }] : []),
    adoption.unit !== null
      ? { label: t("runtime.adopted.unit"), value: `${adoption.unit}.service`, hint: t("runtime.adopted.unitHint") }
      : { label: t("runtime.adopted.unit"), value: t("runtime.adopted.noUnit"), ...prose },
    ...(adoption.upstream !== null ? [{ label: t("runtime.adopted.backend"), value: adoption.upstream }] : []),
    ...(adoption.adopted_by !== null && adoption.adopted_by !== "" ? [{ label: t("runtime.adopted.adoptedBy"), value: adoption.adopted_by, ...prose }] : []),
    ...(adoption.adopted_at !== null ? [{ label: t("runtime.adopted.adoptedAt"), value: <RelativeTime value={adoption.adopted_at} />, copy: false as const, mono: false }] : []),
  ];
}

/**
 * The overview of a site Noust adopted rather than built (checklist B-85): what it is, in the
 * operator's own terms (their unit, their site, their directory), and the one thing Noust says
 * about all of it, that it shows, restarts and reads and never builds or rewrites. There is no
 * deployment to show and nothing to roll back, so this is all of the overview but the resources
 * of the unit, which come from the caller (a site served by the web server alone has none).
 */
export function AdoptedOverview({ app, resources }: { app: App; resources: ReactNode }) {
  const t = useT();
  const adoption = useQuery(adoptionQuery(app.domain));
  return (
    <div className="flex flex-col gap-8">
      <Notice title={t("runtime.adopted.title")}>{t("runtime.adopted.description")}</Notice>
      {resources}
      <Section title={t("runtime.adopted.what")}>
        <Card padding="sm">
          {adoption.isPending ? (
            <KeyValueListSkeleton rows={5} />
          ) : adoption.isError ? (
            <ErrorBlock compact error={adoption.error} title={t("runtime.adopted.loadFailed")} onRetry={() => void adoption.refetch()} retrying={adoption.isRefetching} />
          ) : (
            <KeyValueList items={facts(adoption.data, t)} />
          )}
        </Card>
      </Section>
      <CommandHint command={`noust status ${app.domain}`} label={t("appPages.fromTerminal")} />
    </div>
  );
}
