import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { credentialKeys, interventionQuery, undoIntervention, undoPlanQuery } from "../../../api/queries/credentials";
import type { ActivityDetail, TimelineEntry, UndoItem } from "../../../api/queries/credentials";
import { useDocumentTitle } from "../../../app/documentTitle";
import { GuardedButton } from "../../../components/page/permissions";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Sections } from "../../../components/page/Section";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusGlyph } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { toast } from "../../../components/ui/toast";
import { hasMessage, useT } from "../../../i18n";
import type { T } from "../../../i18n";

const dynamic = (t: T): ((key: string) => string) => t as unknown as (key: string) => string;

function word(t: T, group: "kind" | "via" | "result", value: string): string {
  // What came of a call says "done" and "failed" as the whole console does.
  if (group === "result" && value === "ok") return t("common.words.done");
  if (group === "result" && value === "failed") return t("common.statusPill.failed");
  const key = `callerPolicy.activity.intervention.${group}.${value}`;
  return hasMessage(key) ? dynamic(t)(key) : value;
}

function timelineColumns(t: T): Column<TimelineEntry>[] {
  return [
    { id: "when", header: t("callerPolicy.activity.intervention.columns.when"), width: "w-36", sortValue: (entry) => entry.at, cell: (entry) => <RelativeTime value={entry.at} /> },
    {
      id: "what",
      header: t("callerPolicy.activity.intervention.columns.what"),
      card: "title",
      cell: (entry) => (
        <span className="flex min-w-0 flex-col">
          <span className="text-13 text-pretty">
            {word(t, "kind", entry.kind)} <span className="text-fg-muted">{`(${word(t, "via", entry.via)})`}</span>
          </span>
          <Mono tone="muted" truncate>
            {entry.tool ?? `${entry.method ?? ""} ${entry.path ?? ""}`.trim()}
          </Mono>
          {entry.operator !== null && entry.operator !== undefined ? <span className="text-12 text-fg-muted">{t("callerPolicy.activity.intervention.operator", { name: entry.operator })}</span> : null}
        </span>
      ),
    },
    { id: "application", header: t("callerPolicy.activity.intervention.columns.application"), hideBelow: "md", cell: (entry) => (entry.domain !== null && entry.domain !== undefined ? <Mono>{entry.domain}</Mono> : null) },
    { id: "result", header: t("callerPolicy.activity.intervention.columns.result"), card: "status", width: "w-32", cell: (entry) => <span className="text-13">{word(t, "result", entry.result)}</span> },
  ];
}

