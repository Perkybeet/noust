import { useQuery } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";

import { pendingQuery } from "../../api/queries/runtime";
import type { PendingChanges, PendingItem } from "../../api/queries/runtime";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Mono } from "../../components/ui/Mono";
import { RowList } from "../../components/ui/RowList";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useAppActions } from "../apps/useAppActions";
import { VERB_ICONS } from "../../components/ui/objectIcons";

/**
 * What an application is set to that it does not run with yet. Read with the app, so a deploy
 * or a restart that applies it (a job on the app) refreshes it with the rest. The header says it
 * as a chip on the state's line (`NotAppliedChip`), on every tab, whatever else the page says.
 */
export function usePendingChanges(domain: string) {
  return useQuery(pendingQuery(domain));
}

function areaWords(t: T, area: string): string {
  const known: Record<string, () => string> = {
    environment: () => t("runtime.pending.areas.environment"),
    limits: () => t("runtime.pending.areas.limits"),
    sandbox: () => t("runtime.pending.areas.sandbox"),
    tuning: () => t("runtime.pending.areas.tuning"),
    mounts: () => t("runtime.pending.areas.mounts"),
    build: () => t("runtime.pending.areas.build"),
    container: () => t("runtime.pending.areas.container"),
    compose: () => t("runtime.pending.areas.compose"),
    persistent_paths: () => t("runtime.pending.areas.persistent_paths"),
    health: () => t("runtime.pending.areas.health"),
    hooks: () => t("runtime.pending.areas.hooks"),
    processes: () => t("runtime.pending.areas.processes"),
  };
  return known[area]?.() ?? area;
}

function needsWords(t: T, needs: string): string {
  if (needs === "rebuild") return t("runtime.pending.needsRebuild");
  if (needs === "restart") return t("runtime.pending.needsRestart");
  return t("runtime.pending.needsNextDeploy");
}

function changeWords(t: T, change: string): string {
  if (change === "added") return t("runtime.pending.changeAdded");
  if (change === "removed") return t("runtime.pending.changeRemoved");
  return t("runtime.pending.changeChanged");
}

/** One setting: where it is, what happened to it, what it was and is, and what applying it takes. */
function Item({ item, t }: { item: PendingItem; t: T }) {
  const before = item.before ?? null;
  const after = item.after ?? null;
  return (
    <RowList.Item>
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-13">
            <span className="font-medium text-fg">{areaWords(t, item.area)}</span>
            {item.key !== "" ? (
              <Mono tone="muted" truncate>
                {item.key}
              </Mono>
            ) : null}
            <span className="text-fg-muted">{changeWords(t, item.change)}</span>
          </span>
          <span className="shrink-0 text-12 text-fg-muted">{needsWords(t, item.needs)}</span>
        </div>
        {before !== null || after !== null ? (
          <span className="flex min-w-0 flex-wrap gap-x-3 text-12 text-fg-muted">
            {before !== null ? (
              <Mono tone="muted" truncate>
                {t("runtime.pending.before", { value: before })}
              </Mono>
            ) : null}
            {after !== null ? (
              <Mono tone="muted" truncate>
                {t("runtime.pending.after", { value: after })}
              </Mono>
            ) : null}
          </span>
        ) : null}
      </div>
    </RowList.Item>
  );
}

export function PendingDialog({ domain, pending, open, onOpenChange }: { domain: string; pending: PendingChanges; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const { restart, rebuildAnyway } = useAppActions(domain);
  const needs = pending.needs ?? "next_deploy";
  const done = (): void => {
    onOpenChange(false);
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("runtime.pending.dialogTitle", { domain })}
      description={t("runtime.pending.dialogDescription", { domain })}
      footer={
        <>
          <Button onClick={done}>{t("common.words.close")}</Button>
          {needs === "rebuild" ? (
            <Button
              variant="primary"
              icon={<VERB_ICONS.rebuild aria-hidden="true" />}
              loading={rebuildAnyway.isPending}
              onClick={() => {
                rebuildAnyway.mutate(undefined, { onSuccess: done });
              }}
            >
              {t("runtime.pending.rebuildNow")}
            </Button>
          ) : needs === "restart" ? (
            <Button
              variant="primary"
              icon={<RotateCw aria-hidden="true" />}
              loading={restart.isPending}
              onClick={() => {
                restart.mutate(undefined, { onSuccess: done });
              }}
            >
              {t("runtime.pending.restartNow")}
            </Button>
          ) : null}
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {/* What applying them takes, before the list of what they are. */}
        <p className="text-13 text-pretty text-fg">
          {needs === "restart" ? t("runtime.pending.bodyRestart", { domain }) : needs === "rebuild" ? t("runtime.pending.bodyRebuild", { domain }) : t("runtime.pending.bodyNextDeploy")}
        </p>
        <RowList aria-label={t("runtime.pending.listLabel", { domain })}>
          {(pending.items ?? []).map((item) => (
            <Item key={`${item.area}/${item.key}`} item={item} t={t} />
          ))}
        </RowList>
      </div>
    </Dialog>
  );
}
