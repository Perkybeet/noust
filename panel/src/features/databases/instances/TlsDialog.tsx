import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { instanceKeys, tlsStatusQuery } from "../../../api/queries/databaseInstances";
import type { DbInstance, TlsMode, TlsPlan, TlsStatus } from "../../../api/queries/databaseInstances";
import { jobKeys } from "../../../api/queries/jobs";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatDate, parseTimestamp } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";

const MODES: readonly TlsMode[] = ["off", "optional", "required"];

export interface TlsJobWords {
  running: string;
  succeeded: string;
  failed: string;
}

export interface TlsDialogProps {
  instance: DbInstance;
  onOpenChange: (open: boolean) => void;
  /** The job that does it, to follow, with the words that say what it does. */
  onQueued: (jobId: string, words: TlsJobWords) => void;
}

/** A mode in words. */
export function modeWord(t: T, mode: string): string {
  return mode === "optional" ? t("databases.tls.modes.optional") : mode === "required" ? t("databases.tls.modes.required") : t("databases.tls.modes.off");
}

/** What a mode comes to, in a few words. */
function modeHint(t: T, mode: TlsMode): string {
  return mode === "optional" ? t("databases.tls.modeHints.optional") : mode === "required" ? t("databases.tls.modeHints.required") : t("databases.tls.modeHints.off");
}

/** The certificate an instance presents, who signed it and how long it has. */
function Certificate({ status }: { status: TlsStatus }) {
  const t = useT();
  const certificate = status.certificate;
  const authority = status.ca;
  if (certificate === null || certificate === undefined) return <p className="text-13 text-fg-muted">{t("databases.tls.noCertificate")}</p>;
  const until = parseTimestamp(certificate.not_after);
  return (
    <div className="flex flex-col gap-3">
      {certificate.expiring ? (
        <Notice tone="warning" title={t("databases.tls.expiring")}>
          {t("databases.tls.daysLeft", { count: certificate.days_left })}
        </Notice>
      ) : null}
      <KeyValueList
        items={[
          { label: t("databases.tls.names"), value: (certificate.names ?? []).join(", "), copy: false },
          {
            label: t("databases.tls.validUntil"),
            value: until === null ? certificate.not_after : formatDate(until),
            copy: false,
            mono: false,
            hint: t("databases.tls.daysLeft", { count: certificate.days_left }),
          },
          { label: t("databases.tls.fingerprint"), value: certificate.fingerprint },
          ...(authority === null || authority === undefined ? [] : [{ label: t("databases.tls.authority"), value: authority.path, hint: t("databases.tls.authorityHelp") }]),
        ]}
      />
    </div>
  );
}

