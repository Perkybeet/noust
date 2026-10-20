import { useQuery } from "@tanstack/react-query";

import { replicationQuery } from "../../../api/queries/replication";
import type { ReplicationFinding } from "../../../api/queries/replication";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Card } from "../../../components/ui/Card";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { LoadingRegion } from "../../../components/page";

function Finding({ finding }: { finding: ReplicationFinding }) {
  const t = useT();
  const blocks = finding.severity === "blocks";
  return (
    <li className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        {/* A shape and a word as well as a colour: it is read without telling red from amber. */}
        <StatusPill state={blocks ? "failed" : "warning"} size="sm" label={blocks ? t("runtime.replication.blocks") : t("runtime.replication.warns")} />
        <span className="text-13 text-fg">{finding.message}</span>
      </div>
      {finding.items.length > 0 ? (
        <p className="flex flex-wrap gap-x-2 gap-y-1 text-12 text-fg-muted">
          {finding.items.map((item) => (
            <Mono key={item} tone="muted">
              {item}
            </Mono>
          ))}
        </p>
      ) : null}
      <p className="text-12 text-pretty text-fg-muted">{finding.fix}</p>
    </li>
  );
}

/**
 * Whether the app could run on more than one server, and what stops it (checklist B-24, B-69).
 * Spreading an app is the central's (Fleet > On several servers); what decides whether a second
 * server would serve the same thing is here, on the server it is on: the files it keeps, the
 * database it uses, the tasks it schedules. This reads those and places nothing.
 */
export function ReplicationCard({ domain }: { domain: string }) {
  const t = useT();
  const replication = useQuery(replicationQuery(domain));
  return (
    <Section title={t("runtime.replication.title")} description={t("runtime.replication.description")}>
      <Card>
        {replication.isPending ? (
          <LoadingRegion label={t("runtime.replication.loading")}>
            <Skeleton className="h-16 w-full" />
          </LoadingRegion>
        ) : replication.isError ? (
          <ErrorBlock compact error={replication.error} title={t("runtime.replication.loadFailed")} onRetry={() => void replication.refetch()} retrying={replication.isRefetching} />
        ) : (
          <div className="flex flex-col gap-4">
            <p className="text-13 text-fg">{replication.data.ready ? t("runtime.replication.ready") : t("runtime.replication.notReady")}</p>
            {replication.data.by_image ? <p className="text-12 text-fg-muted">{t("runtime.replication.byImage")}</p> : null}
            {replication.data.findings.length > 0 ? (
              <ul aria-label={t("runtime.replication.findings")} className="flex flex-col divide-y divide-border">
                {replication.data.findings.map((finding) => (
                  <Finding key={finding.code} finding={finding} />
                ))}
              </ul>
            ) : (
              <p className="text-12 text-fg-muted">{t("runtime.replication.nothing")}</p>
            )}
          </div>
        )}
      </Card>
    </Section>
  );
}
