/**
 * Installing updates: what will happen, read before anything is touched.
 *
 * The plan (`GET /api/server/updates/plan`) names the packages, which running software the
 * update disturbs, whether the console itself restarts, the exact command, and what a full
 * upgrade would remove. Removals are listed and have to be ticked, never implied; the update
 * then runs as a job in its own systemd unit, which survives the console restarting.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { request } from "../../../api/client";
import { CommandHint } from "../../../components/page/CommandHint";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useNeedsScrollFocus } from "../../../components/ui/scrollable";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { ServerErrorBlock } from "../errors";
import { updatePlanQuery } from "../queries";
import type { UpdateScope } from "../queries";
import { useServerJob } from "../serverJob";

export interface ApplyDialogProps {
  scope: UpdateScope;
  /** Updates that need a full upgrade (new dependencies, removals) to be installed. */
  keptBack: readonly string[];
  onClose: () => void;
}

/** A list of package names, verbatim, that scrolls when it is long (and then takes focus to be scrolled). */
export function Names({ names, label }: { names: readonly string[]; label: string }) {
  const ref = useRef<HTMLUListElement>(null);
  const scrolls = useNeedsScrollFocus(ref);
  return (
    <ul
      ref={ref}
      aria-label={label}
      {...(scrolls ? { tabIndex: 0 } : {})}
      className="flex max-h-40 flex-wrap gap-x-3 gap-y-1 overflow-y-auto rounded-control border border-border bg-bg-sunken p-2.5 scroll-thin"
    >
      {names.map((name) => (
        <li key={name} className="text-12">
          <Mono>{name}</Mono>
        </li>
      ))}
    </ul>
  );
}

export function ApplyDialog({ scope, keptBack, onClose }: ApplyDialogProps) {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const [full, setFull] = useState(false);
  const [allowRemovals, setAllowRemovals] = useState(false);
  const [missing, setMissing] = useState(false);
  const plan = useQuery(updatePlanQuery(scope, full));
  const removals = plan.data?.removals ?? [];

  const apply = useMutation({
    mutationFn: () => request("post", "/api/server/updates/apply", { body: { scope, full, allow_removals: allowRemovals } }),
    onSuccess: (accepted) => {
      jobs.track(accepted.job_id, "update");
      onClose();
    },
  });

  const submit = (): void => {
    if (removals.length > 0 && !allowRemovals) {
      setMissing(true);
      return;
    }
    setMissing(false);
    apply.mutate();
  };

  const security = scope === "security";
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !apply.isPending) onClose();
      }}
      size="lg"
      title={security ? t("server.updates.apply.titleSecurity") : t("server.updates.apply.titleAll")}
      description={t("server.updates.apply.description")}
      footer={
        <>
          <Button disabled={apply.isPending} onClick={onClose}>
            {t("server.updates.apply.cancel")}
          </Button>
          <Button variant="primary" loading={apply.isPending} disabled={plan.data === undefined || plan.data.packages.length === 0} onClick={submit}>
            {t("server.updates.apply.install", { count: plan.data?.packages.length ?? 0 })}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        {node !== null ? <p className="text-13 text-fg-muted">{t.rich("server.updates.apply.onServer", { server: <Mono>{node}</Mono> })}</p> : null}
        {!security && keptBack.length > 0 ? (
          <Checkbox
            label={t("server.updates.apply.fullLabel")}
            description={t("server.updates.apply.fullDescription", { count: keptBack.length })}
            checked={full}
            onCheckedChange={(checked) => {
              setFull(checked);
              setAllowRemovals(false);
            }}
          />
        ) : null}
        {plan.isError ? (
          <ServerErrorBlock compact error={plan.error} title={t("server.updates.apply.planFailed")} onRetry={() => void plan.refetch()} />
        ) : plan.data === undefined ? (
          <div aria-busy="true" className="flex flex-col gap-3">
            <span className="sr-only">{t("server.updates.apply.planLoading")}</span>
            <Skeleton className="h-4 w-60" />
            <Skeleton className="h-20 w-full rounded-control" />
          </div>
        ) : plan.data.packages.length === 0 ? (
          <Notice>{t("server.updates.apply.nothing")}</Notice>
        ) : (
          <>
            <div className="flex flex-col gap-2">
              <p className="text-13 text-fg">{t("server.updates.apply.packages", { count: plan.data.packages.length })}</p>
              <Names names={plan.data.packages.map((item) => item.name)} label={t("server.updates.apply.packagesLabel")} />
            </div>
            {plan.data.impact.length > 0 ? (
              <div className="flex flex-col gap-2">
                <p className="text-13 text-fg">{t("server.updates.apply.impact")}</p>
                <Names names={plan.data.impact} label={t("server.updates.apply.impactLabel")} />
              </div>
            ) : null}
            {plan.data.restarts_console ? <Notice tone="warning">{t("server.updates.apply.restartsConsole")}</Notice> : null}
            {removals.length > 0 ? (
              <Notice tone="error" title={t("server.updates.apply.removalsTitle", { count: removals.length })}>
                <div className="flex flex-col gap-3">
                  <Names names={removals} label={t("server.updates.apply.removalsLabel")} />
                  <Checkbox label={t("server.updates.apply.allowRemovals")} checked={allowRemovals} onCheckedChange={setAllowRemovals} />
                </div>
              </Notice>
            ) : null}
            <p className="text-13 text-fg-muted">{t("server.updates.apply.noReboot")}</p>
            <CommandHint command={plan.data.command} label={t("server.updates.apply.runs")} />
          </>
        )}
        {missing ? (
          <Notice tone="warning" live>
            {t("server.updates.apply.removalsMissing")}
          </Notice>
        ) : null}
        {apply.isError ? <ServerErrorBlock live compact error={apply.error} title={t("server.updates.apply.failed")} /> : null}
      </div>
    </Dialog>
  );
}
