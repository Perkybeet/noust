import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { instanceCatalogQuery, instanceKeys } from "../../../api/queries/databaseInstances";
import type { MigratePlan } from "../../../api/queries/databaseInstances";
import { jobKeys } from "../../../api/queries/jobs";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";

/** What a major-version move leaves from: an instance of Noust's, or the host's own engine. */
export interface MigrationSource {
  /** What `source` is called in the request: an instance's name, or the engine (`postgresql`). */
  name: string;
  /** What it runs, as the catalog names it (`postgresql`, `mariadb`...). */
  flavour: string;
  /** Its version, as far as it is known. */
  version: string | null;
  /** How it is said to a person. */
  label: string;
}

export interface MigrateDialogProps {
  source: MigrationSource;
  onOpenChange: (open: boolean) => void;
  /** The job that does it, to follow. */
  onQueued: (jobId: string, label: string) => void;
}

/**
 * The release a version belongs to, as the server counts it: PostgreSQL's major number alone
 * (17.2 is 17), the first two numbers of every other engine (8.4, 10.11, 7.0).
 */
function release(flavour: string, version: string): number[] {
  const parts = version.split(".").map((part) => Number.parseInt(part, 10) || 0);
  return flavour === "postgresql" ? parts.slice(0, 1) : [parts[0] ?? 0, parts[1] ?? 0];
}

/** Whether a version is a newer release than the one an engine runs; any is, when that is not known. */
export function isNewer(flavour: string, candidate: string, current: string | null): boolean {
  if (current === null) return true;
  const a = release(flavour, candidate);
  const b = release(flavour, current);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    const x = a[index] ?? 0;
    const y = b[index] ?? 0;
    if (x !== y) return x > y;
  }
  return false;
}

/** What the plan would do, as rows: nothing here changes anything. */
function Plan({ plan }: { plan: MigratePlan }) {
  const t = useT();
  const databases = (plan.databases ?? []).map((entry) => entry.database);
  const blockers = plan.blockers ?? [];
  const warnings = plan.warnings ?? [];
  const apps = plan.apps ?? [];
  return (
    <div className="flex flex-col gap-4">
      {blockers.length > 0 ? (
        <Notice tone="error" title={t("databases.migrate.blockedTitle")} live>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
      <KeyValueList
        items={[
          { label: t("databases.migrate.newInstance"), value: plan.target, hint: plan.new_instance.reference },
          { label: t("databases.migrate.listensOn"), value: `127.0.0.1:${String(plan.new_instance.host_port)}` },
          { label: t("databases.migrate.memory"), value: t("databases.migrate.memoryValue", { mb: plan.new_instance.memory_limit_mb }), mono: false, copy: false },
          { label: t("databases.migrate.databases"), value: databases.length > 0 ? databases.join(", ") : t("databases.migrate.noDatabases"), mono: databases.length > 0, copy: false },
          { label: t("databases.migrate.apps"), value: apps.length > 0 ? apps.join(", ") : t("databases.migrate.noApps"), mono: apps.length > 0, copy: false },
        ]}
      />
      {warnings.length > 0 ? (
        <Notice tone="warning" title={t("databases.migrate.warningsTitle")}>
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
 * "Move to a newer version": a new instance of the newer version is made, every database of
 * this engine is dumped and loaded into it and the applications that use them are pointed at
 * it behind their health gate (`POST /api/databases/instances/migrate`, sudo mode). The engine
 * it leaves is not touched, so the move can be rolled back. The plan is read first and
 * changes nothing; whatever would stop the move is said before the button is offered.
 */
export function MigrateDialog({ source, onOpenChange, onQueued }: MigrateDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const catalog = useQuery(instanceCatalogQuery());
  const offered = (catalog.data?.engines.find((entry) => entry.flavour === source.flavour)?.versions ?? []).filter((entry) => isNewer(source.flavour, entry.version, source.version));
  const [version, setVersion] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [memory, setMemory] = useState("");
  const chosen = version ?? offered.at(-1)?.version ?? null;
  const memoryMb = memory.trim() === "" ? undefined : Number(memory.trim());
  const memoryValid = memoryMb === undefined || (Number.isInteger(memoryMb) && memoryMb >= 1);
  const body = { source: source.name, to_version: chosen ?? "", ...(name.trim() === "" ? {} : { name: name.trim() }), ...(memoryMb === undefined || !memoryValid ? {} : { memory_mb: memoryMb }) };

  const plan = useQuery({
    queryKey: ["databases", "instances", "migrate-plan", body] as const,
    queryFn: () => request("post", "/api/databases/instances/migrate/plan", { body }),
    enabled: chosen !== null && memoryValid,
    retry: false,
    staleTime: 0,
  });
  const go = useMutation({
    mutationFn: () => request("post", "/api/databases/instances/migrate", { body }),
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      void queryClient.invalidateQueries({ queryKey: instanceKeys.all });
      onQueued(accepted.job_id, source.label);
      onOpenChange(false);
    },
  });

  const blocked = plan.data === undefined || (plan.data.blockers ?? []).length > 0;
  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      size="lg"
      title={t("databases.migrate.title", { engine: source.label })}
      description={t("databases.migrate.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("common.confirmDialog.cancel")}</Button>
          <Button variant="primary" disabled={blocked || go.isPending} loading={go.isPending} onClick={() => go.mutate()}>
            {t("databases.migrate.submit")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        {catalog.isError ? <ErrorBlock compact error={catalog.error} title={t("databases.newInstance.catalogFailed")} onRetry={() => void catalog.refetch()} /> : null}
        {catalog.data !== undefined && offered.length === 0 ? <Notice title={t("databases.migrate.nothingNewer", { engine: source.label })}>{t("databases.migrate.nothingNewerBody")}</Notice> : null}
        {offered.length > 0 ? (
          <>
            <Field label={t("databases.migrate.version")} nativeLabel={false} description={t("databases.migrate.versionHelp", { current: source.version ?? "?" })}>
              <Select value={chosen} onValueChange={setVersion} options={offered.map((entry) => ({ value: entry.version, label: entry.version }))} />
            </Field>
            <div className="flex flex-wrap gap-4">
              <Field label={t("databases.migrate.name")} optional description={t("databases.migrate.nameHelp")} className="w-56">
                <Input mono value={name} onValueChange={setName} autoComplete="off" autoCapitalize="off" spellCheck={false} />
              </Field>
              <Field label={t("databases.migrate.memoryField")} optional description={t("databases.migrate.memoryHelp")} error={memoryValid ? undefined : t("databases.newInstance.memoryInvalid")} className="w-44">
                <Input mono inputMode="numeric" value={memory} onValueChange={setMemory} suffix="MB" />
              </Field>
            </div>
          </>
        ) : null}
        {plan.isPending && chosen !== null && memoryValid ? (
          <div aria-busy="true">
            <span className="sr-only">{t("databases.migrate.planning")}</span>
            <Skeleton className="h-32 w-full rounded-control" />
          </div>
        ) : null}
        {plan.isError ? <ErrorBlock compact error={plan.error} title={t("databases.migrate.planFailed")} /> : null}
        {plan.data !== undefined ? <Plan plan={plan.data} /> : null}
        {go.isError ? <ErrorBlock live compact error={go.error} title={t("databases.migrate.failed")} /> : null}
        <CommandHint command={`noust db instance migrate ${source.name} --to-version ${chosen ?? "N"}`} label={t("common.words.fromTerminal")} />
      </div>
    </Dialog>
  );
}
