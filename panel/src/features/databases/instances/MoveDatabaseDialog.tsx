import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { enginesQuery } from "../../../api/queries/databases";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { Accepted } from "../jobs";
import { engineFamily, instanceLabel, sortInstances } from "../engines";

export interface MoveDatabaseDialogProps {
  /** The engine key the database is on, and its name. */
  engine: string;
  name: string;
  onOpenChange: (open: boolean) => void;
  /** The job that moves it, to follow. */
  onQueued: (accepted: Accepted) => void;
}

/**
 * "Move to another engine": the database is dumped, its accounts are made on the target with
 * the passwords Noust keeps, it is loaded, and each application that uses it is pointed at the
 * target behind its health gate (`POST /api/databases/moves`, sudo mode). The engine it leaves
 * is not touched, so it can be moved back; dropping it from there is a choice made here, after a
 * last dump. The plan is read first and changes nothing: what would stop the move is said before
 * the button is offered.
 */
export function MoveDatabaseDialog({ engine, name, onOpenChange, onQueued }: MoveDatabaseDialogProps) {
  const t = useT();
  const engines = useQuery(enginesQuery());
  const all = engines.data?.engines ?? [];
  const here = all.find((item) => item.name === engine);
  const family = here === undefined ? null : engineFamily(here);
  const targets = sortInstances(all.filter((item) => item.running && item.name !== engine && family !== null && engineFamily(item) === family));
  const [target, setTarget] = useState<string | null>(null);
  const [replace, setReplace] = useState(false);
  const [dropSource, setDropSource] = useState(false);
  const chosen = target ?? targets[0]?.name ?? null;
  const body = { source: engine, database: name, target: chosen ?? "", replace, drop_source: dropSource };

  const plan = useQuery({
    queryKey: ["databases", "moves", "plan", body] as const,
    queryFn: () => request("post", "/api/databases/moves/plan", { body }),
    enabled: chosen !== null,
    retry: false,
    staleTime: 0,
  });
  const move = useMutation({
    mutationFn: () => request("post", "/api/databases/moves", { body }),
    onSuccess: (accepted) => {
      onQueued(accepted);
      onOpenChange(false);
    },
  });

  const found = plan.data;
  const blocked = found === undefined || (found.blockers ?? []).length > 0;
  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      size="lg"
      title={t("databases.moveDb.title", { name })}
      description={t("databases.moveDb.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("common.confirmDialog.cancel")}</Button>
          <Button variant="primary" disabled={blocked || move.isPending} loading={move.isPending} onClick={() => move.mutate()}>
            {t("databases.moveDb.submit")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        {engines.isError ? <ErrorBlock compact error={engines.error} title={t("databases.engines.couldNotLoad")} onRetry={() => void engines.refetch()} /> : null}
        {engines.data !== undefined && targets.length === 0 ? <Notice title={t("databases.moveDb.noTarget")}>{t("databases.moveDb.noTargetBody")}</Notice> : null}
        {targets.length > 0 ? (
          <>
            <Field label={t("databases.moveDb.target")} nativeLabel={false} description={t("databases.moveDb.targetHelp")}>
              <Select value={chosen} onValueChange={setTarget} options={targets.map((item) => ({ value: item.name, label: instanceLabel(t, item) }))} />
            </Field>
            <Checkbox label={t("databases.moveDb.replace")} description={t("databases.moveDb.replaceHelp")} checked={replace} onCheckedChange={setReplace} />
            <Checkbox label={t("databases.moveDb.dropSource")} description={t("databases.moveDb.dropSourceHelp")} checked={dropSource} onCheckedChange={setDropSource} />
          </>
        ) : null}
        {plan.isPending && chosen !== null ? (
          <div aria-busy="true">
            <span className="sr-only">{t("databases.moveDb.planning")}</span>
            <Skeleton className="h-28 w-full rounded-control" />
          </div>
        ) : null}
        {plan.isError ? <ErrorBlock compact error={plan.error} title={t("databases.moveDb.planFailed")} /> : null}
        {found !== undefined ? (
          <div className="flex flex-col gap-4">
            {(found.blockers ?? []).length > 0 ? (
              <Notice tone="error" title={t("databases.moveDb.blockedTitle")} live>
                <ul className="flex list-disc flex-col gap-1 pl-5">
                  {(found.blockers ?? []).map((blocker) => (
                    <li key={blocker}>{blocker}</li>
                  ))}
                </ul>
              </Notice>
            ) : null}
            <KeyValueList
              items={[
                { label: t("databases.moveDb.from"), value: found.source, hint: found.source_version ?? undefined },
                { label: t("databases.moveDb.to"), value: found.target, hint: found.target_version ?? undefined },
                { label: t("databases.moveDb.apps"), value: (found.apps ?? []).length > 0 ? (found.apps ?? []).join(", ") : t("databases.moveDb.noApps"), mono: (found.apps ?? []).length > 0, copy: false },
                { label: t("databases.moveDb.accounts"), value: (found.accounts ?? []).length > 0 ? (found.accounts ?? []).join(", ") : t("databases.moveDb.noAccounts"), mono: (found.accounts ?? []).length > 0, copy: false },
              ]}
            />
            {(found.warnings ?? []).length > 0 ? (
              <Notice tone="warning" title={t("databases.moveDb.warningsTitle")}>
                <ul className="flex list-disc flex-col gap-1 pl-5">
                  {(found.warnings ?? []).map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </Notice>
            ) : null}
          </div>
        ) : null}
        {move.isError ? <ErrorBlock live compact error={move.error} title={t("databases.moveDb.failed")} /> : null}
        <CommandHint command={`noust db move ${name} --from ${engine} --to ${chosen ?? "ENGINE"}`} label={t("common.words.fromTerminal")} />
      </div>
    </Dialog>
  );
}