/** What the change would do, as rows. Nothing here changes anything. */
function Plan({ plan }: { plan: UseQueryResult<TlsPlan> }) {
  const t = useT();
  if (plan.isPending) {
    return (
      <div aria-busy="true">
        <span className="sr-only">{t("databases.tls.planning")}</span>
        <Skeleton className="h-24 w-full rounded-control" />
      </div>
    );
  }
  if (plan.isError) return <ErrorBlock compact error={plan.error} title={t("databases.tls.planFailed")} />;
  const apps = plan.data.apps ?? [];
  const blockers = plan.data.blockers ?? [];
  const warnings = plan.data.warnings ?? [];
  const names = plan.data.names ?? [];
  return (
    <div className="flex flex-col gap-3">
      {blockers.length > 0 ? (
        <Notice tone="error" title={t("databases.tls.blockedTitle")} live>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
      <KeyValueList
        items={[
          ...(names.length > 0 ? [{ label: t("databases.tls.certificateNames"), value: names.join(", "), copy: false as const }] : []),
          { label: t("databases.tls.apps"), value: apps.length > 0 ? apps.join(", ") : t("databases.tls.noApps"), mono: apps.length > 0, copy: false },
        ]}
      />
      {warnings.length > 0 ? (
        <Notice tone="warning" title={t("databases.tls.warningsTitle")}>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
    </div>
  );
}

/**
 * "TLS": how an instance serves it, the certificate it presents and the authority that signed it,
 * and the mode it should serve (`PUT /api/databases/instances/{name}/tls`, sudo mode, a job that
 * restarts the engine and puts the previous configuration back if it does not come up). The plan
 * is read first and names the applications that lose their connection; a mode the engine cannot
 * serve is listed with the reason and cannot be chosen.
 */
export function TlsDialog({ instance, onOpenChange, onQueued }: TlsDialogProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const status = useQuery(tlsStatusQuery(instance.name));
  const [mode, setMode] = useState<TlsMode | null>(null);
  const [renewing, setRenewing] = useState(false);
  const current = (status.data?.mode ?? instance.tls_mode) as TlsMode;
  const supported = status.data?.supported ?? {};
  const chosen = mode ?? current;
  const changed = chosen !== current;
  const plan = useQuery({
    queryKey: ["databases", "instances", "tls-plan", instance.name, chosen] as const,
    queryFn: () => request("post", "/api/databases/instances/{name}/tls/plan", { params: { name: instance.name }, body: { mode: chosen } }),
    enabled: changed,
    retry: false,
    staleTime: 0,
  });
  const blocked = !changed || plan.data === undefined || (plan.data.blockers ?? []).length > 0;

  const queued = (jobId: string, words: TlsJobWords): void => {
    void queryClient.invalidateQueries({ queryKey: jobKeys.active });
    void queryClient.invalidateQueries({ queryKey: instanceKeys.all });
    onQueued(jobId, words);
    onOpenChange(false);
  };
  const apply = useMutation({
    mutationFn: () => request("put", "/api/databases/instances/{name}/tls", { params: { name: instance.name }, body: { mode: chosen } }),
    onSuccess: (accepted) =>
      queued(accepted.job_id, {
        running: t("databases.tls.applying", { name: instance.name, mode: modeWord(t, chosen) }),
        succeeded: t("databases.tls.applied", { name: instance.name, mode: modeWord(t, chosen) }),
        failed: t("databases.tls.applyFailed", { name: instance.name }),
      }),
  });

  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      size="lg"
      title={t("databases.tls.title", { name: instance.name })}
      description={t("databases.tls.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("common.confirmDialog.cancel")}</Button>
          {current !== "off" ? (
            <Button disabled={apply.isPending} onClick={() => setRenewing(true)}>
              {t("databases.tls.renew")}
            </Button>
          ) : null}
          <Button variant="primary" disabled={blocked || status.data === undefined || apply.isPending} loading={apply.isPending} onClick={() => apply.mutate()}>
            {t("databases.tls.apply")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        {status.isError ? <ErrorBlock compact error={status.error} title={t("databases.tls.readFailed")} onRetry={() => void status.refetch()} /> : null}
        {status.isPending ? (
          <div aria-busy="true">
            <Skeleton className="h-32 w-full rounded-control" />
          </div>
        ) : null}
        {status.data !== undefined ? (
          <>
            <Field label={t("databases.tls.modeField")} nativeLabel={false} description={t("databases.tls.modeHelp")}>
              <Select
                value={chosen}
                onValueChange={(value) => setMode(value)}
                options={MODES.map((item) => {
                  const reason = supported[item] ?? null;
                  return {
                    value: item,
                    label: modeWord(t, item),
                    hint: reason === null ? modeHint(t, item) : t("databases.tls.notSupported", { mode: modeWord(t, item) }),
                    disabled: reason !== null,
                  };
                })}
              />
            </Field>
            {MODES.map((item) => {
              const reason = supported[item] ?? null;
              return reason === null ? null : (
                <p key={item} className="text-13 text-fg-muted">
                  <Badge mono>{modeWord(t, item)}</Badge> {reason}
                </p>
              );
            })}
            <div className="flex flex-col gap-2">
              <p className="text-13 font-medium text-fg">{t("databases.tls.certificateTitle")}</p>
              <Certificate status={status.data} />
            </div>
            {changed ? (
              <div className="flex flex-col gap-2">
                <p className="text-13 font-medium text-fg">{t("databases.tls.plan")}</p>
                <Plan plan={plan} />
              </div>
            ) : null}
          </>
        ) : null}
        {apply.isError ? <ErrorBlock live compact error={apply.error} title={t("databases.tls.failed")} /> : null}
        <CommandHint command={`noust db instance tls ${instance.name} --mode ${chosen}`} label={t("databases.common.fromTerminal")} />
      </div>
      {renewing ? (
        <ConfirmDialog
          open
          friction="simple"
          onOpenChange={(open) => (open ? undefined : setRenewing(false))}
          server={node}
          title={t("databases.tls.renewTitle", { name: instance.name })}
          description={t("databases.tls.renewDescription")}
          actionLabel={t("databases.tls.renewAction")}
          onConfirm={async () => {
            const accepted = await request("post", "/api/databases/instances/{name}/tls/renew", { params: { name: instance.name } });
            queued(accepted.job_id, {
              running: t("databases.tls.renewing", { name: instance.name }),
              succeeded: t("databases.tls.renewed", { name: instance.name }),
              failed: t("databases.tls.renewFailed", { name: instance.name }),
            });
          }}
        />
      ) : null}
    </Dialog>
  );
}