/** What leaving it as it was would do: the plan, application by application, with the confirmations a schema change asks for. */
function UndoDialog({ id, n, onClose }: { id: number; n: number; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const plan = useQuery(undoPlanQuery(id, n));
  const [confirmed, setConfirmed] = useState<ReadonlySet<string>>(new Set());
  const items: readonly UndoItem[] = plan.data?.items ?? [];
  const runnable = items.some((item) => item.status === "ready" || (item.status === "needs_confirmation" && confirmed.has(item.domain)));
  const start = useMutation({
    mutationFn: () => undoIntervention(id, n, { apps: null, schema_changed_ok: [...confirmed] }),
    onSuccess: (job) => {
      toast.info(t("callerPolicy.activity.undo.started"), { description: job.message });
      void queryClient.invalidateQueries({ queryKey: credentialKeys.all });
      onClose();
    },
  });
  return (
    <ConfirmDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      friction="simple"
      destructive={false}
      size="md"
      title={t("callerPolicy.activity.undo.dialogTitle")}
      description={t("callerPolicy.activity.undo.dialogDescription")}
      actionLabel={t("callerPolicy.activity.undo.confirm")}
      ready={runnable}
      onConfirm={async () => {
        await start.mutateAsync();
      }}
    >
      <QueryState query={plan} label={t("callerPolicy.activity.undo.loadingLabel")} skeleton={<SkeletonCard lines={3} />}>
        {(data) => (
          <div className="flex flex-col gap-4">
            {data.items.length === 0 ? <EmptyState variant="inline" title={t("callerPolicy.activity.undo.none")} /> : null}
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {data.items.map((item) => (
                <li key={item.domain} className="flex flex-col gap-1.5">
                  <p className="flex flex-wrap items-center gap-2 text-13 text-fg">
                    <StatusGlyph state={item.status === "ready" ? "running" : item.status === "blocked" ? "failed" : item.status === "already" ? "stopped" : "warning"} />
                    <Mono>{item.domain}</Mono>
                    <span className="text-fg-muted">{t(`callerPolicy.activity.undo.status.${item.status === "needs_confirmation" || item.status === "already" || item.status === "blocked" ? item.status : "ready"}`)}</span>
                  </p>
                  {item.back_to_release !== null && item.back_to_release !== undefined ? <p className="pl-6 text-12 text-fg-muted">{t("callerPolicy.activity.undo.backTo", { release: item.back_to_release })}</p> : null}
                  {item.status === "blocked" && item.reason !== null && item.reason !== undefined ? (
                    <p className="pl-6 text-12 text-pretty text-fg-muted">{hasMessage(`callerPolicy.activity.undo.reason.${item.reason}`) ? dynamic(t)(`callerPolicy.activity.undo.reason.${item.reason}`) : (item.detail ?? item.reason)}</p>
                  ) : null}
                  {item.status === "needs_confirmation" ? (
                    <div className="flex flex-col gap-1.5 pl-6">
                      <p className="text-12 text-pretty text-fg-muted">{t("callerPolicy.activity.undo.schemaChanged")}</p>
                      <Checkbox
                        label={t("callerPolicy.activity.undo.schemaConfirm", { domain: item.domain })}
                        checked={confirmed.has(item.domain)}
                        onCheckedChange={(checked) =>
                          setConfirmed((current) => {
                            const next = new Set(current);
                            if (checked) next.add(item.domain);
                            else next.delete(item.domain);
                            return next;
                          })
                        }
                      />
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
            {data.irreversible.length > 0 ? (
              <div className="flex flex-col gap-1">
                <p className="text-13 font-medium text-fg">{t("callerPolicy.activity.undo.irreversibleTitle")}</p>
                <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-13 text-pretty text-fg-muted">
                  {data.irreversible.map((entry, index) => (
                    <li key={`${entry.why}:${String(index)}`}>
                      <Mono>{`${entry.method} ${entry.path}`}</Mono> {hasMessage(`callerPolicy.activity.undo.irreversible.${entry.why}`) ? dynamic(t)(`callerPolicy.activity.undo.irreversible.${entry.why}`) : (entry.detail === "" ? entry.why : entry.detail)}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </QueryState>
    </ConfirmDialog>
  );
}

function Detail({ id, n, data }: { id: number; n: number; data: ActivityDetail }) {
  const t = useT();
  const [undoing, setUndoing] = useState(false);
  const title = t("callerPolicy.activity.intervention.title", { n });
  useDocumentTitle(title, 2);
  const intervention = data.intervention;
  return (
    <Sections>
      <SurfaceSection title={title} icon={OBJECT_ICONS.activity} description={t("callerPolicy.activity.intervention.summary", { calls: intervention.calls, changes: intervention.changes })}>
        <KeyValueList
          items={[
            { label: t("callerPolicy.activity.columns.when"), value: <RelativeTime value={intervention.started_at} />, mono: false, copy: false },
            { label: t("callerPolicy.activity.columns.proposed"), value: String(intervention.proposed), mono: false },
            { label: t("callerPolicy.activity.columns.refused"), value: String(intervention.refused), mono: false },
            { label: t("callerPolicy.activity.columns.failed"), value: String(intervention.failed), mono: false },
          ]}
        />
        <p>
          <TextLink to="/settings/tokens/$id" params={{ id: String(id) }} size="ui">
            {data.credential.kind === "assistant" ? t("callerPolicy.activity.assistantTitle", { owner: data.credential.owner ?? "" }) : t("callerPolicy.activity.title", { name: data.credential.name })}
          </TextLink>
        </p>
      </SurfaceSection>
      <SurfaceSection title={t("callerPolicy.activity.intervention.timelineTitle")} icon={OBJECT_ICONS.log} flush>
        <DataTable embedded caption={t("callerPolicy.activity.intervention.timelineCaption")} columns={timelineColumns(t)} rows={data.timeline} getRowId={(entry) => `${entry.at}:${String(entry.seq)}`} />
      </SurfaceSection>
      <SurfaceSection title={t("callerPolicy.activity.intervention.deploymentsTitle")} icon={OBJECT_ICONS.release}>
        {data.deployments.length === 0 ? (
          <EmptyState variant="inline" title={t("callerPolicy.activity.intervention.noDeployments")} />
        ) : (
          <ul aria-label={t("callerPolicy.activity.intervention.deploymentsCaption")} className="m-0 flex list-none flex-col gap-1.5 p-0 text-13">
            {data.deployments.map((deployment) => (
              <li key={deployment.id}>
                <TextLink to="/apps/$domain/deployments/$id" params={{ domain: deployment.domain, id: String(deployment.id) }} size="ui">
                  {`${deployment.domain} #${String(deployment.id)}`}
                </TextLink>
              </li>
            ))}
          </ul>
        )}
      </SurfaceSection>
      {data.undo.available ? (
        <SurfaceSection title={t("callerPolicy.activity.undo.title")} description={t("callerPolicy.activity.undo.description")} icon={OBJECT_ICONS.rollback}>
          <div>
            <GuardedButton permission="apps.deploy" variant="secondary" onClick={() => setUndoing(true)}>
              {t("callerPolicy.activity.undo.action")}
            </GuardedButton>
          </div>
          {data.undo.irreversible > 0 ? <InlineMessage tone="info">{t("callerPolicy.activity.undo.irreversibleTitle")}</InlineMessage> : null}
        </SurfaceSection>
      ) : null}
      {undoing ? <UndoDialog id={id} n={n} onClose={() => setUndoing(false)} /> : null}
    </Sections>
  );
}

/**
 * One intervention of a credential (item 185): what it did in order, the deployments it started and, where Noust can, "Leave it as it
 * was": it puts the code of each application back, shows its plan first, asks to confirm a database schema it cannot put back, and
 * says what it did that does not come back from here. It is drawn only when something can be put back.
 */
export function InterventionPage({ id, n }: { id: number; n: number }) {
  const t = useT();
  const query = useQuery(interventionQuery(id, n));
  return (
    <QueryState query={query} label={t("callerPolicy.activity.loadingLabel")} skeleton={<SkeletonCard lines={4} />}>
      {(data) => <Detail id={id} n={n} data={data} />}
    </QueryState>
  );
}
