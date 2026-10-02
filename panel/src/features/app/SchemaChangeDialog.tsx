import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { appQuery, rollbackPointsQuery } from "../../api/queries/apps";
import { deploymentQuery } from "../../api/queries/deployments";
import type { Deployment } from "../../api/queries/deployments";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { backupBefore, migrationsOf, restoreCommand } from "./schemaChange";
import type { SchemaChangeRefusal } from "./schemaChange";
import { shortCommit } from "./deployments/words";

/** What one deployment changed in the database: its migrating hooks, or Prisma's automatic run. */
function Migrations({ deployment, t }: { deployment: Deployment; t: T }) {
  const migrations = migrationsOf(deployment);
  if (migrations.length === 0) return <p className="text-12 text-fg-muted">{t("appPages.schemaChange.noHookRecorded")}</p>;
  return (
    <ul className="flex flex-col gap-1">
      {migrations.map((hook, index) => (
        <li key={`${hook.run}-${String(index)}`} className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-12 text-fg-muted">
          {hook.automatic === "prisma" ? <span>{t("appPages.schemaChange.prismaAutomatic")}</span> : null}
          <Mono truncate title={hook.run}>
            {hook.run}
          </Mono>
          {hook.service ? <span>{t("appPages.schemaChange.inService", { service: hook.service })}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function DeploymentRow({ domain, id, deployment, t }: { domain: string; id: number; deployment: Deployment | undefined; t: T }) {
  const commit = shortCommit(deployment?.git_commit);
  return (
    <li className="flex min-w-0 flex-col gap-1.5 px-3 py-2.5">
      <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-13">
        <TextLink to="/apps/$domain/deployments/$id" params={{ domain, id: String(id) }} size="ui">
          {t("appPages.deployments.page.heading", { id: String(id) })}
        </TextLink>
        {commit ? <Mono tone="muted">{commit}</Mono> : null}
        {deployment?.started_at ? <RelativeTime value={deployment.started_at} className="text-12 text-fg-muted" /> : null}
      </span>
      {deployment === undefined ? <Skeleton className="h-3 w-48" /> : <Migrations deployment={deployment} t={t} />}
    </li>
  );
}

/**
 * The deployments going back passes, each with the migrations it ran, and how to put the
 * database back too: the backup taken before the first of them.
 */
function SchemaChangeDetails({ domain, refusal, t }: { domain: string; refusal: SchemaChangeRefusal; t: T }) {
  const ids = refusal.deployments;
  const deployments = useQueries({ queries: ids.map((id) => deploymentQuery(id)) });
  const app = useQuery(appQuery(domain));
  const points = useQuery(rollbackPointsQuery(domain));
  const first = deployments[0]?.data;
  const backup = points.data !== undefined ? backupBefore(points.data.items, first) : null;
  const appType = app.data?.app_type ?? null;

  let restore: ReactNode;
  if (backup !== null) {
    restore = (
      <>
        <p className="text-13 text-pretty text-fg">{t("appPages.schemaChange.restoreKnown", { id: backup.id })}</p>
        <CommandHint command={restoreCommand(backup.id, appType)} />
      </>
    );
  } else {
    restore = (
      <>
        <p className="text-13 text-pretty text-fg">{t("appPages.schemaChange.restoreUnknown")}</p>
        <CommandHint command={`noust backup list ${domain}`} />
        <CommandHint command={restoreCommand("BACKUP_ID", appType)} />
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {ids.length > 0 ? (
        <ul aria-label={t("appPages.schemaChange.listLabel")} className="flex flex-col divide-y divide-border rounded-control border border-border">
          {ids.map((id, index) => (
            <DeploymentRow key={id} domain={domain} id={id} deployment={deployments[index]?.data} t={t} />
          ))}
        </ul>
      ) : (
        <p className="text-13 text-pretty text-fg">{refusal.detail}</p>
      )}
      <div className="flex flex-col gap-2">
        <p className="title text-13 text-fg">{t("appPages.schemaChange.restoreTitle")}</p>
        {restore}
      </div>
    </div>
  );
}

export interface SchemaChangeDialogProps {
  domain: string;
  /** The refusal to confirm; the dialog is open while there is one. */
  refusal: SchemaChangeRefusal | null;
  /** Sends the same way back again, confirmed (`schema_changed_ok`). */
  onConfirm: () => Promise<unknown>;
  /** The question was answered (confirmed and done) or dismissed. */
  onClose: () => void;
}

/**
 * Asks before going back past deployments that changed the database's schema, naming each
 * one and the migrations it ran, and saying how to put the database back as well. Going back
 * anyway sends the same request with `schema_changed_ok`; a failure stays in the dialog.
 */
export function SchemaChangeDialog({ domain, refusal, onConfirm, onClose }: SchemaChangeDialogProps) {
  const t = useT();
  // The refusal stays on screen while the dialog closes, so its content does not vanish mid-fade.
  const [shown, setShown] = useState(refusal);
  if (refusal !== null && refusal !== shown) setShown(refusal);
  const confirm = useMutation({ mutationFn: onConfirm, onSuccess: onClose });
  const count = shown?.deployments.length ?? 0;
  const close = (open: boolean): void => {
    if (open || confirm.isPending) return;
    confirm.reset();
    onClose();
  };
  return (
    <Dialog
      open={refusal !== null}
      onOpenChange={close}
      size="md"
      title={t("appPages.schemaChange.title")}
      description={count > 0 ? t("appPages.schemaChange.description", { count }) : t("appPages.schemaChange.descriptionUnnamed")}
      footer={
        <>
          <Button disabled={confirm.isPending} onClick={() => close(false)}>
            {t("appPages.common.cancel")}
          </Button>
          <Button variant="primary" loading={confirm.isPending} onClick={() => confirm.mutate()}>
            {t("appPages.schemaChange.goBackAnyway")}
          </Button>
        </>
      }
    >
      {shown !== null ? (
        <div className="flex flex-col gap-4">
          <SchemaChangeDetails domain={domain} refusal={shown} t={t} />
          {confirm.isError ? <ErrorBlock live compact error={confirm.error} title={t("appPages.rollback.notStarted")} /> : null}
        </div>
      ) : null}
    </Dialog>
  );
}

/**
 * The confirmation every way back shares: `intercept` an error from going back, and when it
 * is the schema refusal the dialog opens and `retry` runs once the operator says yes.
 */
export function useSchemaChangeConfirmation(domain: string) {
  const [pending, setPending] = useState<{ refusal: SchemaChangeRefusal; retry: () => Promise<unknown> } | null>(null);
  return {
    /** True when the error was the refusal and the question is now open. */
    intercept: (refusal: SchemaChangeRefusal | null, retry: () => Promise<unknown>): boolean => {
      if (refusal === null) return false;
      setPending({ refusal, retry });
      return true;
    },
    dialog: (
      <SchemaChangeDialog
        domain={domain}
        refusal={pending?.refusal ?? null}
        onConfirm={() => (pending !== null ? pending.retry() : Promise.resolve())}
        onClose={() => setPending(null)}
      />
    ),
  };
}
